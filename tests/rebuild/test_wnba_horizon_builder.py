"""Tests for the recovered WNBA horizon feature builder (horizon_builder.py),
ported/expanded from the archived `origin/rebuild/wnba-v1` branch's
`test_wnba_research_guards.py` alongside the source file itself -- see
`docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md` for the RECOVER
verdict. The archive review flagged one real gap in the original branch:
"the 5-attempt cutoff-stabilization loop in `_target_as_of_cutoff` ...
deserves its own targeted unit tests for the postponement-drift case
specifically (a game whose start moves more than once)" -- that gap is
closed here (`TestPostponementCutoffStabilization`), it was not covered by
any test on the archived branch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from model_prediction.rebuild.storage import FeatureStore
from model_prediction.rebuild.wnba.horizon_builder import (
    WNBAFeatureBuildResult,
    _assert_research_source_provenance,
    _latest_targets,
    _target_as_of_cutoff,
    build_wnba_live_features,
    build_wnba_replay_features,
)
from model_prediction.rebuild.wnba.store import WNBANormalizedStore
from model_prediction.rebuild.wnba.time import sports_event_date


def _source_frame() -> pl.DataFrame:
    return pl.DataFrame([{
        "event_id": "late-et",
        "event_start_utc": "2026-05-02T02:00:00+00:00",
        "sports_event_date": "2026-05-01",
        "observed_at_utc": "2026-05-01T20:00:00+00:00",
        "raw_snapshot_hash": "raw-1",
        "availability_basis": "capture_time_only",
        "commercial_use_status": "unresolved",
        "production_allowed": False,
    }])


class TestLatestTargetsHonorsWnbaSportsDate:
    def test_late_eastern_game_stays_on_one_wnba_sports_date_across_utc_midnight(self):
        assert sports_event_date("2026-05-02T02:00:00+00:00") == "2026-05-01"
        targets = _latest_targets(_source_frame(), "2026-05-01")
        assert targets["event_id"].to_list() == ["late-et"]
        assert _latest_targets(_source_frame(), "2026-05-02").is_empty()


class TestResearchProvenanceGateFailsClosed:
    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("availability_basis", None),
            ("availability_basis", "unknown"),
            ("commercial_use_status", None),
            ("commercial_use_status", "cleared"),
            ("production_allowed", None),
            ("production_allowed", True),
            ("raw_snapshot_hash", None),
            ("raw_snapshot_hash", ""),
        ],
    )
    def test_source_rights_and_provenance_fail_closed_before_feature_filtering(self, column, value):
        frame = _source_frame().with_columns(pl.lit(value).alias(column))
        with pytest.raises(ValueError):
            _assert_research_source_provenance(frame, "games")

    def test_missing_source_provenance_column_fails_closed(self):
        with pytest.raises(ValueError, match="missing provenance"):
            _assert_research_source_provenance(
                _source_frame().drop("commercial_use_status"), "games",
            )


class TestPostponementCutoffStabilization:
    """Real gap the archive review flagged and this port closes: no test on
    the archived branch exercised a target whose scheduled start moved more
    than once before stabilizing."""

    def _games(self, starts: list[tuple[str, str]]) -> pl.DataFrame:
        # starts: list of (observed_at_utc, event_start_utc) revisions, in
        # the order they were captured.
        return pl.DataFrame([
            {
                "event_id": "postponed-1",
                "event_start_utc": start,
                "observed_at_utc": observed,
                "pit_eligible": True,
            }
            for observed, start in starts
        ])

    def test_two_revisions_converge_to_the_final_known_start(self):
        # First capture: game scheduled for 2026-05-10T20:00Z. Second
        # capture (a real postponement): pushed to 2026-05-12T20:00Z. A
        # naive single-shot cutoff (initial_start - hours_before) would use
        # the *first* start and miss that the game moved; the stabilization
        # loop must re-resolve against the schedule state knowable as of
        # each successive cutoff estimate until it stops moving.
        games = self._games([
            ("2026-05-01T00:00:00+00:00", "2026-05-10T20:00:00+00:00"),
            ("2026-05-09T00:00:00+00:00", "2026-05-12T20:00:00+00:00"),
        ])
        resolved = _target_as_of_cutoff(
            games,
            event_id="postponed-1",
            initial_start=datetime(2026, 5, 10, 20, tzinfo=UTC),
            hours_before=1.0,
        )
        assert resolved is not None
        row, decision_time = resolved
        assert row["event_start_utc"] == "2026-05-12T20:00:00+00:00"
        assert decision_time == datetime(2026, 5, 12, 19, tzinfo=UTC)

    def test_three_revisions_still_converge(self):
        # A double-postponement: moved twice before settling. The loop caps
        # at 5 attempts, so 3 revisions must still resolve.
        games = self._games([
            ("2026-05-01T00:00:00+00:00", "2026-05-10T20:00:00+00:00"),
            ("2026-05-08T00:00:00+00:00", "2026-05-13T20:00:00+00:00"),
            ("2026-05-12T00:00:00+00:00", "2026-05-15T20:00:00+00:00"),
        ])
        resolved = _target_as_of_cutoff(
            games,
            event_id="postponed-1",
            initial_start=datetime(2026, 5, 10, 20, tzinfo=UTC),
            hours_before=6.0,
        )
        assert resolved is not None
        row, decision_time = resolved
        assert row["event_start_utc"] == "2026-05-15T20:00:00+00:00"
        assert decision_time == datetime(2026, 5, 15, 14, tzinfo=UTC)

    def test_non_converging_schedule_fails_closed(self):
        # Pathological: each successive cutoff estimate reveals yet another
        # revision, indefinitely (simulated for 6+ steps, more than the
        # 5-attempt cap). Must raise, not silently use a stale cutoff.
        starts = [
            (f"2026-0{i}-01T00:00:00+00:00", f"2026-0{i + 1}-10T20:00:00+00:00")
            for i in range(1, 7)
        ]
        games = self._games(starts)
        with pytest.raises(ValueError, match="did not stabilize"):
            _target_as_of_cutoff(
                games,
                event_id="postponed-1",
                initial_start=datetime(2026, 1, 10, 20, tzinfo=UTC),
                hours_before=1.0,
            )

    def test_no_eligible_revision_before_cutoff_returns_none(self):
        # Only revision is observed after the computed cutoff -- nothing
        # knowable at that decision time yet.
        games = self._games([("2026-05-10T19:30:00+00:00", "2026-05-10T20:00:00+00:00")])
        resolved = _target_as_of_cutoff(
            games,
            event_id="postponed-1",
            initial_start=datetime(2026, 5, 10, 20, tzinfo=UTC),
            hours_before=1.0,
        )
        assert resolved is None


# ── End-to-end: build_wnba_replay_features / build_wnba_live_features ──────


def _base_row(**overrides: object) -> dict[str, object]:
    row = {
        "availability_basis": "capture_time_only",
        "commercial_use_status": "unresolved",
        "production_allowed": False,
        "pit_eligible": True,
    }
    row.update(overrides)
    return row


def _prior_game_and_boxes(
    *, event_id: str, team_id: str, opponent_id: str, event_start_utc: str,
) -> tuple[dict, list[dict]]:
    game = _base_row(
        event_id=event_id,
        event_start_utc=event_start_utc,
        sports_event_date=event_start_utc[:10],
        observed_at_utc="2024-04-01T00:00:00+00:00",
        raw_snapshot_hash=f"game-{event_id}",
        completed=True,
        home_team_id=team_id,
        away_team_id=opponent_id,
        home_team_canonical_id=f"canon-{team_id}",
        away_team_canonical_id=f"canon-{opponent_id}",
    )
    boxes = [
        _base_row(
            event_id=event_id, team_id=team_id, opponent_team_id=opponent_id,
            observed_at_utc="2024-04-01T00:00:00+00:00", raw_snapshot_hash=f"box-{event_id}-{team_id}",
            points=80, field_goals_made=30, field_goals_attempted=70,
            three_points_made=8, three_points_attempted=24,
            free_throws_made=12, free_throws_attempted=16,
            offensive_rebounds=10, defensive_rebounds=25, turnovers=12,
        ),
        _base_row(
            event_id=event_id, team_id=opponent_id, opponent_team_id=team_id,
            observed_at_utc="2024-04-01T00:00:00+00:00", raw_snapshot_hash=f"box-{event_id}-{opponent_id}",
            points=70, field_goals_made=27, field_goals_attempted=68,
            three_points_made=7, three_points_attempted=22,
            free_throws_made=9, free_throws_attempted=12,
            offensive_rebounds=8, defensive_rebounds=24, turnovers=14,
        ),
    ]
    return game, boxes


def _write_ready_season(tmp_path, *, target_start_utc: str) -> WNBANormalizedStore:
    """Team A and Team B each have one completed prior game (vs. filler
    opponents C/D) before a scheduled A-vs-B target game -- enough history
    for build_team_form_snapshot to return AVAILABLE for both sides."""
    store = WNBANormalizedStore(Path(tmp_path) / "normalized")
    game_a, boxes_a = _prior_game_and_boxes(
        event_id="g-a", team_id="A", opponent_id="C", event_start_utc="2024-05-01T00:00:00+00:00",
    )
    game_b, boxes_b = _prior_game_and_boxes(
        event_id="g-b", team_id="B", opponent_id="D", event_start_utc="2024-05-01T00:00:00+00:00",
    )
    target = _base_row(
        event_id="g-target",
        event_start_utc=target_start_utc,
        sports_event_date=target_start_utc[:10],
        observed_at_utc="2024-04-15T00:00:00+00:00",
        raw_snapshot_hash="game-g-target",
        completed=False,
        home_team_id="A",
        away_team_id="B",
        home_team_canonical_id="canon-A",
        away_team_canonical_id="canon-B",
    )
    games = pl.DataFrame([game_a, game_b, target])
    team_box = pl.DataFrame([*boxes_a, *boxes_b])
    store.write("games", 2024, games)
    store.write("team_box", 2024, team_box)
    return store


class TestInvalidInputsFailClosed:
    def test_unknown_horizon_raises(self, tmp_path):
        with pytest.raises(ValueError, match="horizon must be one of"):
            build_wnba_replay_features(str(tmp_path), "2024-05-10", "afternoon")

    def test_non_calendar_date_raises(self, tmp_path):
        with pytest.raises(ValueError, match="ISO calendar date"):
            build_wnba_replay_features(str(tmp_path), "2024-05-10T00:00:00", "late")


class TestReplayBuildProducesARealRow:
    def test_full_replay_build_returns_one_row_with_both_sides_populated(self, tmp_path):
        _write_ready_season(tmp_path, target_start_utc="2024-05-10T20:00:00+00:00")
        result = build_wnba_replay_features(str(tmp_path), "2024-05-10", "late")
        assert isinstance(result, WNBAFeatureBuildResult)
        assert result.mode == "replay"
        assert result.target_games == 1
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row["event_id"] == "g-target"
        assert row["home_team_id"] == "A"
        assert row["away_team_id"] == "B"
        assert row["home_season_ortg"] is not None
        assert row["away_season_ortg"] is not None
        assert row["horizon"] == "late"
        # decision_time_utc = event_start_utc - 1h for "late".
        assert row["decision_time_utc"] == "2024-05-10T19:00:00+00:00"
        assert result.snapshot_hash is not None
        assert result.feature_schema_hash is not None

    def test_snapshot_is_persisted_and_readable_back(self, tmp_path):
        _write_ready_season(tmp_path, target_start_utc="2024-05-10T20:00:00+00:00")
        result = build_wnba_replay_features(str(tmp_path), "2024-05-10", "late")
        store = FeatureStore(str(tmp_path) + "/features")
        persisted = store.read_version("wnba", "late", result.snapshot_hash)
        assert persisted.height == 1
        assert persisted["event_id"][0] == "g-target"

    def test_hash_is_deterministic_and_rerun_is_idempotent(self, tmp_path):
        _write_ready_season(tmp_path, target_start_utc="2024-05-10T20:00:00+00:00")
        first = build_wnba_replay_features(str(tmp_path), "2024-05-10", "late")
        second = build_wnba_replay_features(str(tmp_path), "2024-05-10", "late")
        assert first.snapshot_hash == second.snapshot_hash

    def test_different_horizons_yield_different_decision_times_and_hashes(self, tmp_path):
        _write_ready_season(tmp_path, target_start_utc="2024-05-10T20:00:00+00:00")
        late = build_wnba_replay_features(str(tmp_path), "2024-05-10", "late")
        early = build_wnba_replay_features(str(tmp_path), "2024-05-10", "early")
        assert late.rows[0]["decision_time_utc"] != early.rows[0]["decision_time_utc"]
        assert late.snapshot_hash != early.snapshot_hash


class TestMissingnessIsHonest:
    def test_target_with_no_prior_team_history_is_recorded_not_dropped_silently(self, tmp_path):
        store = WNBANormalizedStore(Path(tmp_path) / "normalized")
        target = _base_row(
            event_id="g-cold-start",
            event_start_utc="2024-05-10T20:00:00+00:00",
            sports_event_date="2024-05-10",
            observed_at_utc="2024-04-15T00:00:00+00:00",
            raw_snapshot_hash="game-g-cold-start",
            completed=False,
            home_team_id="A",
            away_team_id="B",
            home_team_canonical_id="canon-A",
            away_team_canonical_id="canon-B",
        )
        store.write("games", 2024, pl.DataFrame([target]))
        result = build_wnba_replay_features(str(tmp_path), "2024-05-10", "late")
        assert result.rows == []
        assert result.target_games == 1
        assert result.missing_reasons.get("team_form_metrics_unavailable") == 1
        assert result.snapshot_hash is None

    def test_no_scheduled_games_on_date_is_zero_targets_not_an_error(self, tmp_path):
        _write_ready_season(tmp_path, target_start_utc="2024-05-10T20:00:00+00:00")
        result = build_wnba_replay_features(str(tmp_path), "2024-06-01", "late")
        assert result.target_games == 0
        assert result.rows == []


class TestLiveModeRespectsItsOwnCutoff:
    def test_decision_cutoff_not_reached_is_skipped_in_live_mode(self, tmp_path):
        _write_ready_season(tmp_path, target_start_utc="2024-05-10T20:00:00+00:00")
        # "early" cutoff is 36h before tipoff (2024-05-09T08:00Z); asking
        # live mode "as of" well before that must decline, not guess.
        knowledge_time = datetime(2024, 5, 1, tzinfo=UTC)
        result = build_wnba_live_features(
            str(tmp_path), "2024-05-10", "early", knowledge_time_utc=knowledge_time,
        )
        assert result.rows == []
        assert result.missing_reasons.get("decision_cutoff_not_reached") == 1

    def test_live_mode_after_cutoff_produces_the_row(self, tmp_path):
        _write_ready_season(tmp_path, target_start_utc="2024-05-10T20:00:00+00:00")
        knowledge_time = datetime(2024, 5, 10, 19, 30, tzinfo=UTC)  # after "late" cutoff (19:00Z)
        result = build_wnba_live_features(
            str(tmp_path), "2024-05-10", "late", knowledge_time_utc=knowledge_time,
        )
        assert len(result.rows) == 1
        assert result.mode == "live"

    def test_live_knowledge_time_must_be_timezone_aware(self, tmp_path):
        _write_ready_season(tmp_path, target_start_utc="2024-05-10T20:00:00+00:00")
        with pytest.raises(ValueError, match="timezone-aware"):
            build_wnba_live_features(
                str(tmp_path), "2024-05-10", "late",
                knowledge_time_utc=datetime(2024, 5, 10, 19, 30, tzinfo=UTC).replace(tzinfo=None),
            )


class TestRightsGateAppliesToRealNormalizedData:
    def test_production_enabled_source_is_rejected(self, tmp_path):
        store = WNBANormalizedStore(Path(tmp_path) / "normalized")
        target = _base_row(
            event_id="g-prod",
            event_start_utc="2024-05-10T20:00:00+00:00",
            sports_event_date="2024-05-10",
            observed_at_utc="2024-04-15T00:00:00+00:00",
            raw_snapshot_hash="game-g-prod",
            completed=False,
            home_team_id="A",
            away_team_id="B",
            home_team_canonical_id="canon-A",
            away_team_canonical_id="canon-B",
            production_allowed=True,
        )
        store.write("games", 2024, pl.DataFrame([target]))
        with pytest.raises(ValueError, match="production-enabled"):
            build_wnba_replay_features(str(tmp_path), "2024-05-10", "late")
