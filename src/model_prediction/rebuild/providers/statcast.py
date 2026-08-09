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

STATCAST_CSV_URL = "https://baseballsavant.mlb.com/statcast_search/csv"
MAX_STATCAST_DAYS = 7


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
        cached = self.cache.latest(self.provider_id, "mlb", "statcast_pitches", params)
        if cached is not None and not force:
            try:
                return self._parse(cached.read_bytes(), cached.metadata)
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
            commercial_use_status="baseball_savant_terms_review_required",
            production_allowed=False,
        )
        self.cache.store(metadata, fetched.body)
        if fetched.status_code != 200:
            return ProviderResult.unavailable(f"Statcast returned HTTP {fetched.status_code}", metadata)
        return self._parse(fetched.body, metadata)
