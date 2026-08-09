"""PIT forecast observations from Open-Meteo; never realized-weather backfill."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import polars as pl

from .base import ProviderResult, ProviderStatus, SourceGrade, SourceResponseMetadata, dataframe_schema_hash
from .cache import ProviderRawCache
from .http import HttpProviderClient

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
PARSER_VERSION = "open-meteo-forecast-v2"
HOURLY_VARIABLE_NAMES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation_probability",
    "surface_pressure",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
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
            lead_days = metadata.requested_parameters.get("fixed_lead_days")
            suffix = f"_previous_day{lead_days}" if lead_days is not None else ""
            required = {
                f"temperature_2m{suffix}",
                f"wind_speed_10m{suffix}",
                f"wind_direction_10m{suffix}",
            }
            missing = sorted(required - set(hourly))
            if missing:
                raise KeyError(f"missing required forecast variables: {missing}")
            rows = []
            for index, valid_time in enumerate(times):
                row = {"valid_time": valid_time}
                for key, values in hourly.items():
                    if key != "time" and isinstance(values, list):
                        canonical_key = key
                        if lead_days is not None and key.endswith(suffix):
                            canonical_key = key[: -len(suffix)]
                        row[canonical_key] = values[index]
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
        run_issued_at_utc: datetime | None = None,
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
            "hourly": ",".join(HOURLY_VARIABLE_NAMES),
            "timezone": "UTC",
        }
        cache_extras: dict[str, Any] = {}
        url = FORECAST_URL
        endpoint = "weather_forecast"
        source_version = "Open-Meteo forecast"
        if previous_run_hours is not None and run_issued_at_utc is not None:
            return ProviderResult.unavailable(
                "choose either a fixed previous-day lead or an exact single run, not both"
            )
        if previous_run_hours is not None:
            if previous_run_hours < 24 or previous_run_hours > 168 or previous_run_hours % 24:
                return ProviderResult.unavailable(
                    "Open-Meteo previous-runs supports fixed 24-hour leads only; "
                    "use run_issued_at_utc for exact T-6/T-60 reconstruction"
                )
            lead_days = previous_run_hours // 24
            cache_extras["fixed_lead_days"] = lead_days
            request_params["hourly"] = ",".join(
                f"{name}_previous_day{lead_days}" for name in HOURLY_VARIABLE_NAMES
            )
            url = PREVIOUS_RUNS_URL
            endpoint = "weather_previous_runs"
            source_version = f"Open-Meteo fixed lead previous day {lead_days}"
        elif run_issued_at_utc is not None:
            if run_issued_at_utc.tzinfo is None:
                return ProviderResult.unavailable("run_issued_at_utc must be timezone-aware")
            issued = run_issued_at_utc.astimezone(UTC)
            if issued > start.astimezone(UTC):
                return ProviderResult.unavailable("forecast run cannot be issued after requested valid time")
            request_params["run"] = issued.strftime("%Y-%m-%dT%H:%M")
            cache_extras["forecast_run_issued_at_utc"] = issued.isoformat()
            url = SINGLE_RUNS_URL
            endpoint = "weather_single_run"
            source_version = "Open-Meteo exact single run"
        cache_params = {**request_params, **cache_extras, "game_pk": game_pk}
        cached = self.cache.latest(self.provider_id, "mlb", endpoint, cache_params)
        if cached is not None and not force:
            if cached.metadata.http_status != 200:
                return ProviderResult.unavailable(
                    f"cached Open-Meteo response has HTTP {cached.metadata.http_status}",
                    cached.metadata,
                )
            try:
                result = self._parse(cached.read_bytes(), cached.metadata)
                self.cache.record_parse_result(
                    result.metadata or cached.metadata,
                    parser_version=PARSER_VERSION,
                    status=result.status.value,
                    schema_hash=(result.metadata or cached.metadata).schema_hash,
                    reason=result.reason,
                )
                return result
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
        result = self._parse(fetched.body, metadata)
        self.cache.record_parse_result(
            result.metadata or metadata,
            parser_version=PARSER_VERSION,
            status=result.status.value,
            schema_hash=(result.metadata or metadata).schema_hash,
            reason=result.reason,
        )
        return result
