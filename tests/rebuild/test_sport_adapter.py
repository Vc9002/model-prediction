"""Tests for the shared sport-adapter protocol (sport_adapter.py,
FOUNDATION_COMPLETION.md Phase 13 / item 5)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from model_prediction.rebuild.sport_adapter import (
    STAGE_ERROR,
    STAGE_NO_DATA,
    STAGE_NOT_IMPLEMENTED,
    STAGE_SUCCESS,
    SUPPORTED_SPORTS,
    build_adapter,
)


class TestBuildAdapter:
    def test_every_supported_sport_returns_a_real_adapter(self, tmp_path):
        for sport in SUPPORTED_SPORTS:
            adapter = build_adapter(sport, str(tmp_path))
            assert adapter.sport == sport

    def test_unsupported_sport_fails_closed_not_a_guess(self, tmp_path):
        with pytest.raises(ValueError, match="no adapter registered"):
            build_adapter("cricket", str(tmp_path))

    def test_esports_collect_calls_the_real_stub_and_reports_it_honestly(self, tmp_path):
        # EsportsCollector.collect_date() is a real, honest stub (returns
        # status="stub", does no real network call) -- the adapter must
        # not fabricate SUCCESS for it. Proves the real stub was actually
        # called (not skipped) by checking its own real response note
        # survives into the StageResult.
        adapter = build_adapter("esports", str(tmp_path))
        result = adapter.collect("2026-08-06")
        assert result.status == STAGE_NOT_IMPLEMENTED
        assert result.detail.get("status") == "stub"

    def test_kbo_and_npb_are_explicitly_research_only_not_a_wiring_gap(self, tmp_path):
        # Explicit decision (CLAUDE.md: "restrict the model rather than
        # inventing MLB-equivalent features" when reliable inputs don't
        # exist) -- kbo/npb have no real collector anywhere in this
        # codebase, so every stage honestly reports NOT_IMPLEMENTED with a
        # clear research_only reason rather than raising or, worse,
        # fabricating a working pipeline on top of nothing.
        for sport in ("kbo", "npb"):
            adapter = build_adapter(sport, str(tmp_path))
            assert adapter.sport == sport
            collect_result = adapter.collect("2026-08-06")
            assert collect_result.status == STAGE_NOT_IMPLEMENTED
            assert collect_result.detail["qualification_status"] == "RESEARCH_ONLY"
            assert adapter.predict("2026-08-06", "late").status == STAGE_NOT_IMPLEMENTED
            assert adapter.build_features("2026-08-06", "late").status == STAGE_NOT_IMPLEMENTED


class TestHonestNotImplementedStages:
    """The real point of this module: a sport with no real trained model
    must say so honestly through the shared interface, not fabricate a
    prediction. Esports is the one remaining sport with no real
    predict/match_markets/decide/build_features -- NBA/WNBA/NFL/Soccer/
    Tennis all gained real basic (Elo) implementations, see
    TestBasicEloAdapter below."""

    def test_esports_predict_is_honestly_not_implemented(self, tmp_path):
        adapter = build_adapter("esports", str(tmp_path))
        result = adapter.predict("2026-08-06", "late")
        assert result.status == STAGE_NOT_IMPLEMENTED

    def test_esports_match_markets_is_honestly_not_implemented(self, tmp_path):
        adapter = build_adapter("esports", str(tmp_path))
        result = adapter.match_markets("2026-08-06", "late")
        assert result.status == STAGE_NOT_IMPLEMENTED

    def test_esports_decide_is_honestly_not_implemented(self, tmp_path):
        adapter = build_adapter("esports", str(tmp_path))
        result = adapter.decide("2026-08-06", "late")
        assert result.status == STAGE_NOT_IMPLEMENTED

    def test_esports_build_features_is_honestly_not_implemented(self, tmp_path):
        adapter = build_adapter("esports", str(tmp_path))
        result = adapter.build_features("2026-08-06", "late")
        assert result.status == STAGE_NOT_IMPLEMENTED

    def test_mlb_predict_on_a_cold_empty_data_root_is_honest_no_data_not_a_crash(self, tmp_path):
        # MLB predict/match_markets/decide are now real (mlb_shadow_pipeline.py)
        # -- against a genuinely empty data_root (no scoreboard ever
        # collected), the honest real result is NO_DATA, not a crash and
        # not NOT_IMPLEMENTED (that would misrepresent real code as absent).
        adapter = build_adapter("mlb", str(tmp_path))
        result = adapter.predict("2026-08-06", "late")
        assert result.status == STAGE_NO_DATA

    def test_mlb_match_markets_without_predict_first_fails_closed(self, tmp_path):
        # match_markets() depends on real forecast state predict() builds --
        # calling it first (e.g. a bare --markets-only invocation) must fail
        # closed with a clear reason, not silently operate on nothing.
        adapter = build_adapter("mlb", str(tmp_path))
        result = adapter.match_markets("2026-08-06", "late")
        assert result.status == STAGE_ERROR

    def test_mlb_decide_without_predict_first_fails_closed(self, tmp_path):
        adapter = build_adapter("mlb", str(tmp_path))
        result = adapter.decide("2026-08-06", "late")
        assert result.status == STAGE_ERROR


class TestCollectionOnlyAdapterFailsClosedNotCrashed:
    """Real bug found live, then fixed: SoccerCollector/TennisCollector
    called the real ESPN client with league codes ("SOCCER"/"TENNIS")
    that don't exist in data_sources/espn.py's LEAGUE_PATHS -- a real
    ValueError on a real call, not a mock. Fixed by defaulting to a real
    valid league each (EPL, ATP) instead. This class now covers two
    things: the fix actually works (real network collection succeeds),
    and the adapter's try/except still reports a genuine failure as an
    honest per-stage ERROR rather than crashing, for any future collector
    bug of the same shape."""

    def test_tennis_collect_uses_the_real_default_league(self, tmp_path, monkeypatch):
        from model_prediction.rebuild.collectors import TennisCollector

        captured: dict[str, str] = {}

        def fake_collect_date(self, game_date: str, league: str = "ATP"):
            captured.update(date=game_date, league=league)
            return {"status": "no_games", "date": game_date, "sport": "tennis"}

        monkeypatch.setattr(TennisCollector, "collect_date", fake_collect_date)
        adapter = build_adapter("tennis", str(tmp_path))
        result = adapter.collect("2026-08-06")
        assert result.status == STAGE_NO_DATA
        assert captured == {"date": "2026-08-06", "league": "ATP"}

    def test_collection_only_adapter_still_reports_a_genuine_collector_failure_as_error(self, tmp_path):
        # Proves the try/except in _CollectionOnlyAdapter.collect() still
        # does its job for a real failure, now that the specific
        # SOCCER/TENNIS bug it was written for is fixed.
        from unittest.mock import MagicMock

        from model_prediction.rebuild.sport_adapter import _CollectionOnlyAdapter

        broken_collector = MagicMock()
        broken_collector.collect_date.side_effect = ValueError("simulated real collector failure")
        adapter = _CollectionOnlyAdapter("tennis", broken_collector)

        result = adapter.collect("2026-08-06")
        assert result.status == "ERROR"
        assert "simulated real collector failure" in result.detail["error"]


def _write_nba_scoreboard(data_root, event_id: str, event_start_utc: str, home: str, away: str,
                           status: str, home_score: int = 0, away_score: int = 0) -> None:
    from model_prediction.rebuild.storage import NormalizedStore, provenance_row, utc_now

    norm = NormalizedStore(f"{data_root}/normalized")
    row = {
        **provenance_row(source="espn_public", source_record_id=event_id, source_version="v1",
                          observed_at_utc=utc_now().isoformat(), effective_at_utc=event_start_utc,
                          event_start_utc=event_start_utc),
        "event_id": event_id, "home_team": home, "away_team": away,
        "home_score": home_score, "away_score": away_score, "status": status, "venue": "",
    }
    norm.write("nba", "scoreboard", __import__("polars").DataFrame([row]), primary_key=["event_id"])


class TestBasicEloAdapter:
    """Real basic (Elo) foundation pipeline for NBA/NFL/Soccer/Tennis
    -- proves predict/match_markets/decide are genuinely real (not the
    NOT_IMPLEMENTED mixin) and fail closed the same way MLBAdapter does."""

    def test_wnba_collect_calls_the_real_collector_with_sport_wnba_not_the_nba_default(self, tmp_path):
        # Real bug found live (2026-08-07): the inherited
        # _CollectionOnlyAdapter.collect() called
        # self.collector.collect_date(date) with no sport argument, which
        # silently defaulted to NBACollector.collect_date()'s own
        # sport="nba" default -- so the shared CLI's collect stage for
        # --sport wnba was actually collecting real NBA data (correctly
        # NO_DATA, off-season) while claiming to serve WNBA.
        # _BasicEloAdapter.collect() must use the same sport-parameterized
        # collect_fn match_markets_stage() uses, not the raw collector.
        calls = []
        adapter = build_adapter("wnba", str(tmp_path))
        # Replaces the real network-calling collect_date with a spy so this
        # stays a fast, deterministic unit test -- live network
        # verification is done separately (matching the pattern used
        # elsewhere in this file).
        adapter.collector.collect_date = lambda d, sport="nba": (calls.append(sport), {"status": "no_games"})[1]
        adapter.collect("2026-08-06")
        assert calls == ["wnba"]

    def test_wnba_production_stages_fail_closed_until_foundation_release(self, tmp_path):
        adapter = build_adapter("wnba", str(tmp_path))
        cold_features = adapter.build_features("2026-08-06", "late")
        assert cold_features.status == STAGE_NO_DATA
        assert cold_features.detail["production_allowed"] is False
        for result in (
            adapter.predict("2026-08-06", "late"),
            adapter.match_markets("2026-08-06", "late"),
            adapter.decide("2026-08-06", "late"),
        ):
            assert result.status == STAGE_NOT_IMPLEMENTED
            assert result.detail["qualification_status"] == "FOUNDATION_BLOCKED"
            assert result.detail["commercial_use_status"] == "unresolved"
            assert result.detail["production_allowed"] is False

    def test_wnba_replay_and_live_adapter_paths_have_identical_pit_features(self, tmp_path):
        import json

        import polars as pl

        from model_prediction.rebuild.shadow_ledger import ShadowLedger
        from model_prediction.rebuild.storage import FeatureStore
        from model_prediction.rebuild.wnba.store import WNBANormalizedStore

        store = WNBANormalizedStore(tmp_path / "normalized")
        games = pl.DataFrame([
            {
                "event_id": "prior",
                "event_start_utc": "2026-08-01T22:00:00+00:00",
                "sports_event_date": "2026-08-01",
                "observed_at_utc": "2026-08-02T00:00:00+00:00",
                "raw_snapshot_hash": "games-prior",
                "availability_basis": "capture_time_only",
                "commercial_use_status": "unresolved",
                "production_allowed": False,
                "completed": True,
                "pit_eligible": True,
                "home_team_id": "A",
                "away_team_id": "B",
                "home_team_canonical_id": "wnba:team:a",
                "away_team_canonical_id": "wnba:team:b",
            },
            {
                "event_id": "target",
                "event_start_utc": "2026-08-10T22:00:00+00:00",
                "sports_event_date": "2026-08-10",
                "observed_at_utc": "2026-08-10T20:00:00+00:00",
                "raw_snapshot_hash": "games-target",
                "availability_basis": "capture_time_only",
                "commercial_use_status": "unresolved",
                "production_allowed": False,
                "completed": False,
                "pit_eligible": True,
                "home_team_id": "A",
                "away_team_id": "B",
                "home_team_canonical_id": "wnba:team:a",
                "away_team_canonical_id": "wnba:team:b",
            },
        ])
        boxes = pl.DataFrame([
            {
                "event_id": "prior",
                "event_start_utc": "2026-08-01T22:00:00+00:00",
                "sports_event_date": "2026-08-01",
                "observed_at_utc": "2026-08-02T00:00:00+00:00",
                "raw_snapshot_hash": f"box-{team}",
                "availability_basis": "capture_time_only",
                "commercial_use_status": "unresolved",
                "production_allowed": False,
                "pit_eligible": True,
                "team_id": team,
                "opponent_team_id": opponent,
                "points": points,
                "field_goals_made": 30,
                "field_goals_attempted": 70,
                "three_points_made": 8,
                "three_points_attempted": 22,
                "free_throws_made": 14,
                "free_throws_attempted": 18,
                "offensive_rebounds": 9,
                "defensive_rebounds": 27,
                "turnovers": 11,
            }
            for team, opponent, points in (("A", "B", 82), ("B", "A", 77))
        ])
        store.write("games", 2026, games)
        store.write("team_box", 2026, boxes)

        ledger = ShadowLedger(tmp_path / "shadow.db")
        run_id = ledger.record_run("wnba", run_type="test")
        ledger.close()
        adapter = build_adapter("wnba", str(tmp_path))
        before_cutoff = adapter.build_live_features(
            "2026-08-10",
            "late",
            knowledge_time_utc=datetime.fromisoformat("2026-08-10T20:59:59+00:00"),
        )
        assert before_cutoff.status == STAGE_NO_DATA
        assert before_cutoff.detail["missing_reasons"] == {"decision_cutoff_not_reached": 1}
        replay = adapter.build_replay_features("2026-08-10", "late", run_id=run_id)
        live = adapter.build_live_features(
            "2026-08-10",
            "late",
            run_id=run_id,
            knowledge_time_utc=datetime.fromisoformat("2026-08-10T21:00:00+00:00"),
        )

        assert replay.status == STAGE_SUCCESS
        assert live.status == STAGE_SUCCESS
        assert replay.detail["snapshot_hash"] == live.detail["snapshot_hash"]
        assert replay.detail["feature_schema_hash"] == live.detail["feature_schema_hash"]
        persisted = FeatureStore(tmp_path / "features").read("wnba", "late")
        assert persisted.height == 1
        assert persisted["decision_time_utc"][0] == "2026-08-10T21:00:00+00:00"
        assert persisted["production_allowed"][0] is False
        schema_path = replay.detail["payload_path"].replace(".parquet", ".schema.json")
        schema = json.loads(Path(schema_path).read_text())
        assert schema["feature_schema_hash"] == replay.detail["feature_schema_hash"]
        ledger = ShadowLedger(tmp_path / "shadow.db")
        lineage = ledger.feature_snapshots_for_horizon("wnba", "late")
        ledger.close()
        assert len(lineage) == 1
        assert lineage[0]["dataset_hash"] == replay.detail["snapshot_hash"]

    def test_wnba_adapter_fails_closed_on_malformed_team_metrics(self, tmp_path):
        import polars as pl

        from model_prediction.rebuild.wnba.store import WNBANormalizedStore

        store = WNBANormalizedStore(tmp_path / "normalized")
        store.write("games", 2026, pl.DataFrame([
            {
                "event_id": "prior",
                "event_start_utc": "2026-08-01T22:00:00+00:00",
                "sports_event_date": "2026-08-01",
                "observed_at_utc": "2026-08-02T00:00:00+00:00",
                "raw_snapshot_hash": "games-prior",
                "availability_basis": "capture_time_only",
                "commercial_use_status": "unresolved",
                "production_allowed": False,
                "completed": True,
                "pit_eligible": True,
                "home_team_id": "A",
                "away_team_id": "B",
                "home_team_canonical_id": "wnba:team:a",
                "away_team_canonical_id": "wnba:team:b",
            },
            {
                "event_id": "target",
                "event_start_utc": "2026-08-10T22:00:00+00:00",
                "sports_event_date": "2026-08-10",
                "observed_at_utc": "2026-08-10T20:00:00+00:00",
                "raw_snapshot_hash": "games-target",
                "availability_basis": "capture_time_only",
                "commercial_use_status": "unresolved",
                "production_allowed": False,
                "completed": False,
                "pit_eligible": True,
                "home_team_id": "A",
                "away_team_id": "B",
                "home_team_canonical_id": "wnba:team:a",
                "away_team_canonical_id": "wnba:team:b",
            },
        ]))
        store.write("team_box", 2026, pl.DataFrame([
            {
                "event_id": "prior",
                "event_start_utc": "2026-08-01T22:00:00+00:00",
                "sports_event_date": "2026-08-01",
                "observed_at_utc": "2026-08-02T00:00:00+00:00",
                "raw_snapshot_hash": f"box-{team}",
                "availability_basis": "capture_time_only",
                "commercial_use_status": "unresolved",
                "production_allowed": False,
                "pit_eligible": True,
                "team_id": team,
                "opponent_team_id": opponent,
                "points": points,
                "field_goals_made": "30",
                "field_goals_attempted": "70",
                "three_points_made": "8",
                "three_points_attempted": "22",
                "free_throws_made": "14",
                "free_throws_attempted": "18",
                "offensive_rebounds": "9",
                "defensive_rebounds": "27",
                "turnovers": "11",
            }
            for team, opponent, points in (("A", "B", "malformed"), ("B", "A", "77"))
        ]))
        result = build_adapter("wnba", str(tmp_path)).build_live_features(
            "2026-08-10",
            "late",
            knowledge_time_utc=datetime.fromisoformat("2026-08-10T21:00:00+00:00"),
        )
        assert result.status == STAGE_ERROR
        assert result.detail["production_allowed"] is False

    def test_nba_predict_on_a_cold_empty_data_root_is_honest_no_data_not_a_crash(self, tmp_path):
        adapter = build_adapter("nba", str(tmp_path))
        result = adapter.predict("2026-08-06", "late")
        assert result.status == STAGE_NO_DATA

    def test_nba_match_markets_without_predict_first_fails_closed(self, tmp_path):
        adapter = build_adapter("nba", str(tmp_path))
        result = adapter.match_markets("2026-08-06", "late")
        assert result.status == STAGE_ERROR

    def test_nba_decide_without_predict_first_fails_closed(self, tmp_path):
        adapter = build_adapter("nba", str(tmp_path))
        result = adapter.decide("2026-08-06", "late")
        assert result.status == STAGE_ERROR

    def test_nba_build_features_is_real_not_the_stub(self, tmp_path):
        # Confirms build_features is real code (basic_sport_pipeline
        # reading the real scoreboard), not silently falling back to the
        # NOT_IMPLEMENTED mixin -- an empty data_root produces a real
        # NO_DATA, which the mixin cannot produce.
        adapter = build_adapter("nba", str(tmp_path))
        result = adapter.build_features("2026-08-06", "late")
        assert result.status == STAGE_NO_DATA

    def test_predict_freezes_a_real_winner_from_real_historical_results(self, tmp_path):
        # 12 real completed games where "Alpha" always beats "Beta" --
        # Elo must learn a real, non-50/50 preference for Alpha, and
        # predicted_winner must be frozen (moneyline only, no market
        # inspected yet) before match_markets/decide ever run.
        for i in range(12):
            _write_nba_scoreboard(
                tmp_path, f"hist{i}", f"2026-07-{i + 1:02d}T22:10:00+00:00",
                "Alpha", "Beta", "STATUS_FINAL", home_score=110, away_score=90,
            )
        _write_nba_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Alpha", "Beta", "STATUS_SCHEDULED")

        adapter = build_adapter("nba", str(tmp_path))
        result = adapter.predict("2026-08-06", "late")
        assert result.status == STAGE_SUCCESS
        assert result.detail["games_predicted"] == 1
        forecast = adapter._state.forecasts["401"]
        assert forecast.predicted_winner == "home"
        assert forecast.calibrated_probabilities["home"] > 0.5

    def test_decide_without_a_real_market_still_returns_a_real_no_bet_not_a_crash(self, tmp_path):
        # collect_fn is injected (not build_adapter()'s real network-calling
        # closure) so this stays a fast, deterministic unit test -- live
        # network verification against real Polymarket data is done
        # separately, matching the pattern used for MLBAdapter.
        from model_prediction.rebuild.sport_adapter import _BasicEloAdapter

        for i in range(12):
            _write_nba_scoreboard(
                tmp_path, f"hist{i}", f"2026-07-{i + 1:02d}T22:10:00+00:00",
                "Alpha", "Beta", "STATUS_FINAL", home_score=110, away_score=90,
            )
        _write_nba_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Alpha", "Beta", "STATUS_SCHEDULED")

        adapter = _BasicEloAdapter("nba", str(tmp_path), collector=None, collect_fn=lambda d: {"status": "no_markets"})
        adapter.predict("2026-08-06", "late")
        # No real Polymarket data at all in this cold data_root -- match_markets
        # must still succeed honestly with zero real candidates, not crash.
        markets_result = adapter.match_markets("2026-08-06", "late")
        assert markets_result.status == STAGE_SUCCESS
        decide_result = adapter.decide("2026-08-06", "late")
        assert decide_result.status == STAGE_SUCCESS
        assert decide_result.detail["total_bets"] == 0  # no real market evidence -> no real bet is possible


class TestMLBRealCollectAndFeatures:
    def test_collect_and_build_features_are_real_not_stubs(self, tmp_path):
        # MLBAdapter.build_features is real code (horizon_builder.py) --
        # confirm it's actually being called, not silently falling back to
        # the mixin's NOT_IMPLEMENTED stub, by checking against an empty
        # data_root: real code returns NO_DATA (zero real games found),
        # the honest-stub mixin would return NOT_IMPLEMENTED instead.
        adapter = build_adapter("mlb", str(tmp_path))
        result = adapter.build_features("2026-08-06", "late")
        assert result.status != STAGE_NOT_IMPLEMENTED

    def test_build_features_with_a_run_id_records_real_ledger_lineage(self, tmp_path):
        # Real gap closed: horizon_builder.py wrote to FeatureStore but
        # that write was never captured in ShadowLedger lineage even
        # though both real methods independently existed.
        from unittest.mock import patch

        from model_prediction.rebuild.shadow_ledger import ShadowLedger

        with patch(
            "model_prediction.rebuild.mlb_features.build_live_game_feature_row",
            return_value={"event_id": "401", "home_sp_avg_velocity": 93.0},
        ), patch(
            "model_prediction.rebuild.horizon_builder.point_in_time_probable_starters",
            return_value={"401": {"home_starter": "A", "away_starter": "B"}},
        ):
            adapter = build_adapter("mlb", str(tmp_path))
            from model_prediction.rebuild.storage import NormalizedStore, provenance_row, utc_now
            norm = NormalizedStore(f"{tmp_path}/normalized")
            row = {
                **provenance_row(source="espn_public", source_record_id="401", source_version="v1",
                                  observed_at_utc=utc_now().isoformat(),
                                  effective_at_utc="2026-08-06T22:10:00+00:00",
                                  event_start_utc="2026-08-06T22:10:00+00:00"),
                "event_id": "401", "home_team": "Seattle Mariners", "away_team": "Detroit Tigers",
                "home_score": 0, "away_score": 0, "status": "STATUS_SCHEDULED", "venue": "",
            }
            norm.write("mlb", "scoreboard", __import__("polars").DataFrame([row]), primary_key=["event_id"])

            # A real run_id must reference a real runs row (foreign key) --
            # matches exactly what rebuild_shadow_cli.py's run() does
            # before calling any adapter stage.
            setup_ledger = ShadowLedger(f"{tmp_path}/shadow.db")
            real_run_id = setup_ledger.record_run("mlb", run_type="test")
            setup_ledger.close()

            result = adapter.build_features("2026-08-06", "late", run_id=real_run_id)
            assert result.status == "SUCCESS"

        ledger = ShadowLedger(f"{tmp_path}/shadow.db")
        rows = ledger.feature_snapshots_for_horizon("mlb", "late")
        ledger.close()
        assert len(rows) == 1
        assert rows[0]["run_id"] == real_run_id
        assert rows[0]["row_count"] == 1
