"""Tests for the real early/mid/late horizon dataset builder
(horizon_builder.py, FOUNDATION_COMPLETION.md Phase 7).
"""

from __future__ import annotations

from unittest.mock import patch

import polars as pl
import pytest

from model_prediction.rebuild.horizon_builder import build_mlb_horizon_dataset
from model_prediction.rebuild.horizons import horizon_specs_for_sport
from model_prediction.rebuild.storage import FeatureStore, NormalizedStore, provenance_row, utc_now


def _write_scoreboard(data_root, event_id: str, event_start_utc: str, home: str, away: str) -> None:
    norm = NormalizedStore(f"{data_root}/normalized")
    row = {
        **provenance_row(
            source="espn_public", source_record_id=event_id, source_version="v1",
            observed_at_utc=utc_now().isoformat(), effective_at_utc=event_start_utc,
            event_start_utc=event_start_utc,
        ),
        "event_id": event_id, "home_team": home, "away_team": away,
        "home_score": 0, "away_score": 0, "status": "STATUS_SCHEDULED", "venue": "",
    }
    norm.write("mlb", "scoreboard", pl.DataFrame([row]), primary_key=["event_id"])


class TestInvalidHorizon:
    def test_unknown_horizon_raises(self, tmp_path):
        with pytest.raises(ValueError, match="horizon must be one of"):
            build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "afternoon", [])


class TestDecisionTimesDifferByHorizon:
    """The one thing that's genuinely real and horizon-sensitive today:
    decision_time_utc itself, and anything joined against a real
    observation timestamp (probable starters)."""

    def test_early_mid_late_produce_different_real_decision_times(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers")

        early = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "early", [])
        mid = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "mid", [])
        late = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "late", [])

        assert early.decision_times["401"] != mid.decision_times["401"] != late.decision_times["401"]
        # early is furthest before start, late is closest
        assert early.decision_times["401"] < mid.decision_times["401"] < late.decision_times["401"]


class TestAvailableInformationIsRecorded:
    """CLAUDE.md Part 1 sec 9: 'Every prediction must store:
    decision_timestamp, horizon, available_information.' Real gap closed
    here: horizon_specs_for_sport() declared per-horizon available
    features for every sport but had zero real callers anywhere in this
    codebase (verified via grep) before this."""

    def test_result_carries_the_real_declared_features_for_this_horizon(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers")

        result = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "mid", [])

        assert result.available_information == horizon_specs_for_sport("mlb")["mid"].available_features
        assert result.available_information != []

    def test_early_and_late_declare_different_available_information(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers")

        early = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "early", [])
        late = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "late", [])

        assert early.available_information != late.available_information


class TestMissingnessIsHonest:
    def test_no_probable_starter_data_is_recorded_as_real_missingness_not_silently_dropped(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers")

        result = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "late", [])

        assert result.coverage["total_games"] == 1
        assert result.coverage["rows_built"] == 0
        assert result.coverage["coverage_fraction"] == 0.0
        assert result.missingness["missing_reasons"]["no_point_in_time_probable_starters"] == 1
        assert result.snapshot_hash is None  # nothing to persist

    def test_probable_starter_observed_after_early_cutoff_is_honest_early_missingness(self, tmp_path):
        # A revision published close to game time is real, valid data for
        # "late" but must not leak into "early" -- exactly the point-in-time
        # guarantee point_in_time_probable_starters exists to provide.
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers")
        records = [{
            "event_id": "401", "observed_at_utc": "2026-08-06T21:30:00+00:00",  # ~40min before start
            "home_starter": "Some Pitcher", "away_starter": "Other Pitcher",
        }]

        early = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "early", records)
        late = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "late", records)

        # early's decision time (T-36h) is long before this observation was
        # published -- it must not see it.
        assert early.missingness["missing_reasons"].get("no_point_in_time_probable_starters") == 1
        # late's decision time (T-60m) is after the observation -- it has a
        # real probable starter to work with (may still fail later at real
        # Statcast-ID resolution in this test's empty-data environment, but
        # NOT for the point-in-time reason).
        assert "no_point_in_time_probable_starters" not in late.missingness["missing_reasons"]

    def test_no_scheduled_games_is_zero_coverage_not_an_error(self, tmp_path):
        result = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "late", [])
        assert result.coverage["total_games"] == 0
        assert result.coverage["rows_built"] == 0


class TestSnapshotHashReflectsRealContent:
    """Real bug found by external review, verified then fixed: snapshot_hash
    was computed from {game_date, horizon, event_ids} only -- metadata, not
    the actual feature values. FeatureStore.write_snapshot() treats a
    repeated hash as an immutable identity (a no-op write) -- a real source
    correction that changed feature values without changing event_ids would
    silently never persist, masquerading as the stale prior version."""

    def test_same_event_ids_but_different_feature_content_yields_different_hashes(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers")
        records = [{
            "event_id": "401", "observed_at_utc": "2026-08-06T21:30:00+00:00",
            "home_starter": "Pitcher A", "away_starter": "Pitcher B",
        }]

        first_row = {"event_id": "401", "home_sp_avg_velocity": 93.0}
        second_row = {"event_id": "401", "home_sp_avg_velocity": 97.5}  # a real corrected value

        with patch(
            "model_prediction.rebuild.mlb_features.build_live_game_feature_row",
            return_value=first_row,
        ):
            first = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "late", records)

        with patch(
            "model_prediction.rebuild.mlb_features.build_live_game_feature_row",
            return_value=second_row,
        ):
            second = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "late", records)

        assert first.snapshot_hash != second.snapshot_hash, (
            "a real change in feature content for the same event_ids must "
            "produce a different snapshot_hash, or the corrected data "
            "silently never persists (FeatureStore treats a repeated hash "
            "as an already-seen, no-op write)"
        )

        # And the corrected content is actually the one readable back --
        # not silently discarded in favor of the stale version.
        store = FeatureStore(str(tmp_path) + "/features")
        stored = store.read_version("mlb", "late", second.snapshot_hash)
        assert stored["home_sp_avg_velocity"][0] == 97.5

    def test_identical_content_yields_the_same_hash(self, tmp_path):
        _write_scoreboard(tmp_path, "401", "2026-08-06T22:10:00+00:00", "Seattle Mariners", "Detroit Tigers")
        records = [{
            "event_id": "401", "observed_at_utc": "2026-08-06T21:30:00+00:00",
            "home_starter": "Pitcher A", "away_starter": "Pitcher B",
        }]
        row = {"event_id": "401", "home_sp_avg_velocity": 93.0}

        with patch("model_prediction.rebuild.mlb_features.build_live_game_feature_row", return_value=row):
            first = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "late", records)
            second = build_mlb_horizon_dataset(str(tmp_path), "2026-08-06", "late", records)

        assert first.snapshot_hash == second.snapshot_hash
