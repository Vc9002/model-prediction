from __future__ import annotations

import json

import httpx
import pytest

from model_prediction.rebuild.providers.base import ProviderStatus, assert_economic_use_allowed
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.mlb_stats import MLBStatsProvider

SCHEDULE_PAYLOAD = {
    "dates": [
        {
            "games": [
                {
                    "gamePk": 745123,
                    "gameDate": "2026-08-10T23:10:00Z",
                    "officialDate": "2026-08-10",
                    "season": "2026",
                    "gameType": "R",
                    "gameNumber": 1,
                    "doubleHeader": "N",
                    "scheduledInnings": 9,
                    "status": {"abstractGameState": "Preview", "detailedState": "Scheduled", "statusCode": "S"},
                    "teams": {
                        "home": {"team": {"id": 111}, "probablePitcher": {"id": 55}},
                        "away": {"team": {"id": 121}, "probablePitcher": {"id": 66}},
                    },
                    "venue": {"id": 3},
                }
            ]
        }
    ]
}


def _http(handler) -> HttpProviderClient:
    return HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=1),
    )


def test_schedule_is_raw_first_cached_and_rights_gated(tmp_path):
    body = json.dumps(SCHEDULE_PAYLOAD).encode()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path.endswith("/v1/schedule")
        return httpx.Response(200, content=body, request=request)

    from datetime import date

    provider = MLBStatsProvider(_http(handler), ProviderRawCache(tmp_path))
    first = provider.schedule(date(2026, 8, 10), date(2026, 8, 10))
    second = provider.schedule(date(2026, 8, 10), date(2026, 8, 10))

    assert first.status is ProviderStatus.AVAILABLE
    assert first.frame is not None and first.frame["game_pk"].to_list() == [745123]
    assert first.metadata is not None
    assert first.metadata.commercial_use_status == "unresolved"
    assert first.metadata.production_allowed is False
    with pytest.raises(PermissionError, match="not cleared"):
        assert_economic_use_allowed(first.metadata)
    assert second.metadata is not None and second.metadata.from_cache
    assert calls == 1


def test_a_cached_server_error_does_not_block_a_later_real_retry(tmp_path):
    """Negative-cache-poisoning regression: a cached 500 must not stick forever."""
    from datetime import date

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, content=b"server error", request=request)
        return httpx.Response(200, content=json.dumps(SCHEDULE_PAYLOAD).encode(), request=request)

    provider = MLBStatsProvider(_http(handler), ProviderRawCache(tmp_path))
    first = provider.schedule(date(2026, 8, 10), date(2026, 8, 10))
    assert first.status is ProviderStatus.UNAVAILABLE
    assert calls == 1

    # A second call must retry the network (not silently reuse the cached
    # 500 forever) and succeed once the upstream recovers.
    second = provider.schedule(date(2026, 8, 10), date(2026, 8, 10))
    assert second.status is ProviderStatus.AVAILABLE
    assert calls == 2


def test_malformed_schedule_payload_is_degraded_but_raw_retained(tmp_path):
    from datetime import date

    body = b'{"no_dates": true}'
    cache = ProviderRawCache(tmp_path)
    provider = MLBStatsProvider(
        _http(lambda request: httpx.Response(200, content=body, request=request)), cache
    )
    result = provider.schedule(date(2026, 8, 10), date(2026, 8, 10))
    assert result.status is ProviderStatus.DEGRADED
    assert result.frame is None
    assert len(list(tmp_path.rglob("*.bin"))) == 1


def test_game_feed_identity_mismatch_fails_closed(tmp_path):
    payload = {
        "gameData": {"game": {"pk": 999}, "teams": {}, "probablePitchers": {}, "players": {}},
        "liveData": {"boxscore": {"teams": {}}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode(), request=request)

    provider = MLBStatsProvider(_http(handler), ProviderRawCache(tmp_path))
    result = provider.game_feed(123)  # requested 123, payload says 999
    assert result.status is ProviderStatus.DEGRADED
    assert "identity mismatch" in (result.reason or "")
