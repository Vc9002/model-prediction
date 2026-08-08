"""Tests for the shared MLB shadow pipeline (mlb_shadow_pipeline.py),
extracted from scripts/mlb_shadow_run.py so the shared CLI's MLBAdapter
can run the same real predict/market/decide logic without reimplementing
it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from model_prediction.rebuild import mlb_shadow_pipeline as pipeline
from model_prediction.rebuild.storage import NormalizedStore, provenance_row, utc_now


def _write_scoreboard(data_root, event_id: str, event_start_utc: str, home: str, away: str, status: str) -> None:
    norm = NormalizedStore(f"{data_root}/normalized")
    row = {
        **provenance_row(source="espn_public", source_record_id=event_id, source_version="v1",
                          observed_at_utc=utc_now().isoformat(), effective_at_utc=event_start_utc,
                          event_start_utc=event_start_utc),
        "event_id": event_id, "home_team": home, "away_team": away,
        "home_score": 0, "away_score": 0, "status": status, "venue": "",
    }
    norm.write("mlb", "scoreboard", pl.DataFrame([row]), primary_key=["event_id"])


class TestLoadState:
    def test_no_scoreboard_ever_collected_returns_none_not_a_crash(self, tmp_path):
        assert pipeline.load_state(str(tmp_path), "2026-08-06") is None

    def test_no_scheduled_games_for_date_returns_none(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-05T22:10:00+00:00", "A", "B", "STATUS_FINAL")
        assert pipeline.load_state(str(tmp_path), "2026-08-06") is None

    def test_real_scheduled_game_produces_real_state(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "2026-08-06")
        assert state is not None
        assert state.tonight.height == 1
        assert "401" in state.decision_times


class TestBuildForecastCalledExactlyOncePerGame:
    """Real bug found and fixed via live diff against scripts/
    mlb_shadow_run.py's proven output: JointScoreDistribution holds one
    stateful np.random.default_rng(seed), consumed by both predict_row()
    and probability_for_market()'s Monte Carlo simulation. Calling
    build_forecast() twice per game (once market-blind in predict_stage,
    once with real market lines in decide_stage) drew from that generator
    twice, producing a different win probability than the proven script's
    single call for the identical row/model (observed live: 0.49 vs
    0.49255, despite expected_home_score matching exactly since that
    value is deterministic). Fixed: predict_stage() only resolves and
    caches the real feature row; the one real build_forecast() call
    happens in decide_stage()."""

    def test_predict_stage_does_not_call_build_forecast(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers", "STATUS_SCHEDULED")
        # 30 real completed-game rows so predict_stage's real
        # features.height < 30 gate passes naturally.
        for i in range(30):
            _write_scoreboard(tmp_path, f"hist{i}", f"2026-07-{(i % 28) + 1:02d}T22:10:00+00:00",
                               "A", "B", "STATUS_FINAL")
        state = pipeline.load_state(str(tmp_path), "2026-08-06")
        assert state is not None

        with patch(
            "model_prediction.rebuild.mlb_features.build_game_feature_row",
            return_value={"game_date": "2026-07-01", "event_id": "hist"},
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.train_through",
            return_value=(MagicMock(), MagicMock(), 30),
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.point_in_time_probable_starters",
            return_value={"401": {"home_starter": "A", "away_starter": "B"}},
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.build_live_game_feature_row",
            return_value={"event_id": "401"},
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.build_forecast",
            side_effect=AssertionError("build_forecast must not be called from predict_stage"),
        ):
            result = pipeline.predict_stage(state, str(tmp_path))

        assert result["status"] == "ok"
        assert result["games_predicted"] == 1
        assert "401" in state.rows_by_event

    def test_decide_stage_calls_build_forecast_exactly_once_per_game(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "2026-08-06")
        assert state is not None
        state.model = MagicMock()
        state.bootstrap = MagicMock()
        state.rows_by_event = {"401": {"event_id": "401"}}

        fake_forecast = MagicMock()
        fake_forecast.model_artifact_hash = "hash1"

        with patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.build_forecast", return_value=fake_forecast,
        ) as mock_build_forecast, patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.evaluate_game", return_value=[],
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.real_market_snapshot_hash", return_value="mkthash",
        ):
            pipeline.decide_stage(state)

        assert mock_build_forecast.call_count == 1

    def test_decide_stage_without_predict_first_raises(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers", "STATUS_SCHEDULED")
        state = pipeline.load_state(str(tmp_path), "2026-08-06")
        assert state is not None
        with pytest.raises(ValueError, match="predict_stage"):
            pipeline.decide_stage(state)


class TestModelArtifactLineage:
    """Real gap closed: predict_stage() trains a real model but never
    recorded which real artifact this run's predictions are bound to --
    record_model_artifact() (added earlier this session) had no real
    caller wiring it into the actual pipeline."""

    def test_predict_stage_records_a_real_model_artifact_when_given_a_ledger(self, tmp_path):
        from model_prediction.rebuild.shadow_ledger import ShadowLedger

        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers", "STATUS_SCHEDULED")
        for i in range(30):
            _write_scoreboard(tmp_path, f"hist{i}", f"2026-07-{(i % 28) + 1:02d}T22:10:00+00:00",
                               "A", "B", "STATUS_FINAL")
        state = pipeline.load_state(str(tmp_path), "2026-08-06")
        assert state is not None

        fake_model = MagicMock()
        fake_model.to_artifact.return_value = {"model_id": "mlb-two-head-v1", "artifact_hash": "real_test_hash_abc"}

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        run_id = ledger.record_run("mlb", run_type="test")

        with patch(
            "model_prediction.rebuild.mlb_features.build_game_feature_row",
            return_value={"game_date": "2026-07-01", "event_id": "hist"},
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.train_through",
            return_value=(fake_model, MagicMock(), 30),
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.point_in_time_probable_starters", return_value={},
        ):
            pipeline.predict_stage(state, str(tmp_path), ledger=ledger, run_id=run_id)

        row = ledger.conn.execute(
            "SELECT run_id, artifact_hash FROM model_artifacts WHERE artifact_hash='real_test_hash_abc'"
        ).fetchone()
        ledger.close()
        assert row is not None
        assert row["run_id"] == run_id


class TestPlayerIdentityLineage:
    """Real gap closed: probable starters were resolved to a real MLBAM id
    (lookup_pitcher_id) but that id was only ever used inline for Statcast
    feature lookups, never registered as a canonical player entity --
    identity.resolve_mlbam_player_id() (added this session) had no real
    caller wiring it into the actual pipeline, mirroring the
    TestModelArtifactLineage gap fixed earlier."""

    def test_predict_stage_registers_real_canonical_player_identity_for_probable_starters(self, tmp_path):
        from model_prediction.rebuild.identity import IdentityRegistry
        from model_prediction.rebuild.metadata import MetadataDB

        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers", "STATUS_SCHEDULED")
        for i in range(30):
            _write_scoreboard(tmp_path, f"hist{i}", f"2026-07-{(i % 28) + 1:02d}T22:10:00+00:00",
                               "A", "B", "STATUS_FINAL")
        state = pipeline.load_state(str(tmp_path), "2026-08-06")
        assert state is not None

        registries_seen = []

        def fake_build_live_row(espn_game, home_name, away_name, pitches, starters, data_root,
                                 identity_registry=None, decision_time_utc=None):
            registries_seen.append(identity_registry)
            if identity_registry is not None:
                from model_prediction.rebuild.identity import resolve_mlbam_player_id
                resolve_mlbam_player_id(identity_registry, "mlb", home_name, 665742, "2026-08-06T21:10:00+00:00")
                resolve_mlbam_player_id(identity_registry, "mlb", away_name, 592450, "2026-08-06T21:10:00+00:00")
            return {"event_id": "401"}

        with patch(
            "model_prediction.rebuild.mlb_features.build_game_feature_row",
            return_value={"game_date": "2026-07-01", "event_id": "hist"},
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.train_through",
            return_value=(MagicMock(), MagicMock(), 30),
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.point_in_time_probable_starters",
            return_value={"401": {"home_starter": "Juan Soto", "away_starter": "Aaron Judge"}},
        ), patch(
            "model_prediction.rebuild.mlb_shadow_pipeline.build_live_game_feature_row",
            side_effect=fake_build_live_row,
        ):
            pipeline.predict_stage(state, str(tmp_path))

        assert len(registries_seen) == 1
        assert isinstance(registries_seen[0], IdentityRegistry)

        # And the real registration is independently resolvable via a
        # fresh registry against the same real metadata.db, not just an
        # in-memory artifact of this one call.
        fresh_registry = IdentityRegistry(MetadataDB(f"{tmp_path}/metadata.db"))
        resolved = fresh_registry.resolve("statcast_mlbam", "665742")
        assert resolved is not None
        assert resolved.canonical_name == "Juan Soto"
        assert resolved.entity_type == "player"
        assert resolved.sport == "mlb"


class TestResumeState:
    """MLB-4 (multi-sport execution spec): real cross-process resume --
    save_resume_state()/load_resume_state() must persist and reload the
    real trained model plus resolved feature rows, producing identical
    real predictions to the original in-memory state, not just "loads
    without error." """

    def _fit_real_model_and_state(self, tmp_path, target_date="2026-08-06"):
        import numpy as np

        from model_prediction.rebuild.mlb_shadow_pipeline import (
            DIFFERENTIAL_FEATURES,
            INTENSITY_FEATURES,
            MLBRunState,
            train_through,
        )

        _write_scoreboard(tmp_path, "401", f"{target_date}T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers", "STATUS_SCHEDULED")
        rng = np.random.default_rng(0)
        n = 40
        data = {f: rng.uniform(1, 5, n) for f in INTENSITY_FEATURES}
        data.update({f: rng.uniform(-2, 2, n) for f in DIFFERENTIAL_FEATURES})
        data["total_runs"] = sum(data[f] for f in INTENSITY_FEATURES) / len(INTENSITY_FEATURES) + rng.normal(0, 0.3, n)
        data["home_margin"] = sum(data[f] for f in DIFFERENTIAL_FEATURES) / len(DIFFERENTIAL_FEATURES)
        data["game_date"] = [f"2026-07-{(i % 28) + 1:02d}" for i in range(n)]
        features = pl.DataFrame(data)

        model, bootstrap, train_n = train_through(features, target_date)
        row = {f: float(rng.uniform(1, 5)) for f in INTENSITY_FEATURES}
        row.update({f: float(rng.uniform(-2, 2)) for f in DIFFERENTIAL_FEATURES})

        state = MLBRunState(
            target_date=target_date, tonight=pl.DataFrame({"event_id": ["401"]}),
            model=model, bootstrap=bootstrap, train_n=train_n,
            rows_by_event={"401": row}, skipped={"402": "no_probable_starters_available"},
        )
        return state, row

    def test_save_then_load_produces_identical_real_predictions(self, tmp_path):
        state, row = self._fit_real_model_and_state(tmp_path)
        pred_before = state.model.predict_row("401", row)

        pipeline.save_resume_state(state, str(tmp_path), "run123")
        loaded = pipeline.load_resume_state(str(tmp_path), "run123", state.target_date)

        assert loaded is not None
        pred_after = loaded.model.predict_row("401", loaded.rows_by_event["401"])
        assert pred_after.home_expected_runs == pred_before.home_expected_runs
        assert pred_after.away_expected_runs == pred_before.away_expected_runs
        assert pred_after.home_win_prob == pred_before.home_win_prob

    def test_loaded_state_preserves_train_n_and_skipped(self, tmp_path):
        state, _ = self._fit_real_model_and_state(tmp_path)
        pipeline.save_resume_state(state, str(tmp_path), "run123")
        loaded = pipeline.load_resume_state(str(tmp_path), "run123", state.target_date)

        assert loaded is not None
        assert loaded.train_n == state.train_n
        assert loaded.skipped == {"402": "no_probable_starters_available"}

    def test_loaded_state_has_no_bootstrap_real_disclosed_gap(self, tmp_path):
        state, _ = self._fit_real_model_and_state(tmp_path)
        assert state.bootstrap is not None  # the original state DID fit one
        pipeline.save_resume_state(state, str(tmp_path), "run123")
        loaded = pipeline.load_resume_state(str(tmp_path), "run123", state.target_date)

        assert loaded is not None
        assert loaded.bootstrap is None

    def test_no_saved_resume_state_returns_none(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers", "STATUS_SCHEDULED")
        assert pipeline.load_resume_state(str(tmp_path), "nonexistent_run", "2026-08-06") is None

    def test_mismatched_date_returns_none_not_stale_state(self, tmp_path):
        state, _ = self._fit_real_model_and_state(tmp_path, target_date="2026-08-06")
        pipeline.save_resume_state(state, str(tmp_path), "run123")

        assert pipeline.load_resume_state(str(tmp_path), "run123", "2026-08-07") is None

    def test_saving_before_training_fails_loudly(self, tmp_path):
        from model_prediction.rebuild.mlb_shadow_pipeline import MLBRunState

        state = MLBRunState(target_date="2026-08-06", tonight=pl.DataFrame({"event_id": ["401"]}))
        with pytest.raises(ValueError):
            pipeline.save_resume_state(state, str(tmp_path), "run123")
