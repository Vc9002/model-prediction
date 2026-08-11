from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from model_prediction.rebuild.nfl.audit import audit_nfl_season
from model_prediction.rebuild.nfl.foundation import NFLFoundation
from model_prediction.rebuild.nfl.normalize import normalize_nfl_table
from model_prediction.rebuild.nfl.pit import eligible_prior_team_plays, eligible_weekly_roster
from model_prediction.rebuild.nfl.store import NFLNormalizedStore
from model_prediction.rebuild.providers.base import (
    ProviderResult,
    ProviderStatus,
    SourceGrade,
    SourceResponseMetadata,
)

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
        license_id="CC-BY-4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_required=True,
        attribution_text="nflverse project data; attribution required under CC BY 4.0",
        upstream_rights_status="unresolved",
        commercial_use_status="unresolved",
        production_allowed=False,
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
        assert frame["license_id"][0] == "CC-BY-4.0"
        assert frame["attribution_required"][0] is True
        assert frame["upstream_rights_status"][0] == "unresolved"
        assert frame["commercial_use_status"][0] == "unresolved"
        assert frame["production_allowed"][0] is False
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


def test_schedule_normalizer_survives_all_null_prefix_longer_than_inference_window():
    # Real bug found backfilling nflverse 2022 schedule data: pl.DataFrame(rows)
    # defaults to infer_schema_length=100, so a nullable column (temp/wind are
    # None for every dome game) that stays None for the first 100+ rows gets
    # locked to a Null dtype, then crashes ("could not append value... f64")
    # the moment a real float shows up later -- entirely row-order dependent,
    # not a data-content bug. Repro here: 120 all-null-weather rows followed
    # by one real outdoor game.
    rows = []
    for i in range(120):
        rows.append(
            {
                "game_id": f"2024_{i:02d}_AAA_BBB",
                "season": 2024,
                "game_type": "REG",
                "week": (i % 18) + 1,
                "gameday": "2024-09-09",
                "gametime": "20:15",
                "away_team": "AAA",
                "home_team": "BBB",
                "away_score": 10,
                "home_score": 20,
                "overtime": 0,
                "stadium": "Dome Stadium",
                "roof": "dome",
                "surface": "turf",
                "temp": None,
                "wind": None,
                "away_rest": 7,
                "home_rest": 7,
            }
        )
    rows.append(
        {
            "game_id": "2024_99_CCC_DDD",
            "season": 2024,
            "game_type": "REG",
            "week": 9,
            "gameday": "2024-11-01",
            "gametime": "13:00",
            "away_team": "CCC",
            "home_team": "DDD",
            "away_score": 13,
            "home_score": 32,
            "overtime": 0,
            "stadium": "Outdoor Stadium",
            "roof": "outdoors",
            "surface": "grass",
            "temp": 66.0,
            "wind": 9.0,
            "away_rest": 8,
            "home_rest": 8,
        }
    )
    source = pl.DataFrame(rows, infer_schema_length=None)
    _, games = normalize_nfl_table("schedule", source, _metadata())
    assert games.height == 121
    last = games.filter(pl.col("event_id") == "2024_99_CCC_DDD")
    assert last["temperature_f"][0] == 66.0
    assert last["wind_mph"][0] == 9.0


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
    assert report["license_id"] == "CC-BY-4.0"
    assert report["attribution_required"] is True
    assert report["upstream_rights_status"] == "unresolved"
    assert report["production_allowed"] is False


def test_dataset_manifest_preserves_asset_rights_metadata(tmp_path):
    class FixtureNFLVerseProvider:
        def season_table(self, table: str, season: int, *, force: bool = False) -> ProviderResult:
            assert table == "schedule"
            assert season == 2024
            assert force is False
            return ProviderResult(
                ProviderStatus.AVAILABLE,
                _metadata(),
                _fixture("nfl_schedule_rows.json").filter(pl.col("season") == season),
            )

    foundation = NFLFoundation(FixtureNFLVerseProvider(), tmp_path)  # type: ignore[arg-type]
    report = foundation.backfill([2024], tables=("schedule",))
    season = report["seasons"][0]
    asset = season["source_assets"]["schedule"]
    assert asset["license_id"] == "CC-BY-4.0"
    assert asset["attribution_required"] is True
    assert asset["upstream_rights_status"] == "unresolved"
    assert asset["commercial_use_status"] == "unresolved"
    assert asset["production_allowed"] is False
    assert season["production_allowed"] is False
