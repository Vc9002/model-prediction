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


def test_schedule_with_sparse_late_reschedule_values_does_not_crash(tmp_path):
    # Real bug, live-caught: a full real season's schedule has
    # reschedule_date/resume_date null for the overwhelming majority of
    # games, with a real ISO datetime string only for the rare suspended/
    # postponed game. Polars' list-of-dicts schema inference reads an early
    # row prefix; an all-null prefix infers a Null-typed column that then
    # crashes the instant it hits a real string value later in the same
    # batch (verified against a real 2023 full-season response: 2500+ games
    # with reschedule_date=None, then a real string value on game 718700).
    # This test mirrors that exact shape rather than a single-game fixture.
    games = [
        {
            "gamePk": 700000 + i,
            "gameDate": f"2026-04-{(i % 28) + 1:02d}T23:10:00Z",
            "officialDate": f"2026-04-{(i % 28) + 1:02d}",
            "season": "2026",
            "gameType": "R",
            "gameNumber": 1,
            "doubleHeader": "N",
            "scheduledInnings": 9,
            "status": {"abstractGameState": "Final", "detailedState": "Final", "statusCode": "F"},
            "teams": {
                "home": {"team": {"id": 111}, "probablePitcher": {"id": 55}},
                "away": {"team": {"id": 121}, "probablePitcher": {"id": 66}},
            },
            "venue": {"id": 3},
        }
        for i in range(500)
    ]
    games[499]["rescheduleDate"] = "2026-09-01T17:10:00Z"
    games[499]["gamePk"] = 718700
    payload = {"dates": [{"games": games}]}
    body = json.dumps(payload).encode()
    cache = ProviderRawCache(tmp_path)
    provider = MLBStatsProvider(
        _http(lambda request: httpx.Response(200, content=body, request=request)), cache
    )
    from datetime import date

    result = provider.schedule(date(2026, 4, 1), date(2026, 4, 28))
    assert result.status is ProviderStatus.AVAILABLE
    assert result.frame is not None
    assert result.frame.height == 500
    rescheduled = result.frame.filter(result.frame["reschedule_date"].is_not_null())
    assert rescheduled.height == 1
    assert rescheduled["game_pk"].item() == 718700
    assert rescheduled["reschedule_date"].item() == "2026-09-01T17:10:00Z"


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
