"""PIT forecast observations from Open-Meteo; never realized-weather backfill.

Sport-neutral by construction (lat/lon/event identity are caller-supplied) --
shared by every outdoor sport that needs pregame weather (MLB, NFL, soccer),
not MLB-specific despite originating in the MLB v3 research branch.
"""

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
from .rights import SourceRightsProfile

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

OPEN_METEO_RIGHTS = SourceRightsProfile(
    source_asset="Open-Meteo forecast / previous-runs / single-run hourly weather",
    provider_chain="api.open-meteo.com",
    license_id="open-meteo-attribution-and-terms-review-required",
    license_url="https://open-meteo.com/en/terms",
    attribution_required=True,
    attribution_text="Weather data by Open-Meteo.com",
    subscription_required=False,
    subscription_scope="none",
    upstream_rights_status="unresolved",
    commercial_use_status="unresolved",
    use_scope="research_shadow_only",
    production_allowed=False,
    policy_note=(
        "Open-Meteo's free tier requires attribution and is documented as "
        "non-commercial by default; commercial/production use requires a "
        "paid API key not yet provisioned or reviewed here."
    ),
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
        sport: str,
        latitude: float,
        longitude: float,
        start: datetime,
        end: datetime,
        event_id: str,
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
        cache_params = {**request_params, **cache_extras, "event_id": event_id}
        cached = self.cache.latest(self.provider_id, sport, endpoint, cache_params)
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
            sport=sport,
            endpoint_family=endpoint,
            requested_parameters=cache_params,
            request_time_utc=fetched.request_time_utc,
            retrieved_at_utc=fetched.retrieved_at_utc,
            observed_at_utc=fetched.retrieved_at_utc,
            http_status=fetched.status_code,
            content_hash=hashlib.sha256(fetched.body).hexdigest(),
            schema_hash=None,
            source_event_id=event_id,
            content_type=fetched.headers.get("content-type"),
            source_version=source_version,
            source_grade=SourceGrade.A,
            **OPEN_METEO_RIGHTS.metadata_kwargs(),
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
