from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from model_prediction.rebuild.nfl.audit import audit_nfl_season
from model_prediction.rebuild.nfl.normalize import normalize_nfl_table
from model_prediction.rebuild.nfl.pit import eligible_prior_team_plays, eligible_weekly_roster
from model_prediction.rebuild.nfl.store import NFLNormalizedStore
from model_prediction.rebuild.providers.base import SourceGrade, SourceResponseMetadata

FIXTURES = Path(__file__).parent / "fixtures/providers/nflverse"


def _metadata(observed: str = "2026-08-09T00:00:00+00:00", content: str = "a") -> SourceResponseMetadata:
    return SourceResponseMetadata(
        provider="nflverse",
        sport="nfl",
        endpoint_family="fixture",
        requested_parameters={"season": 2024},
        request_time_utc=observed,
        retrieved_at_utc=observed,
        observed_at_utc=observed,
        http_status=200,
        content_hash=content * 64,
        schema_hash="b" * 64,
        source_version="nflverse-data:fixture",
        source_grade=SourceGrade.B,
    )


def _fixture(name: str) -> pl.DataFrame:
    return pl.DataFrame(json.loads((FIXTURES / name).read_text()))


def test_normalizers_preserve_capture_time_and_mutable_release_warning():
    _, games = normalize_nfl_table("schedule", _fixture("nfl_schedule_rows.json"), _metadata())
    _, plays = normalize_nfl_table("pbp", _fixture("nfl_pbp_rows.json"), _metadata())
    _, rosters = normalize_nfl_table("weekly_rosters", _fixture("nfl_weekly_roster_rows.json"), _metadata())
    for frame in (games, plays, rosters):
        assert frame["observed_at_utc"][0] == "2026-08-09T00:00:00+00:00"
        assert frame["effective_at_utc"][0] == "2026-08-09T00:00:00+00:00"
        assert frame["availability_basis"][0] == "capture_time_only_mutable_release"
    assert games.filter(pl.col("season") == 2024)["event_start_utc"][0] == "2024-09-10T00:15:00+00:00"
    assert plays["game_complete"].all()


def test_historical_backfill_is_not_retrospective_pit_evidence():
    _, games = normalize_nfl_table("schedule", _fixture("nfl_schedule_rows.json"), _metadata())
    _, plays = normalize_nfl_table("pbp", _fixture("nfl_pbp_rows.json"), _metadata())
    result = eligible_prior_team_plays(
        games,
        plays,
        team_id="NYJ",
        decision_time_utc=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert result.is_empty()


def test_later_decision_can_use_complete_observed_game_but_exclusion_is_strict():
    _, games = normalize_nfl_table("schedule", _fixture("nfl_schedule_rows.json"), _metadata())
    _, plays = normalize_nfl_table("pbp", _fixture("nfl_pbp_rows.json"), _metadata())
    decision = datetime(2026, 8, 10, tzinfo=UTC)
    eligible = eligible_prior_team_plays(games, plays, team_id="NYJ", decision_time_utc=decision)
    excluded = eligible_prior_team_plays(
        games,
        plays,
        team_id="NYJ",
        decision_time_utc=decision,
        exclude_event_id="2024_01_NYJ_SF",
    )
    assert eligible.height == 1
    assert eligible["play_id"].to_list() == ["1"]
    assert excluded.is_empty()


def test_end_of_regulation_clock_without_end_game_marker_fails_closed():
    pbp = (
        _fixture("nfl_pbp_rows.json")
        .filter(pl.col("play_id") == 1)
        .with_columns(pl.lit(0).alias("game_seconds_remaining"))
    )
    _, plays = normalize_nfl_table("pbp", pbp, _metadata())
    assert not plays["game_complete"].any()


def test_roster_captured_after_decision_is_hidden():
    _, roster = normalize_nfl_table("weekly_rosters", _fixture("nfl_weekly_roster_rows.json"), _metadata())
    before = eligible_weekly_roster(
        roster,
        team_id="NYJ",
        season=2024,
        week=1,
        decision_time_utc=datetime(2025, 1, 1, tzinfo=UTC),
    )
    after = eligible_weekly_roster(
        roster,
        team_id="NYJ",
        season=2024,
        week=1,
        decision_time_utc=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert before.is_empty()
    assert after.height == 2


def test_store_preserves_corrected_vintage_and_is_content_idempotent(tmp_path):
    _, first = normalize_nfl_table("schedule", _fixture("nfl_schedule_rows.json"), _metadata())
    corrected_source = _fixture("nfl_schedule_rows.json").with_columns(
        pl.when(pl.col("season") == 2024).then(pl.lit(33)).otherwise(pl.col("home_score")).alias("home_score")
    )
    _, corrected = normalize_nfl_table(
        "schedule", corrected_source, _metadata("2026-08-10T00:00:00+00:00", "c")
    )
    store = NFLNormalizedStore(tmp_path)
    path = store.write("games", 2024, first.filter(pl.col("season") == 2024))
    assert store.write("games", 2024, first.filter(pl.col("season") == 2024)) == path
    store.write("games", 2024, corrected.filter(pl.col("season") == 2024))
    stored = store.read_season("games", 2024)
    assert stored.height == 2
    assert sorted(stored["home_score"].to_list()) == [32, 33]


def test_kickoff_timezone_uses_eastern_dst_and_missing_time_fails_closed():
    source = (
        _fixture("nfl_schedule_rows.json")
        .head(1)
        .with_columns(
            pl.lit("2024-11-03").alias("gameday"),
            pl.lit("13:00").alias("gametime"),
        )
    )
    _, games = normalize_nfl_table("schedule", source, _metadata())
    assert games["event_start_utc"][0] == "2024-11-03T18:00:00+00:00"
    with pytest.raises(ValueError, match="missing NFL gametime"):
        normalize_nfl_table("schedule", source.with_columns(pl.lit("").alias("gametime")), _metadata())


def test_audit_keeps_capture_only_limitation_explicit(tmp_path):
    _, games = normalize_nfl_table("schedule", _fixture("nfl_schedule_rows.json"), _metadata())
    _, plays = normalize_nfl_table("pbp", _fixture("nfl_pbp_rows.json"), _metadata())
    _, rosters = normalize_nfl_table("weekly_rosters", _fixture("nfl_weekly_roster_rows.json"), _metadata())
    store = NFLNormalizedStore(tmp_path)
    store.write("games", 2024, games.filter(pl.col("season") == 2024))
    store.write("plays", 2024, plays)
    store.write("weekly_rosters", 2024, rosters)
    report = audit_nfl_season(store, 2024)
    assert report["status"] == "HEALTHY"
    assert "not retrospective PIT evidence" in report["qualification_note"]
