from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from model_prediction.rebuild.identity import IdentityRegistry, resolve_espn_scoreboard_team_ids
from model_prediction.rebuild.metadata import MetadataDB
from model_prediction.rebuild.providers.base import (
    ProviderResult,
    ProviderStatus,
    SourceGrade,
    SourceResponseMetadata,
)
from model_prediction.rebuild.wnba.audit import audit_wnba_season
from model_prediction.rebuild.wnba.foundation import WNBAFoundation
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


def _identity(tmp_path) -> IdentityRegistry:
    return IdentityRegistry(MetadataDB(tmp_path / "metadata.db"))


def test_schedule_normalization_preserves_capture_time_not_fake_historical_time(tmp_path):
    identity = _identity(tmp_path)
    table, frame = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=identity,
    )
    assert table == "games"
    assert frame["observed_at_utc"][0] == "2026-08-09T00:00:00+00:00"
    assert frame["event_start_utc"][0].startswith("2024-05-15T23:00:00")
    assert frame["availability_basis"][0] == "capture_time_only"
    assert frame["home_team_canonical_id"][0] == identity.resolve("espn_public:wnba", "1").entity_id
    assert frame["away_team_canonical_id"][0] == identity.resolve("espn_public:wnba", "2").entity_id


def test_historical_backfill_is_excluded_from_earlier_decision(tmp_path):
    _, games = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=_identity(tmp_path),
    )
    team_box = pl.DataFrame({
        "event_id": ["401000001"],
        "team_id": ["1"],
        "observed_at_utc": ["2026-08-09T00:00:00+00:00"],
        "raw_snapshot_hash": ["team-box-raw"],
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


def test_same_feature_gate_allows_observation_for_later_decision(tmp_path):
    _, games = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=_identity(tmp_path),
    )
    team_box = pl.DataFrame({
        "event_id": ["401000001"],
        "team_id": ["1"],
        "observed_at_utc": ["2026-08-09T00:00:00+00:00"],
        "raw_snapshot_hash": ["team-box-raw"],
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
    _, games = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=_identity(tmp_path),
    )
    store = WNBANormalizedStore(tmp_path)
    first = store.write("games", 2024, games)
    second = store.write("games", 2024, games)
    assert first == second
    assert len(list(first.parent.glob("part-*.parquet"))) == 1


def test_partition_store_refuses_conflicting_same_observation(tmp_path):
    _, games = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=_identity(tmp_path),
    )
    store = WNBANormalizedStore(tmp_path)
    store.write("games", 2024, games)
    changed = games.with_columns(pl.lit(999).alias("home_score"))
    with pytest.raises(ValueError, match="conflicting WNBA games content"):
        store.write("games", 2024, changed)


def test_partition_store_detects_corrupted_part_bytes(tmp_path):
    _, games = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=_identity(tmp_path),
    )
    store = WNBANormalizedStore(tmp_path)
    part = store.write("games", 2024, games)
    part.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="failed SHA256 verification"):
        store.read_observations("games", 2024)


def test_latest_view_keeps_history_but_selects_newest_observation(tmp_path):
    identity = _identity(tmp_path)
    _, old = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata("2026-08-09T00:00:00+00:00"), identity=identity,
    )
    _, new = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata("2026-08-10T00:00:00+00:00"), identity=identity,
    )
    store = WNBANormalizedStore(tmp_path)
    store.write("games", 2024, old)
    store.write("games", 2024, new)
    assert store.read_season("games", 2024).height == 2
    latest = store.read_latest("games", 2024)
    assert latest.height == 1
    assert latest["observed_at_utc"][0] == "2026-08-10T00:00:00+00:00"


def test_audit_reports_missing_boxes_as_degraded_not_system_failure(tmp_path):
    _, games = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=_identity(tmp_path),
    )
    store = WNBANormalizedStore(tmp_path)
    store.write("games", 2024, games)
    report = audit_wnba_season(store, 2024)
    assert report["status"] == "DEGRADED"
    assert report["missing_team_boxscores"] == 1
    assert "not retrospective PIT evidence" in report["qualification_note"]


def test_audit_empty_season_is_unavailable_and_expectation_is_independent(tmp_path):
    store = WNBANormalizedStore(tmp_path)
    empty = audit_wnba_season(store, 2024)
    assert empty["status"] == "UNAVAILABLE"
    assert empty["games_expected"] is None
    assert empty["expected_games_basis"] == "UNAVAILABLE"

    _, games = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=_identity(tmp_path),
    )
    store.write("games", 2024, games)
    report = audit_wnba_season(store, 2024, expected_event_ids={"401000001", "missing"})
    assert report["games_expected"] == 2
    assert report["games_present"] == 1
    assert report["expected_events_missing"] == 1


def test_audit_counts_duplicate_observations_before_any_latest_view(tmp_path):
    _, games = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=_identity(tmp_path),
    )
    store = WNBANormalizedStore(tmp_path)
    store.write("games", 2024, games)
    duplicate = games.with_columns(pl.lit("later-ingest-only").alias("ingested_at_utc"))
    buffer = io.BytesIO()
    duplicate.write_parquet(buffer)
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    path = store.partition_dir("games", 2024) / f"part-{digest}.parquet"
    path.write_bytes(payload)

    report = audit_wnba_season(store, 2024)
    assert report["duplicate_events"] == 1
    assert report["status"] == "ERROR"


def test_foundation_and_live_collector_share_one_identity_registry_scheme(tmp_path):
    identity = _identity(tmp_path)
    _, games = normalize_wnba_table(
        "schedule", _schedule_frame(), _metadata(), identity=identity,
    )
    live_home, live_away = resolve_espn_scoreboard_team_ids(
        identity,
        "wnba",
        "espn_public",
        {"id": "1", "displayName": "Atlanta Dream"},
        {"id": "2", "displayName": "Connecticut Sun"},
        "2026-08-10T00:00:00+00:00",
    )
    assert live_home == games["home_team_canonical_id"][0]
    assert live_away == games["away_team_canonical_id"][0]


def test_team_box_contract_requires_feature_metrics_and_rejects_bad_numbers(tmp_path):
    identity = _identity(tmp_path)
    normalize_wnba_table("schedule", _schedule_frame(), _metadata(), identity=identity)
    row = {
        "game_id": "401000001",
        "season": 2024,
        "game_date_time": "2024-05-15T23:00:00+00:00",
        "team_id": "1",
        "team_display_name": "Home Example",
        "opponent_team_id": "2",
        "team_home_away": "home",
        "team_score": 82,
        "field_goals_made": 30,
        "field_goals_attempted": 70,
        "three_point_field_goals_made": 8,
        "three_point_field_goals_attempted": 22,
        "free_throws_made": 14,
        "free_throws_attempted": 18,
        "offensive_rebounds": 9,
        "defensive_rebounds": 27,
        "turnovers": 11,
    }
    table, normalized = normalize_wnba_table(
        "team_box", pl.DataFrame([row]), _metadata(), identity=identity,
    )
    assert table == "team_box"
    assert normalized["field_goals_attempted"][0] == 70
    assert normalized["opponent_team_canonical_id"][0] == identity.resolve(
        "espn_public:wnba", "2",
    ).entity_id

    row["turnovers"] = "not-a-number"
    with pytest.raises(ValueError, match="invalid WNBA integer"):
        normalize_wnba_table(
            "team_box", pl.DataFrame([row]), _metadata(), identity=identity,
        )


def test_foundation_manifest_is_deterministic_and_write_once(tmp_path):
    class ScheduleProvider:
        @staticmethod
        def season_table(table, season, *, force=False):
            assert table == "schedule"
            assert season == 2024
            return ProviderResult(
                ProviderStatus.AVAILABLE,
                _metadata(),
                _schedule_frame(),
            )

    foundation = WNBAFoundation(
        ScheduleProvider(),
        tmp_path / "normalized",
        _identity(tmp_path),
    )
    first = foundation.backfill([2024], tables=["schedule"])
    second = foundation.backfill([2024], tables=["schedule"])
    assert first == second
    manifests = list((tmp_path / "normalized/wnba/_manifests/season=2024").glob("*.json"))
    assert len(manifests) == 1
    payload = json.loads(manifests[0].read_text())
    assert payload["normalized_parts"]["games"]
    assert payload["errors"] == {}
