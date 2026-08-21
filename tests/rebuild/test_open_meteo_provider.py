from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from model_prediction.rebuild.providers.base import ProviderStatus, assert_economic_use_allowed
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.open_meteo import OpenMeteoForecastProvider

FORECAST_PAYLOAD = {
    "hourly": {
        "time": ["2026-08-10T18:00", "2026-08-10T19:00"],
        "temperature_2m": [88.0, 87.0],
        "wind_speed_10m": [5.0, 6.0],
        "wind_direction_10m": [180, 190],
    }
}


def _http(handler) -> HttpProviderClient:
    return HttpProviderClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(attempts=1),
    )


def test_forecast_is_sport_neutral_raw_first_and_rights_gated(tmp_path):
    body = json.dumps(FORECAST_PAYLOAD).encode()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "api.open-meteo.com"
        return httpx.Response(200, content=body, request=request)

    provider = OpenMeteoForecastProvider(_http(handler), ProviderRawCache(tmp_path))
    start = datetime(2026, 8, 10, 18, tzinfo=UTC)
    end = datetime(2026, 8, 10, 21, tzinfo=UTC)
    first = provider.forecast(
        sport="nfl", latitude=39.9, longitude=-75.2, start=start, end=end, event_id="nfl-game-1"
    )
    second = provider.forecast(
        sport="nfl", latitude=39.9, longitude=-75.2, start=start, end=end, event_id="nfl-game-1"
    )

    assert first.status is ProviderStatus.AVAILABLE
    assert first.frame is not None and first.frame.height == 2
    assert first.metadata is not None
    assert first.metadata.sport == "nfl"
    assert first.metadata.attribution_required is True
    assert first.metadata.production_allowed is False
    with pytest.raises(PermissionError, match="not cleared"):
        assert_economic_use_allowed(first.metadata)
    assert second.metadata is not None and second.metadata.from_cache
    assert calls == 1


def test_naive_datetimes_are_rejected():
    provider = OpenMeteoForecastProvider(
        _http(lambda r: httpx.Response(200)), ProviderRawCache("/tmp/unused")
    )
    result = provider.forecast(
        sport="mlb",
        latitude=0.0,
        longitude=0.0,
        start=datetime(2026, 8, 10),  # noqa: DTZ001 -- deliberately naive, testing rejection
        end=datetime(2026, 8, 10, 3),  # noqa: DTZ001 -- deliberately naive, testing rejection
        event_id="x",
    )
    assert result.status is ProviderStatus.UNAVAILABLE
    assert "timezone-aware" in (result.reason or "")


def test_missing_required_variables_is_degraded(tmp_path):
    body = json.dumps({"hourly": {"time": ["2026-08-10T18:00"]}}).encode()
    provider = OpenMeteoForecastProvider(
        _http(lambda request: httpx.Response(200, content=body, request=request)),
        ProviderRawCache(tmp_path),
    )
    result = provider.forecast(
        sport="mlb",
        latitude=0.0,
        longitude=0.0,
        start=datetime(2026, 8, 10, 18, tzinfo=UTC),
        end=datetime(2026, 8, 10, 21, tzinfo=UTC),
        event_id="x",
    )
    assert result.status is ProviderStatus.DEGRADED
    assert "schema drift" in (result.reason or "")
