from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest

from model_prediction.rebuild.mlb_v3.audit import audit_mlb_v3
from model_prediction.rebuild.mlb_v3.boundary import MLBV3DataBoundary, MLBV3GuardedRepository
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
    DataUseContext,
    ProviderResult,
    ProviderStatus,
    SourceGrade,
    SourceResponseMetadata,
    assert_economic_use_allowed,
    assert_frame_use_allowed,
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


def _store(tmp_path: Path, *, context: DataUseContext = DataUseContext.RESEARCH) -> MLBV3NormalizedStore:
    boundary = MLBV3DataBoundary(tmp_path)
    return MLBV3NormalizedStore(
        tmp_path / "data" / "rebuild" / "normalized",
        repository=MLBV3GuardedRepository(boundary),
        use_context=context,
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
    assert result.metadata.commercial_use_status == "unresolved"


def test_weather_is_forecast_observation_not_realized_weather(tmp_path):
    payload = json.loads((FIXTURES / "weather.json").read_text())
    payload["hourly"] = {
        (key if key == "time" else f"{key}_previous_day1"): value
        for key, value in payload["hourly"].items()
    }
    body = json.dumps(payload).encode()
    provider = OpenMeteoForecastProvider(_client(body), ProviderRawCache(tmp_path / "raw"))
    result = provider.forecast(
        sport="mlb",
        latitude=40.0,
        longitude=-75.0,
        start=datetime(2026, 8, 9, 16, tzinfo=UTC),
        end=datetime(2026, 8, 9, 20, tzinfo=UTC),
        event_id="900001",
        previous_run_hours=24,
    )
    assert result.frame is not None and result.metadata is not None
    weather = normalize_weather(result.frame, result.metadata)
    assert weather.height == 2
    assert set(weather["weather_source_quality"]) == {"A_FIXED_LEAD_24H"}
    assert weather["forecast_issued_at_utc"].item(0) == "2026-08-08T17:00:00+00:00"
    assert result.metadata.production_allowed is False
    assert result.metadata.commercial_use_status == "unresolved"


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
    store = _store(tmp_path)
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
    report = audit_mlb_v3(store, 2026)
    assert report["status"] == "DEGRADED"
    assert report["coverage"]["starters"]["coverage"] == 0.0


def test_foundation_backfills_schedule_into_mlb_v3_namespace(tmp_path):
    body = (FIXTURES / "schedule.json").read_bytes()
    provider = MLBStatsProvider(_client(body), ProviderRawCache(tmp_path / "raw"))
    foundation = MLBV3Foundation(
        tmp_path / "data" / "rebuild" / "normalized",
        repo_root=tmp_path,
        mlb_stats=provider,
    )
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
    assert result.metadata.commercial_use_status == "unresolved"
    with pytest.raises(PermissionError, match="not cleared for production/economic use"):
        assert_economic_use_allowed(result.metadata)


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
    for source in package.rglob("*.py"):
        if source.name == "boundary.py":
            continue
        text = source.read_text()
        assert not any(token in text for token in forbidden), source


@pytest.mark.parametrize(
    ("provider_kind", "body"),
    [
        ("mlb_stats", (FIXTURES / "schedule.json").read_bytes()),
        ("statcast", (FIXTURES / "statcast.csv").read_bytes()),
        ("open_meteo", (FIXTURES / "weather.json").read_bytes()),
    ],
)
def test_cached_non_200_can_never_be_reparsed_as_available(tmp_path, provider_kind, body):
    cache = ProviderRawCache(tmp_path / "raw")
    if provider_kind == "mlb_stats":
        provider = MLBStatsProvider(_client(body, 500), cache)
        first = provider.schedule(date(2026, 8, 9), date(2026, 8, 9))
        second = provider.schedule(date(2026, 8, 9), date(2026, 8, 9))
    elif provider_kind == "statcast":
        provider = StatcastProvider(_client(body, 500), cache)
        first = provider.pitches(date(2026, 8, 9), date(2026, 8, 9))
        second = provider.pitches(date(2026, 8, 9), date(2026, 8, 9))
    else:
        provider = OpenMeteoForecastProvider(_client(body, 500), cache)
        kwargs = {
            "sport": "mlb",
            "latitude": 40.0,
            "longitude": -75.0,
            "start": datetime(2026, 8, 9, 16, tzinfo=UTC),
            "end": datetime(2026, 8, 9, 20, tzinfo=UTC),
            "event_id": "900001",
        }
        first = provider.forecast(**kwargs)
        second = provider.forecast(**kwargs)
    assert first.status is ProviderStatus.UNAVAILABLE
    assert second.status is ProviderStatus.UNAVAILABLE
    assert second.metadata is not None and second.metadata.http_status == 500


def test_successful_parse_gets_immutable_schema_manifest(tmp_path):
    body = (FIXTURES / "schedule.json").read_bytes()
    provider = MLBStatsProvider(_client(body), ProviderRawCache(tmp_path / "raw"))
    result = provider.schedule(date(2026, 8, 9), date(2026, 8, 9))
    assert result.available
    parse_manifests = list((tmp_path / "raw").rglob("parse_results/*.json"))
    assert len(parse_manifests) == 1
    manifest = json.loads(parse_manifests[0].read_text())
    assert manifest["content_hash"] == result.metadata.content_hash
    assert manifest["parser_version"] == "mlb-stats-v1"
    assert manifest["schema_hash"] == result.metadata.schema_hash


def test_game_feed_payload_must_match_requested_game_pk(tmp_path):
    body = (FIXTURES / "game_feed.json").read_bytes()
    raw_root = tmp_path / "raw"
    result = MLBStatsProvider(_client(body), ProviderRawCache(raw_root)).game_feed(999999)
    assert result.status is ProviderStatus.DEGRADED
    assert "identity mismatch" in str(result.reason)
    assert result.frame is None
    assert list(raw_root.rglob("*.bin")), "raw evidence must still precede the rejected parse"


def test_store_rejects_conflicting_primary_key_before_persistence(tmp_path):
    store = _store(tmp_path)
    body = (FIXTURES / "schedule.json").read_bytes()
    result = MLBStatsProvider._schedule_rows(
        body,
        _metadata(body, datetime(2026, 8, 9, 8, tzinfo=UTC), endpoint="schedule"),
    )
    games = normalize_schedule(result.frame, result.metadata)
    store.write("games", 2026, games)
    conflict = games.with_columns(pl.lit("CONTRADICTORY").alias("status"))
    with pytest.raises(ValueError, match="conflicting MLB v3 games primary keys"):
        store.write("games", 2026, conflict)
    assert len(list(store.partition_dir("games", 2026).glob("part-*.parquet"))) == 1


def test_sparse_cross_game_coverage_is_never_healthy(tmp_path):
    store = _store(tmp_path)
    schedule_body = (FIXTURES / "schedule.json").read_bytes()
    schedule = MLBStatsProvider._schedule_rows(
        schedule_body,
        _metadata(schedule_body, datetime(2026, 8, 9, 8, tzinfo=UTC), endpoint="schedule"),
    )
    store.write("games", 2026, normalize_schedule(schedule.frame, schedule.metadata))
    feed_body = (FIXTURES / "game_feed.json").read_bytes()
    feed = MLBStatsProvider._feed_rows(
        feed_body,
        _metadata(feed_body, datetime(2026, 8, 9, 9, tzinfo=UTC), endpoint="game_feed", event="900001"),
    )
    starters = normalize_game_feed(feed.frame, feed.metadata)["probable_pitchers"]
    store.write("probable_pitchers", 2026, starters)
    statcast_body = (FIXTURES / "statcast.csv").read_bytes()
    statcast = StatcastProvider._parse(
        statcast_body,
        _metadata(statcast_body, datetime(2026, 8, 10, 9, tzinfo=UTC), endpoint="statcast"),
    )
    store.write("statcast_pitches", 2026, normalize_statcast(statcast.frame, statcast.metadata))
    report = audit_mlb_v3(store, 2026)
    assert report["status"] == "DEGRADED"
    assert report["coverage"]["starters"]["coverage"] == 0.5
    assert report["coverage"]["lineups"]["coverage"] == 0.0
    assert report["coverage"]["weather"]["coverage"] == 0.0


def test_weather_modes_fail_closed_and_never_conflate_capture_with_issue(tmp_path):
    body = (FIXTURES / "weather.json").read_bytes()
    provider = OpenMeteoForecastProvider(_client(body), ProviderRawCache(tmp_path / "raw"))
    common = {
        "sport": "mlb",
        "latitude": 40.0,
        "longitude": -75.0,
        "start": datetime(2026, 8, 9, 16, tzinfo=UTC),
        "end": datetime(2026, 8, 9, 20, tzinfo=UTC),
        "event_id": "900001",
    }
    unsupported = provider.forecast(**common, previous_run_hours=6)
    assert unsupported.status is ProviderStatus.UNAVAILABLE
    assert "exact T-6" in str(unsupported.reason)

    live = provider.forecast(**common)
    weather = normalize_weather(live.frame, live.metadata)
    assert weather["forecast_issued_at_utc"].null_count() == weather.height
    assert set(weather["weather_source_quality"]) == {"A_LIVE_CAPTURE_ISSUE_UNKNOWN"}

    exact_issue = datetime(2026, 8, 9, 6, tzinfo=UTC)
    exact = provider.forecast(**common, run_issued_at_utc=exact_issue, force=True)
    exact_weather = normalize_weather(exact.frame, exact.metadata)
    assert set(exact_weather["forecast_issued_at_utc"]) == {exact_issue.isoformat()}
    assert set(exact_weather["weather_source_quality"]) == {"A_EXACT_SINGLE_RUN"}


def test_delayed_is_not_postponed_and_suspended_resume_is_explicit():
    body = json.loads((FIXTURES / "schedule.json").read_text())
    delayed = body["dates"][0]["games"][0]
    delayed["status"] = {"abstractGameState": "Live", "detailedState": "Delayed", "statusCode": "D"}
    suspended = body["dates"][0]["games"][1]
    suspended["status"] = {"abstractGameState": "Live", "detailedState": "Suspended", "statusCode": "U"}
    suspended["resumeDate"] = "2026-08-10T17:05:00Z"
    encoded = json.dumps(body).encode()
    result = MLBStatsProvider._schedule_rows(
        encoded,
        _metadata(encoded, datetime(2026, 8, 9, 8, tzinfo=UTC), endpoint="schedule"),
    )
    games = normalize_schedule(result.frame, result.metadata)
    delayed_row = games.filter(pl.col("game_pk") == 900001).row(0, named=True)
    suspended_row = games.filter(pl.col("game_pk") == 900002).row(0, named=True)
    assert delayed_row["delayed"] is True and delayed_row["postponed"] is False
    assert suspended_row["suspended"] is True and suspended_row["resumed"] is True


def test_latest_as_of_parses_offsets_and_rejects_post_start_rows():
    frame = pl.DataFrame({
        "game_pk": [1],
        "observed_at_utc": ["2026-01-01T01:00:00+01:00"],
        "event_start_utc": ["2026-01-01T00:20:00+00:00"],
        "pit_eligible": [True],
    })
    before = latest_as_of(
        frame,
        entity_keys=["game_pk"],
        decision_time_utc=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
        event_start_column="event_start_utc",
    )
    assert before.height == 1
    after = latest_as_of(
        frame,
        entity_keys=["game_pk"],
        decision_time_utc=datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
        event_start_column="event_start_utc",
    )
    assert after.is_empty()
    naive = frame.with_columns(pl.lit("2026-01-01T00:00:00").alias("observed_at_utc"))
    with pytest.raises(ValueError, match="timezone-aware"):
        latest_as_of(
            naive,
            entity_keys=["game_pk"],
            decision_time_utc=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
        )


def test_unresolved_source_frame_is_research_only():
    frame = pl.DataFrame({"production_allowed": [False]})
    assert_frame_use_allowed(frame, DataUseContext.RESEARCH)
    with pytest.raises(PermissionError, match="SHADOW_ECONOMICS"):
        assert_frame_use_allowed(frame, DataUseContext.SHADOW_ECONOMICS)


def test_statcast_resume_uses_success_manifest_and_in_season_empty_degrades(tmp_path):
    body = (FIXTURES / "statcast.csv").read_bytes()
    metadata = _metadata(body, datetime(2026, 8, 10, 9, tzinfo=UTC), endpoint="statcast")
    parsed = StatcastProvider._parse(body, metadata)

    class CountingProvider:
        def __init__(self, result):
            self.result = result
            self.calls = 0

        def pitches(self, _start, _end, *, force=False):
            self.calls += 1
            return self.result

    provider = CountingProvider(parsed)
    foundation = MLBV3Foundation(
        tmp_path / "data" / "rebuild" / "normalized",
        repo_root=tmp_path,
        statcast=provider,
    )
    first = foundation.backfill_statcast(date(2026, 8, 9), date(2026, 8, 9))
    second = foundation.backfill_statcast(date(2026, 8, 9), date(2026, 8, 9))
    assert first["status"] == "AVAILABLE"
    assert second["skipped_successful_chunks"] == 1
    assert provider.calls == 1

    empty_frame = pl.DataFrame(
        schema={
            "game_pk": pl.Int64,
            "game_date": pl.String,
            "at_bat_number": pl.Int64,
            "pitch_number": pl.Int64,
        }
    )
    empty_provider = CountingProvider(ProviderResult(ProviderStatus.AVAILABLE, metadata, empty_frame, "NO_PITCHES"))
    empty_foundation = MLBV3Foundation(
        tmp_path / "empty" / "data" / "rebuild" / "normalized",
        repo_root=tmp_path / "empty",
        statcast=empty_provider,
    )
    empty_report = empty_foundation.backfill_statcast(date(2026, 7, 15), date(2026, 7, 15))
    assert empty_report["status"] == "DEGRADED"
    assert empty_report["errors"]["2026-07-15:2026-07-15"] == "UNEXPECTED_EMPTY_IN_SEASON"
