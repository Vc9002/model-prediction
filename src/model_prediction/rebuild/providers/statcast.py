"""Bounded raw-first Baseball Savant Statcast CSV access."""

from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from datetime import date

import polars as pl

from .base import ProviderResult, ProviderStatus, SourceGrade, SourceResponseMetadata, dataframe_schema_hash
from .cache import ProviderRawCache
from .http import HttpProviderClient
from .rights import SourceRightsProfile

STATCAST_CSV_URL = "https://baseballsavant.mlb.com/statcast_search/csv"
MAX_STATCAST_DAYS = 7
PARSER_VERSION = "statcast-csv-v1"

STATCAST_RIGHTS = SourceRightsProfile(
    source_asset="Baseball Savant Statcast pitch-level CSV export",
    provider_chain="baseballsavant.mlb.com",
    license_id="baseball-savant-terms-review-required",
    license_url="https://baseballsavant.mlb.com/about",
    attribution_required=False,
    attribution_text=None,
    subscription_required=False,
    subscription_scope="none",
    upstream_rights_status="unresolved",
    commercial_use_status="unresolved",
    use_scope="research_shadow_only",
    production_allowed=False,
    policy_note=(
        "Public CSV export reachability does not grant commercial rights; "
        "terms of use for economic/production consumption have not been "
        "affirmatively reviewed."
    ),
)


class StatcastProvider:
    provider_id = "baseball_savant"

    def __init__(self, http: HttpProviderClient, cache: ProviderRawCache) -> None:
        self.http = http
        self.cache = cache

    @staticmethod
    def _validate_range(start: date, end: date) -> None:
        if end < start:
            raise ValueError("Statcast end precedes start")
        if (end - start).days + 1 > MAX_STATCAST_DAYS:
            raise ValueError(f"Statcast requests must be partitioned to <= {MAX_STATCAST_DAYS} days")

    @staticmethod
    def _parse(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        try:
            frame = pl.read_csv(io.BytesIO(body), infer_schema_length=10000, null_values=["null", ""])
        except Exception as exc:  # noqa: BLE001 - parser boundary
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"Statcast CSV parse failed: {exc}")
        required = {"game_pk", "game_date", "at_bat_number", "pitch_number"}
        missing = sorted(required - set(frame.columns))
        if missing:
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"Statcast schema drift: missing {missing}")
        return ProviderResult(
            ProviderStatus.AVAILABLE,
            replace(metadata, schema_hash=dataframe_schema_hash(frame)),
            frame,
            "NO_PITCHES" if frame.is_empty() else None,
        )

    def pitches(self, start: date, end: date, *, force: bool = False) -> ProviderResult:
        try:
            self._validate_range(start, end)
        except ValueError as exc:
            return ProviderResult.unavailable(str(exc))
        params = {
            "type": "details",
            "game_date_gt": start.isoformat(),
            "game_date_lt": end.isoformat(),
            "player_type": "batter",
        }
        cached = self.cache.latest_success(self.provider_id, "mlb", "statcast_pitches", params)
        if cached is not None and not force:
            if cached.metadata.http_status != 200:
                return ProviderResult.unavailable(
                    f"cached Statcast response has HTTP {cached.metadata.http_status}",
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
            except Exception as exc:  # noqa: BLE001 - cache boundary
                return ProviderResult(ProviderStatus.DEGRADED, cached.metadata, None, f"cached parse failed: {exc}")
        try:
            fetched = self.http.get(STATCAST_CSV_URL, params=params)
        except Exception as exc:  # noqa: BLE001 - network boundary
            return ProviderResult.unavailable(f"Statcast request failed: {exc}")
        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport="mlb",
            endpoint_family="statcast_pitches",
            requested_parameters=params,
            request_time_utc=fetched.request_time_utc,
            retrieved_at_utc=fetched.retrieved_at_utc,
            observed_at_utc=fetched.retrieved_at_utc,
            http_status=fetched.status_code,
            content_hash=hashlib.sha256(fetched.body).hexdigest(),
            schema_hash=None,
            content_type=fetched.headers.get("content-type"),
            source_version="Baseball Savant CSV",
            source_grade=SourceGrade.A,
            **STATCAST_RIGHTS.metadata_kwargs(),
        )
        self.cache.store(metadata, fetched.body)
        if fetched.status_code != 200:
            return ProviderResult.unavailable(f"Statcast returned HTTP {fetched.status_code}", metadata)
        result = self._parse(fetched.body, metadata)
        self.cache.record_parse_result(
            result.metadata or metadata,
            parser_version=PARSER_VERSION,
            status=result.status.value,
            schema_hash=(result.metadata or metadata).schema_hash,
            reason=result.reason,
        )
        return result
