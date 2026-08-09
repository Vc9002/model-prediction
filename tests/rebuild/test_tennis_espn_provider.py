from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from model_prediction.rebuild.providers.base import ProviderStatus, assert_economic_use_allowed
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.tennis_espn import ESPNTennisProvider

FIXTURES = Path(__file__).parent / "fixtures/providers/tennis"
SCOREBOARD_BODY = (FIXTURES / "espn_atp_scoreboard_sample.json").read_bytes()


def _http(handler) -> HttpProviderClient:
    return HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=1),
    )


def test_scoreboard_is_raw_first_cached_and_rights_gated(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path.endswith("/tennis/atp/scoreboard")
        return httpx.Response(200, content=SCOREBOARD_BODY, request=request)

    provider = ESPNTennisProvider(_http(handler), ProviderRawCache(tmp_path))
    first = provider.scoreboard("atp")
    second = provider.scoreboard("atp")

    assert first.status is ProviderStatus.AVAILABLE
    assert first.frame is not None and first.frame.height == 2  # two competitions in the fixture
    assert set(first.frame["tour"].to_list()) == {"atp"}
    assert first.metadata is not None
    assert first.metadata.production_allowed is False
    with pytest.raises(PermissionError, match="not cleared"):
        assert_economic_use_allowed(first.metadata)
    assert second.metadata is not None and second.metadata.from_cache
    assert calls == 1


def test_no_home_away_court_semantics_assumed(tmp_path):
    """Real invariant: ESPN's homeAway is preserved raw, never reinterpreted."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SCOREBOARD_BODY, request=request)

    provider = ESPNTennisProvider(_http(handler), ProviderRawCache(tmp_path))
    result = provider.scoreboard("atp")
    assert result.frame is not None
    row = result.frame.row(0, named=True)
    assert row["competitor_1_espn_home_away"] in ("home", "away")
    assert row["competitor_2_espn_home_away"] in ("home", "away")
    assert row["competitor_1_id"] and row["competitor_2_id"]
    assert row["competitor_1_id"] != row["competitor_2_id"]


def test_exactly_one_winner_per_completed_match(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SCOREBOARD_BODY, request=request)

    provider = ESPNTennisProvider(_http(handler), ProviderRawCache(tmp_path))
    result = provider.scoreboard("atp")
    assert result.frame is not None
    for row in result.frame.iter_rows(named=True):
        if row["status_completed"]:
            assert row["competitor_1_winner"] != row["competitor_2_winner"]


def test_malformed_payload_is_degraded_but_raw_retained(tmp_path):
    body = b'{"not_events": []}'
    cache = ProviderRawCache(tmp_path)
    provider = ESPNTennisProvider(
        _http(lambda request: httpx.Response(200, content=body, request=request)), cache
    )
    result = provider.scoreboard("wta")
    assert result.status is ProviderStatus.DEGRADED
    assert result.frame is None
    assert len(list(tmp_path.rglob("*.bin"))) == 1


def test_events_dispatch_requires_sport_tennis_and_valid_tour():
    provider = ESPNTennisProvider(_http(lambda r: httpx.Response(200)), ProviderRawCache("/tmp/unused"))
    result = provider.events(sport="soccer", tour="atp")
    assert result.status is ProviderStatus.UNAVAILABLE

    result2 = provider.events(sport="tennis", tour="not-a-tour")
    assert result2.status is ProviderStatus.UNAVAILABLE


def test_a_cached_server_error_does_not_block_a_later_retry(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, content=b"error", request=request)
        return httpx.Response(200, content=SCOREBOARD_BODY, request=request)

    provider = ESPNTennisProvider(_http(handler), ProviderRawCache(tmp_path))
    first = provider.scoreboard("atp")
    assert first.status is ProviderStatus.UNAVAILABLE
    second = provider.scoreboard("atp")
    assert second.status is ProviderStatus.AVAILABLE
    assert calls == 2
