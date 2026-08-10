from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from model_prediction.rebuild.providers.base import SourceGrade, SourceResponseMetadata
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.football_data import FOOTBALL_DATA_RIGHTS
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.soccer_espn import ESPNSoccerProvider
from model_prediction.rebuild.soccer.audit import audit_soccer_data
from model_prediction.rebuild.soccer.foundation import SoccerFoundation
from model_prediction.rebuild.soccer.normalize import normalize_soccer_matches
from model_prediction.rebuild.soccer.pit import eligible_matches_as_of, prior_team_matches_as_of
from model_prediction.rebuild.soccer.rights import (
    assert_economic_use_allowed,
    assert_research_shadow_allowed,
)
from model_prediction.rebuild.soccer.store import SoccerNormalizedStore

FIXTURE = Path(__file__).parent / "fixtures/providers/soccer/football_data_matches.json"
ESPN_FIXTURE = Path(__file__).parent / "fixtures/providers/soccer/espn_scoreboard.json"


def _metadata(observed: str) -> SourceResponseMetadata:
    return SourceResponseMetadata(
        provider="football_data_v4",
        sport="soccer",
        endpoint_family="competition_matches",
        requested_parameters={"competition": "PL"},
        request_time_utc=observed,
        retrieved_at_utc=observed,
        observed_at_utc=observed,
        http_status=200,
        content_hash="a" * 64,
        schema_hash="b" * 64,
        source_version="v4",
        source_grade=SourceGrade.A,
        **FOOTBALL_DATA_RIGHTS.metadata_kwargs(),
    )


def _source_frame() -> pl.DataFrame:
    match = json.loads(FIXTURE.read_text())["matches"][0]
    return pl.DataFrame(
        [
            {
                "source_match_id": str(match["id"]),
                "competition_id": "PL",
                "competition_name": "Premier League",
                "season_id": str(match["season"]["id"]),
                "event_start": match["utcDate"],
                "status": match["status"],
                "completed": True,
                "home_team_id": str(match["homeTeam"]["id"]),
                "home_team_name": match["homeTeam"]["name"],
                "away_team_id": str(match["awayTeam"]["id"]),
                "away_team_name": match["awayTeam"]["name"],
                "home_score": 1,
                "away_score": 1,
                "venue_id": None,
                "venue_name": None,
                "provider_updated_at": match["lastUpdated"],
            }
        ]
    )


def test_normalization_preserves_draw_and_uses_capture_time_availability():
    frame = normalize_soccer_matches(_source_frame(), _metadata("2026-08-10T00:00:00+00:00"))
    assert frame["home_score"].to_list() == [1]
    assert frame["away_score"].to_list() == [1]
    assert frame["observed_at_utc"][0] == frame["available_at_utc"][0]
    assert frame["provider_updated_at_utc"][0] == "2026-08-09T17:05:00+00:00"
    assert frame["availability_basis"][0] == "capture_time_only"
    assert frame["source_asset"][0] == "football-data.org API v4 competition matches"
    assert frame["subscription_required"][0] is True
    assert frame["subscription_scope"][0] == "single_application"
    assert frame["attribution_text"][0] == "Data provided by football-data.org"
    assert frame["commercial_use_status"][0] == "unresolved"
    assert frame["production_allowed"][0] is False
    assert frame["schema_version"][0] == "2"
    assert_research_shadow_allowed(frame)
    with pytest.raises(PermissionError, match="not cleared"):
        assert_economic_use_allowed(frame)


def test_backfilled_result_is_not_available_to_earlier_decision():
    frame = normalize_soccer_matches(_source_frame(), _metadata("2026-08-10T00:00:00+00:00"))
    result = eligible_matches_as_of(frame, datetime(2026, 8, 9, 18, tzinfo=UTC), completed_only=True)
    assert result.is_empty()


def test_latest_observation_as_of_is_selected_without_future_revision():
    first = normalize_soccer_matches(_source_frame(), _metadata("2026-08-10T00:00:00+00:00"))
    revised_source = _source_frame().with_columns(pl.lit(2).alias("home_score"))
    revised = normalize_soccer_matches(revised_source, _metadata("2026-08-11T00:00:00+00:00"))
    observations = pl.concat([first, revised])
    early = eligible_matches_as_of(observations, datetime(2026, 8, 10, 12, tzinfo=UTC))
    late = eligible_matches_as_of(observations, datetime(2026, 8, 11, 12, tzinfo=UTC))
    assert early["home_score"].to_list() == [1]
    assert late["home_score"].to_list() == [2]


def test_prior_team_matches_requires_completed_and_pre_decision_event():
    frame = normalize_soccer_matches(_source_frame(), _metadata("2026-08-10T00:00:00+00:00"))
    result = prior_team_matches_as_of(
        frame,
        team_id="10",
        decision_time_utc=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )
    assert result.height == 1


def test_naive_timestamp_fails_closed():
    source = _source_frame().with_columns(pl.lit("2026-08-09T15:00:00").alias("event_start"))
    with pytest.raises(ValueError, match="timezone-naive"):
        normalize_soccer_matches(source, _metadata("2026-08-10T00:00:00+00:00"))


def test_store_keeps_distinct_observations_and_is_content_idempotent(tmp_path):
    first = normalize_soccer_matches(_source_frame(), _metadata("2026-08-10T00:00:00+00:00"))
    second = normalize_soccer_matches(_source_frame(), _metadata("2026-08-11T00:00:00+00:00"))
    store = SoccerNormalizedStore(tmp_path)
    first_paths = store.write_matches(first)
    assert store.write_matches(first) == first_paths
    store.write_matches(second)
    assert len(list(tmp_path.rglob("part-*.parquet"))) == 2
    assert store.read_matches().height == 2


def test_foundation_persists_espn_and_reports_statsbomb_policy_block(tmp_path):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=ESPN_FIXTURE.read_bytes(), request=request)
    )
    provider = ESPNSoccerProvider(
        HttpProviderClient(client=httpx.Client(transport=transport), retry=RetryPolicy(attempts=1)),
        ProviderRawCache(tmp_path / "raw"),
    )
    foundation = SoccerFoundation(provider, tmp_path / "normalized")
    report = foundation.collect_date(date(2026, 8, 10))

    assert report["sources"][0]["rows_written"] == 1
    assert report["sources"][0]["commercial_use_status"] == "unresolved"
    assert report["sources"][0]["production_allowed"] is False
    assert report["sources"][-1]["status"] == "POLICY_BLOCKED"
    assert report["sources"][-1]["commercial_use_status"] == "prohibited"
    assert report["sources"][-1]["production_allowed"] is False
    assert report["use_scope"] == "research_shadow_only"
    assert report["economic_use_allowed"] is False
    manifest = json.loads(Path(report["manifest"]).read_text())
    assert manifest["production_allowed"] is False
    assert manifest["sources"][0]["source_asset"] == "ESPN Site v2 soccer scoreboard"
    assert manifest["sources"][-1]["use_scope"] == "policy_blocked"
    assert foundation.store.read_matches().height == 1

    audit = foundation.audit()
    assert audit["status"] == "DEGRADED"
    assert audit["rights_policy_valid"] is True
    assert audit["economic_use_allowed"] is False
    assert audit["production_allowed"] is False
    assert audit["source_assets"][0]["commercial_use_status"] == "unresolved"


@pytest.mark.parametrize(
    ("column", "value", "error"),
    [
        ("commercial_use_status", None, ValueError),
        ("commercial_use_status", "unknown", ValueError),
        ("upstream_rights_status", None, ValueError),
        ("upstream_rights_status", "unknown", ValueError),
        ("use_scope", None, ValueError),
        ("use_scope", "unknown", ValueError),
        ("production_allowed", None, ValueError),
        ("production_allowed", True, PermissionError),
    ],
)
def test_normalized_rights_reject_null_unknown_or_production_claim(column, value, error):
    frame = normalize_soccer_matches(
        _source_frame(), _metadata("2026-08-10T00:00:00+00:00")
    ).with_columns(pl.lit(value).alias(column))
    with pytest.raises(error):
        eligible_matches_as_of(frame, datetime(2026, 8, 10, 12, tzinfo=UTC))


def test_audit_fails_closed_when_normalized_rights_are_tampered(tmp_path):
    frame = normalize_soccer_matches(_source_frame(), _metadata("2026-08-10T00:00:00+00:00"))
    store = SoccerNormalizedStore(tmp_path)
    store.write_matches(frame)
    parquet = next(tmp_path.rglob("part-*.parquet"))
    frame.with_columns(pl.lit("unknown").alias("commercial_use_status")).write_parquet(parquet)

    report = audit_soccer_data(store)
    assert report["status"] == "ERROR"
    assert report["rights_policy_valid"] is False
    assert report["economic_use_allowed"] is False
