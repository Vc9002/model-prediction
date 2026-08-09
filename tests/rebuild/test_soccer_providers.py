from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from model_prediction.rebuild.providers.base import (
    ProviderStatus,
    SourceResponseMetadata,
    assert_economic_use_allowed,
)
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.football_data import FootballDataProvider
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.soccer_espn import ESPNSoccerProvider
from model_prediction.rebuild.providers.statsbomb_open import StatsBombOpenDataProvider

FIXTURES = Path(__file__).parent / "fixtures/providers/soccer"


def _http(handler, *, interval: float = 0.0) -> HttpProviderClient:
    return HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=1),
        min_interval_seconds=interval,
    )


def test_espn_scoreboard_is_captured_before_strict_parse(tmp_path):
    body = (FIXTURES / "espn_scoreboard.json").read_bytes()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path.endswith("/soccer/eng.1/scoreboard")
        return httpx.Response(200, content=body, request=request)

    provider = ESPNSoccerProvider(_http(handler), ProviderRawCache(tmp_path))
    first = provider.current_schedule(date(2026, 8, 10), "epl")
    second = provider.current_schedule(date(2026, 8, 10), "eng.1")

    assert first.status is ProviderStatus.AVAILABLE
    assert first.frame is not None and first.frame["source_match_id"].to_list() == ["700001"]
    assert second.metadata is not None and second.metadata.from_cache
    assert first.metadata is not None
    assert first.metadata.source_asset == "ESPN Site v2 soccer scoreboard"
    assert first.metadata.provider_chain == "SportsDataverse catalog -> ESPN Site v2"
    assert first.metadata.commercial_use_status == "unresolved"
    assert first.metadata.production_allowed is False
    assert second.metadata.to_dict() | {"from_cache": False} == first.metadata.to_dict()
    with pytest.raises(PermissionError, match="not cleared"):
        assert_economic_use_allowed(first.metadata)
    assert calls == 1
    assert len(list(tmp_path.rglob("*.bin"))) == 1
    manifest = json.loads(next(tmp_path.rglob("observations/*.json")).read_text())
    assert manifest["use_scope"] == "research_shadow_only"
    assert manifest["commercial_use_status"] == "unresolved"
    assert manifest["production_allowed"] is False


def test_espn_malformed_payload_is_degraded_but_raw_retained(tmp_path):
    body = b'{"not_events": []}'
    provider = ESPNSoccerProvider(
        _http(lambda request: httpx.Response(200, content=body, request=request)),
        ProviderRawCache(tmp_path),
    )
    result = provider.current_schedule(date(2026, 8, 10))
    assert result.status is ProviderStatus.DEGRADED
    assert result.frame is None
    assert len(list(tmp_path.rglob("*.bin"))) == 1


def test_football_data_requires_token_without_network_or_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATA_TOKEN", raising=False)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    provider = FootballDataProvider(_http(handler, interval=6.5), ProviderRawCache(tmp_path), token=None)
    result = provider.matches("PL", date(2026, 8, 9), date(2026, 8, 9))
    assert result.status is ProviderStatus.UNAVAILABLE
    assert result.reason == "TOKEN_NOT_CONFIGURED"
    assert calls == 0
    assert not list(tmp_path.rglob("*"))


def test_football_data_token_is_header_only_and_never_cached(tmp_path):
    body = (FIXTURES / "football_data_matches.json").read_bytes()
    token = "synthetic-secret-token"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Auth-Token"] == token
        assert token not in str(request.url)
        return httpx.Response(200, content=body, request=request)

    provider = FootballDataProvider(_http(handler, interval=6.5), ProviderRawCache(tmp_path), token=token)
    result = provider.matches("PL", date(2026, 8, 9), date(2026, 8, 9))
    assert result.status is ProviderStatus.AVAILABLE
    assert result.frame is not None and result.frame["home_score"].to_list() == [1]
    assert result.metadata is not None
    assert result.metadata.subscription_required is True
    assert result.metadata.subscription_scope == "single_application"
    assert result.metadata.attribution_required is True
    assert result.metadata.attribution_text == "Data provided by football-data.org"
    assert result.metadata.commercial_use_status == "unresolved"
    assert result.metadata.production_allowed is False
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert token.encode() not in path.read_bytes()


def test_football_data_rejects_transport_below_documented_policy(tmp_path):
    with __import__("pytest").raises(ValueError, match="6.5"):
        FootballDataProvider(
            _http(lambda request: httpx.Response(200, request=request), interval=6.49),
            ProviderRawCache(tmp_path),
            token="x",
        )


def test_statsbomb_is_explicitly_policy_blocked_and_has_no_network_surface():
    provider = StatsBombOpenDataProvider()
    result = provider.events(sport="soccer")
    assert result.status is ProviderStatus.POLICY_BLOCKED
    assert result.frame is None and result.metadata is None
    assert "no network request" in (result.reason or "")
    assert provider.rights.commercial_use_status == "prohibited"
    assert provider.rights.upstream_rights_status == "prohibited"
    assert provider.rights.use_scope == "policy_blocked"
    assert "derived analysis" in provider.rights.policy_note


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commercial_use_status", None),
        ("commercial_use_status", "unknown"),
        ("upstream_rights_status", None),
        ("upstream_rights_status", "unknown"),
        ("use_scope", None),
        ("use_scope", "unknown"),
        ("production_allowed", None),
    ],
)
def test_provider_metadata_rejects_null_or_unknown_rights(field, value):
    kwargs = {
        "provider": "fixture",
        "sport": "soccer",
        "endpoint_family": "events",
        "requested_parameters": {},
        "request_time_utc": "2026-08-10T00:00:00+00:00",
        "retrieved_at_utc": "2026-08-10T00:00:00+00:00",
        "observed_at_utc": "2026-08-10T00:00:00+00:00",
        "http_status": 200,
        "content_hash": "a" * 64,
        "schema_hash": "b" * 64,
        field: value,
    }
    with pytest.raises((TypeError, ValueError), match="explicit"):
        SourceResponseMetadata(**kwargs)


def test_unresolved_provider_cannot_claim_production_allowed():
    with pytest.raises(ValueError, match="requires cleared"):
        SourceResponseMetadata(
            provider="fixture",
            sport="soccer",
            endpoint_family="events",
            requested_parameters={},
            request_time_utc="2026-08-10T00:00:00+00:00",
            retrieved_at_utc="2026-08-10T00:00:00+00:00",
            observed_at_utc="2026-08-10T00:00:00+00:00",
            http_status=200,
            content_hash="a" * 64,
            schema_hash="b" * 64,
            production_allowed=True,
        )
