from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest

from model_prediction.rebuild.mlb_v3.audit import audit_mlb_v3
from model_prediction.rebuild.mlb_v3.boundary import MLBV3DataBoundary
from model_prediction.rebuild.mlb_v3.foundation import MLBV3Foundation
from model_prediction.rebuild.mlb_v3.normalize import (
    normalize_game_feed,
    normalize_schedule,
    normalize_statcast,
    normalize_transactions,
    normalize_weather,
)
from model_prediction.rebuild.mlb_v3.pit import latest_as_of
from model_prediction.rebuild.mlb_v3.store import MLBV3NormalizedStore
from model_prediction.rebuild.providers.base import (
    SourceGrade,
    SourceResponseMetadata,
    assert_production_use_allowed,
)
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.mlb_stats import MLBStatsProvider
from model_prediction.rebuild.providers.open_meteo import OpenMeteoForecastProvider
from model_prediction.rebuild.providers.statcast import StatcastProvider

FIXTURES = Path(__file__).parent / "fixtures" / "mlb_v3"


def _metadata(body: bytes, observed: datetime, *, endpoint: str, event: str | None = None) -> SourceResponseMetadata:
    timestamp = observed.astimezone(UTC).isoformat()
    return SourceResponseMetadata(
        provider="fixture",
        sport="mlb",
        endpoint_family=endpoint,
        requested_parameters={},
        request_time_utc=timestamp,
        retrieved_at_utc=timestamp,
        observed_at_utc=timestamp,
        http_status=200,
        content_hash=hashlib.sha256(body).hexdigest(),
        schema_hash=None,
        source_event_id=event,
        source_grade=SourceGrade.A,
    )


def _client(body: bytes, status: int = 200) -> HttpProviderClient:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=body, request=request))
    return HttpProviderClient(
        client=httpx.Client(transport=transport),
        retry=RetryPolicy(attempts=1),
        sleep=lambda _seconds: None,
    )


def test_schedule_keeps_game_pk_doubleheader_and_reschedule_identity(tmp_path):
    body = (FIXTURES / "schedule.json").read_bytes()
    provider = MLBStatsProvider(_client(body), ProviderRawCache(tmp_path / "raw"))
    result = provider.schedule(date(2026, 8, 9), date(2026, 8, 9))
    assert result.frame is not None and result.metadata is not None

    games = normalize_schedule(result.frame, result.metadata)
    assert games["game_pk"].to_list() == [900001, 900002, 900003]
    assert games["canonical_event_id"].n_unique() == 3
    assert games.filter(pl.col("game_pk") == 900002)["doubleheader_number"].item() == 2
    postponed = games.filter(pl.col("game_pk") == 900003).row(0, named=True)
    assert postponed["postponed"] is True
    assert postponed["rescheduled_from_date"] == "2026-08-09T00:10:00Z"
    assert postponed["reschedule_date"] == "2026-08-12T00:10:00Z"
    assert all(value == "capture_time_only" for value in games["availability_basis"])


def test_probable_pitcher_corrections_use_latest_observation_as_of(tmp_path):
    body = (FIXTURES / "game_feed.json").read_bytes()
    first_time = datetime(2026, 8, 9, 10, tzinfo=UTC)
    first = MLBStatsProvider._feed_rows(body, _metadata(body, first_time, endpoint="game_feed", event="900001"))
    assert first.frame is not None and first.metadata is not None
    first_rows = normalize_game_feed(first.frame, first.metadata)["probable_pitchers"]

    changed_payload = json.loads(body)
    changed_payload["gameData"]["probablePitchers"]["home"] = {"id": 699, "fullName": "Replacement"}
    changed_body = json.dumps(changed_payload).encode()
    second_time = first_time + timedelta(hours=2)
    second = MLBStatsProvider._feed_rows(
        changed_body,
        _metadata(changed_body, second_time, endpoint="game_feed", event="900001"),
    )
    assert second.frame is not None and second.metadata is not None
    second_rows = normalize_game_feed(second.frame, second.metadata)["probable_pitchers"]
    observations = pl.concat([first_rows, second_rows])

    before = latest_as_of(
        observations,
        entity_keys=["game_pk", "team_side"],
        decision_time_utc=first_time + timedelta(minutes=30),
    )
    after = latest_as_of(
        observations,
        entity_keys=["game_pk", "team_side"],
        decision_time_utc=second_time + timedelta(minutes=1),
    )
    assert before.filter(pl.col("team_side") == "home")["pitcher_id"].item() == "601"
    assert after.filter(pl.col("team_side") == "home")["pitcher_id"].item() == "699"


def test_game_feed_normalizes_confirmed_lineup_and_roster():
    body = (FIXTURES / "game_feed.json").read_bytes()
    metadata = _metadata(body, datetime(2026, 8, 9, 12, tzinfo=UTC), endpoint="game_feed", event="900001")
    result = MLBStatsProvider._feed_rows(body, metadata)
    assert result.frame is not None and result.metadata is not None
    outputs = normalize_game_feed(result.frame, result.metadata)
    assert outputs["lineups"].height == 2
    assert set(outputs["lineups"]["confirmation_state"]) == {"CONFIRMED"}
    assert outputs["rosters"].height == 2


def test_statcast_range_is_bounded_and_normalizes_capture_time(tmp_path):
    provider = StatcastProvider(
        _client((FIXTURES / "statcast.csv").read_bytes()),
        ProviderRawCache(tmp_path / "raw"),
    )
    rejected = provider.pitches(date(2026, 8, 1), date(2026, 8, 9))
    assert rejected.available is False
    assert "<= 7 days" in str(rejected.reason)

    result = provider.pitches(date(2026, 8, 9), date(2026, 8, 9))
    assert result.frame is not None and result.metadata is not None
    pitches = normalize_statcast(result.frame, result.metadata)
    assert pitches.height == 2
    assert pitches["canonical_event_id"].n_unique() == 1
    assert set(pitches["availability_basis"]) == {"capture_time_only"}
    assert result.metadata.production_allowed is False
    assert result.metadata.commercial_use_status == "baseball_savant_terms_review_required"


def test_weather_is_forecast_observation_not_realized_weather(tmp_path):
    body = (FIXTURES / "weather.json").read_bytes()
    provider = OpenMeteoForecastProvider(_client(body), ProviderRawCache(tmp_path / "raw"))
    result = provider.forecast(
        latitude=40.0,
        longitude=-75.0,
        start=datetime(2026, 8, 9, 16, tzinfo=UTC),
        end=datetime(2026, 8, 9, 20, tzinfo=UTC),
        game_pk=900001,
        previous_run_hours=6,
    )
    assert result.frame is not None and result.metadata is not None
    weather = normalize_weather(result.frame, result.metadata)
    assert weather.height == 2
    assert set(weather["weather_source_quality"]) == {"A_FORECAST_CAPTURE"}
    assert weather["forecast_issued_at_utc"].item(0) == result.metadata.observed_at_utc
    assert result.metadata.production_allowed is False
    assert "terms_review_required" in result.metadata.commercial_use_status


def test_transactions_keep_source_identity_and_capture_time():
    body = (FIXTURES / "transactions.json").read_bytes()
    metadata = _metadata(body, datetime(2026, 8, 9, 12, tzinfo=UTC), endpoint="transactions")
    result = MLBStatsProvider._transaction_rows(body, metadata)
    assert result.frame is not None and result.metadata is not None
    transactions = normalize_transactions(result.frame, result.metadata)
    assert transactions["transaction_id"].item() == "70001"
    assert transactions["player_id"].item() == "501"
    assert transactions["availability_basis"].item() == "capture_time_only"


def test_raw_bytes_are_stored_before_parse_failure(tmp_path):
    raw_root = tmp_path / "raw"
    provider = MLBStatsProvider(_client(b"not-json"), ProviderRawCache(raw_root))
    result = provider.schedule(date(2026, 8, 9), date(2026, 8, 9))
    assert result.status.value == "DEGRADED"
    assert list(raw_root.rglob("*.bin"))
    assert list(raw_root.rglob("*.json"))


def test_cache_hash_corruption_fails_closed_through_provider(tmp_path):
    body = (FIXTURES / "schedule.json").read_bytes()
    cache = ProviderRawCache(tmp_path / "raw")
    provider = MLBStatsProvider(_client(body), cache)
    first = provider.schedule(date(2026, 8, 9), date(2026, 8, 9))
    assert first.available
    blob = next((tmp_path / "raw").rglob("*.bin"))
    blob.write_bytes(b"corrupt")
    second = provider.schedule(date(2026, 8, 9), date(2026, 8, 9))
    assert second.status.value == "DEGRADED"
    assert "SHA256" in str(second.reason)


def test_normalized_store_is_content_addressed_and_audit_no_data_is_honest(tmp_path):
    store = MLBV3NormalizedStore(tmp_path / "normalized")
    assert audit_mlb_v3(store, 2026)["status"] == "NO_DATA"

    body = (FIXTURES / "schedule.json").read_bytes()
    result = MLBStatsProvider._schedule_rows(
        body,
        _metadata(body, datetime(2026, 8, 9, 8, tzinfo=UTC), endpoint="schedule"),
    )
    assert result.frame is not None and result.metadata is not None
    games = normalize_schedule(result.frame, result.metadata)
    first = store.write("games", 2026, games)
    second = store.write("games", 2026, games)
    assert first == second
    assert len(list(first.parent.glob("part-*.parquet"))) == 1
    assert audit_mlb_v3(store, 2026)["status"] == "DEGRADED"


def test_foundation_backfills_schedule_into_mlb_v3_namespace(tmp_path):
    body = (FIXTURES / "schedule.json").read_bytes()
    provider = MLBStatsProvider(_client(body), ProviderRawCache(tmp_path / "raw"))
    foundation = MLBV3Foundation(tmp_path / "normalized", mlb_stats=provider)
    report = foundation.backfill_mlb_stats(date(2026, 8, 9), date(2026, 8, 9))
    assert report["status"] == "AVAILABLE"
    assert report["row_counts"]["games"] == 3
    assert foundation.store.read("games", 2026).height == 3


def test_v3_data_boundary_rejects_v2_sealed_and_shadow_sources(tmp_path):
    boundary = MLBV3DataBoundary(tmp_path)
    allowed = tmp_path / "data" / "rebuild" / "normalized" / "mlb_v3" / "games.parquet"
    assert boundary.assert_read_path(allowed) == allowed.resolve()
    raw_allowed = tmp_path / "data" / "rebuild" / "raw" / "mlb_stats" / "mlb" / "schedule.json"
    assert boundary.assert_read_path(raw_allowed) == raw_allowed.resolve()
    with pytest.raises(PermissionError):
        boundary.assert_read_path(tmp_path / "outputs" / "rebuild" / "test_consumption_registry.json")
    with pytest.raises(PermissionError):
        boundary.assert_read_path(tmp_path / "data" / "rebuild" / "shadow.db")


def test_unresolved_public_provider_cannot_be_used_for_production(tmp_path):
    body = (FIXTURES / "schedule.json").read_bytes()
    result = MLBStatsProvider(_client(body), ProviderRawCache(tmp_path / "raw")).schedule(
        date(2026, 8, 9), date(2026, 8, 9)
    )
    assert result.metadata is not None
    assert result.metadata.production_allowed is False
    assert result.metadata.commercial_use_status == "mlb_stats_api_terms_review_required"
    with pytest.raises(PermissionError, match="not production-cleared"):
        assert_production_use_allowed(result.metadata)


def test_latest_as_of_rejects_timezone_naive_cutoff():
    with pytest.raises(ValueError, match="timezone-aware"):
        latest_as_of(
            pl.DataFrame({"game_pk": [1], "observed_at_utc": ["2026-01-01T00:00:00+00:00"], "pit_eligible": [True]}),
            entity_keys=["game_pk"],
            decision_time_utc=datetime(2026, 1, 1, tzinfo=None),  # noqa: DTZ001 - deliberate rejection case
        )


def test_mlb_v3_runtime_modules_do_not_import_v2_or_prospective_ledgers():
    package = Path(__file__).parents[2] / "src" / "model_prediction" / "rebuild" / "mlb_v3"
    forbidden = (
        "model_prediction.rebuild.mlb_shadow_pipeline",
        "model_prediction.rebuild.shadow_ledger",
        "test_consumption_registry.json",
    )
    for source in package.glob("*.py"):
        if source.name == "boundary.py":
            continue
        text = source.read_text()
        assert not any(token in text for token in forbidden), source
