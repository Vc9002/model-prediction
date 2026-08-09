from __future__ import annotations

from datetime import date

import httpx
import pytest

from model_prediction.rebuild.providers.base import ProviderStatus, assert_economic_use_allowed
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.statcast import StatcastProvider

CSV_BODY = b"game_pk,game_date,at_bat_number,pitch_number\n745123,2026-08-05,1,1\n"


def _http(handler) -> HttpProviderClient:
    return HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=1),
    )


def test_pitches_are_raw_first_cached_and_rights_gated(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=CSV_BODY, request=request)

    provider = StatcastProvider(_http(handler), ProviderRawCache(tmp_path))
    first = provider.pitches(date(2026, 8, 1), date(2026, 8, 5))
    second = provider.pitches(date(2026, 8, 1), date(2026, 8, 5))

    assert first.status is ProviderStatus.AVAILABLE
    assert first.frame is not None and first.frame["game_pk"].to_list() == [745123]
    assert first.metadata is not None
    assert first.metadata.production_allowed is False
    with pytest.raises(PermissionError, match="not cleared"):
        assert_economic_use_allowed(first.metadata)
    assert second.metadata is not None and second.metadata.from_cache
    assert calls == 1


def test_range_over_seven_days_is_rejected_before_any_request(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=CSV_BODY, request=request)

    provider = StatcastProvider(_http(handler), ProviderRawCache(tmp_path))
    result = provider.pitches(date(2026, 8, 1), date(2026, 8, 10))
    assert result.status is ProviderStatus.UNAVAILABLE
    assert "partitioned" in (result.reason or "")
    assert calls == 0


def test_missing_required_columns_is_degraded(tmp_path):
    body = b"not_a_real_column\n1\n"
    provider = StatcastProvider(
        _http(lambda request: httpx.Response(200, content=body, request=request)),
        ProviderRawCache(tmp_path),
    )
    result = provider.pitches(date(2026, 8, 1), date(2026, 8, 2))
    assert result.status is ProviderStatus.DEGRADED
    assert "schema drift" in (result.reason or "")
