from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from model_prediction.rebuild.providers.base import SourceGrade, SourceResponseMetadata
from model_prediction.rebuild.wnba.audit import audit_wnba_season
from model_prediction.rebuild.wnba.normalize import normalize_wnba_table
from model_prediction.rebuild.wnba.pit import eligible_prior_team_games
from model_prediction.rebuild.wnba.store import WNBANormalizedStore

FIXTURE = Path(__file__).parent / "fixtures/providers/sportsdataverse/wnba_schedule_rows.json"


def _metadata(observed: str = "2026-08-09T00:00:00+00:00") -> SourceResponseMetadata:
    return SourceResponseMetadata(
        provider="sportsdataverse",
        sport="wnba",
        endpoint_family="wnba_schedule",
        requested_parameters={"season": 2024},
        request_time_utc=observed,
        retrieved_at_utc=observed,
        observed_at_utc=observed,
        http_status=200,
        content_hash="a" * 64,
        schema_hash="b" * 64,
        source_version="0.0.72",
        source_grade=SourceGrade.B,
    )


def _schedule_frame() -> pl.DataFrame:
    return pl.DataFrame(json.loads(FIXTURE.read_text())).with_columns(
        pl.col("game_date_time").str.to_datetime(time_zone="UTC")
    )


def test_schedule_normalization_preserves_capture_time_not_fake_historical_time():
    table, frame = normalize_wnba_table("schedule", _schedule_frame(), _metadata())
    assert table == "games"
    assert frame["observed_at_utc"][0] == "2026-08-09T00:00:00+00:00"
    assert frame["event_start_utc"][0].startswith("2024-05-15T23:00:00")
    assert frame["availability_basis"][0] == "capture_time_only"
    assert frame["home_team_canonical_id"][0] == "wnba:team:espn:1"
    assert frame["away_team_canonical_id"][0] == "wnba:team:espn:2"


def test_historical_backfill_is_excluded_from_earlier_decision():
    _, games = normalize_wnba_table("schedule", _schedule_frame(), _metadata())
    team_box = pl.DataFrame({
        "event_id": ["401000001"],
        "team_id": ["1"],
        "observed_at_utc": ["2026-08-09T00:00:00+00:00"],
        "pit_eligible": [True],
        "event_start_utc": ["2024-05-15T23:00:00+00:00"],
        "points": [82],
    })
    result = eligible_prior_team_games(
        games,
        team_box,
        team_id="1",
        decision_time_utc=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert result.is_empty()


def test_same_feature_gate_allows_observation_for_later_decision():
    _, games = normalize_wnba_table("schedule", _schedule_frame(), _metadata())
    team_box = pl.DataFrame({
        "event_id": ["401000001"],
        "team_id": ["1"],
        "observed_at_utc": ["2026-08-09T00:00:00+00:00"],
        "pit_eligible": [True],
        "event_start_utc": ["2024-05-15T23:00:00+00:00"],
        "points": [82],
    })
    result = eligible_prior_team_games(
        games,
        team_box,
        team_id="1",
        decision_time_utc=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert result.height == 1


def test_partition_store_is_content_idempotent(tmp_path):
    _, games = normalize_wnba_table("schedule", _schedule_frame(), _metadata())
    store = WNBANormalizedStore(tmp_path)
    first = store.write("games", 2024, games)
    second = store.write("games", 2024, games)
    assert first == second
    assert len(list(first.parent.glob("part-*.parquet"))) == 1


def test_latest_view_keeps_history_but_selects_newest_observation(tmp_path):
    _, old = normalize_wnba_table("schedule", _schedule_frame(), _metadata("2026-08-09T00:00:00+00:00"))
    _, new = normalize_wnba_table("schedule", _schedule_frame(), _metadata("2026-08-10T00:00:00+00:00"))
    store = WNBANormalizedStore(tmp_path)
    store.write("games", 2024, old)
    store.write("games", 2024, new)
    assert store.read_season("games", 2024).height == 2
    latest = store.read_latest("games", 2024)
    assert latest.height == 1
    assert latest["observed_at_utc"][0] == "2026-08-10T00:00:00+00:00"


def test_audit_reports_missing_boxes_as_degraded_not_system_failure(tmp_path):
    _, games = normalize_wnba_table("schedule", _schedule_frame(), _metadata())
    store = WNBANormalizedStore(tmp_path)
    store.write("games", 2024, games)
    report = audit_wnba_season(store, 2024)
    assert report["status"] == "DEGRADED"
    assert report["missing_team_boxscores"] == 1
    assert "not retrospective PIT evidence" in report["qualification_note"]
