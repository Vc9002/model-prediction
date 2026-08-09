"""PIT forecast observations from Open-Meteo; never realized-weather backfill."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from typing import Any

import polars as pl

from .base import ProviderResult, ProviderStatus, SourceGrade, SourceResponseMetadata, dataframe_schema_hash
from .cache import ProviderRawCache
from .http import HttpProviderClient

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
HOURLY_VARIABLES = (
    "temperature_2m,relative_humidity_2m,precipitation_probability,"
    "surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
)


class OpenMeteoForecastProvider:
    provider_id = "open_meteo"

    def __init__(self, http: HttpProviderClient, cache: ProviderRawCache) -> None:
        self.http = http
        self.cache = cache

    @staticmethod
    def _parse(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        try:
            payload = json.loads(body)
            hourly = payload["hourly"]
            times = hourly["time"]
            if not isinstance(times, list):
                raise TypeError("hourly.time is not a list")
            rows = []
            for index, valid_time in enumerate(times):
                row = {"valid_time": valid_time}
                for key, values in hourly.items():
                    if key != "time" and isinstance(values, list):
                        row[key] = values[index]
                rows.append(row)
            frame = pl.DataFrame(rows) if rows else pl.DataFrame(schema={"valid_time": pl.String})
        except (KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"Open-Meteo schema drift: {exc}")
        enriched = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(ProviderStatus.AVAILABLE, enriched, frame, "NO_FORECAST_HOURS" if frame.is_empty() else None)

    def forecast(
        self,
        *,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
        game_pk: int,
        previous_run_hours: int | None = None,
        force: bool = False,
    ) -> ProviderResult:
        if start.tzinfo is None or end.tzinfo is None:
            return ProviderResult.unavailable("forecast start/end must be timezone-aware")
        if end < start:
            return ProviderResult.unavailable("forecast end precedes start")
        request_params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
            "hourly": HOURLY_VARIABLES,
            "timezone": "UTC",
        }
        url = FORECAST_URL
        endpoint = "weather_forecast"
        source_version = "Open-Meteo forecast"
        if previous_run_hours is not None:
            if previous_run_hours < 1:
                return ProviderResult.unavailable("previous_run_hours must be positive")
            request_params["past_hours"] = previous_run_hours
            url = PREVIOUS_RUNS_URL
            endpoint = "weather_previous_runs"
            source_version = "Open-Meteo previous runs"
        cache_params = {**request_params, "game_pk": game_pk}
        cached = self.cache.latest(self.provider_id, "mlb", endpoint, cache_params)
        if cached is not None and not force:
            try:
                return self._parse(cached.read_bytes(), cached.metadata)
            except Exception as exc:  # noqa: BLE001
                return ProviderResult(ProviderStatus.DEGRADED, cached.metadata, None, f"cached parse failed: {exc}")
        try:
            fetched = self.http.get(url, params=request_params)
        except Exception as exc:  # noqa: BLE001
            return ProviderResult.unavailable(f"Open-Meteo request failed: {exc}")
        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport="mlb",
            endpoint_family=endpoint,
            requested_parameters=cache_params,
            request_time_utc=fetched.request_time_utc,
            retrieved_at_utc=fetched.retrieved_at_utc,
            observed_at_utc=fetched.retrieved_at_utc,
            http_status=fetched.status_code,
            content_hash=hashlib.sha256(fetched.body).hexdigest(),
            schema_hash=None,
            source_event_id=str(game_pk),
            content_type=fetched.headers.get("content-type"),
            source_version=source_version,
            source_grade=SourceGrade.A,
            commercial_use_status="open_meteo_attribution_and_terms_review_required",
            production_allowed=False,
        )
        self.cache.store(metadata, fetched.body)
        if fetched.status_code != 200:
            return ProviderResult.unavailable(f"Open-Meteo returned HTTP {fetched.status_code}", metadata)
        return self._parse(fetched.body, metadata)
