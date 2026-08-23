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


def test_aggregate_pitcher_metrics():
    import polars as pl

    df = pl.DataFrame(
        {
            "pitcher": [101, 101, 101, 101, 102],
            "pitch_type": ["FF", "FF", "SL", "FF", "CH"],
            "release_speed": [96.5, 97.0, 85.0, 96.0, 84.0],
            "description": ["swinging_strike", "called_strike", "foul", "ball", "swinging_strike"],
            "events": [None, None, "strikeout", None, "walk"],
            "estimated_woba_using_speedangle": [None, None, 0.0, None, 0.7],
        }
    )

    agg = StatcastProvider.aggregate_pitcher_metrics(df)
    assert len(agg) == 2
    row_101 = agg.filter(pl.col("pitcher") == 101).to_dicts()[0]
    assert row_101["total_pitches"] == 4
    # fastball velocity mean of 96.5, 97.0, 96.0 = 96.5
    assert pytest.approx(row_101["fastball_velocity_mean"], abs=0.01) == 96.50
    # CSW rate: 2 out of 4 (swinging_strike + called_strike) = 0.50
    assert pytest.approx(row_101["csw_rate"], abs=0.01) == 0.50
    # K-rate = 1.0, BB-rate = 0.0 -> K-BB = 1.0
    assert row_101["k_minus_bb_rate"] == 1.0
