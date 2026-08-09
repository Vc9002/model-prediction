"""Optional raw-first football-data.org v4 soccer provider."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import date
from typing import Any, Final

import polars as pl

from .base import ProviderResult, ProviderStatus, SourceGrade, SourceResponseMetadata, dataframe_schema_hash
from .cache import ProviderRawCache
from .http import HttpProviderClient
from .soccer_rights import FOOTBALL_DATA_RIGHTS

FOOTBALL_DATA_MIN_INTERVAL_SECONDS: Final = 6.5
FOOTBALL_DATA_COMPETITIONS: Final = frozenset({"PL", "PD", "BL1", "SA", "FL1", "CL", "DED", "PPL"})


class FootballDataProvider:
    provider_id = "football_data_v4"
    rights = FOOTBALL_DATA_RIGHTS

    def __init__(
        self,
        http: HttpProviderClient,
        cache: ProviderRawCache,
        *,
        token: str | None = None,
    ) -> None:
        if http.min_interval_seconds < FOOTBALL_DATA_MIN_INTERVAL_SECONDS:
            raise ValueError("football-data.org transport must enforce at least 6.5 seconds between requests")
        self.http = http
        self.cache = cache
        self._token = (token if token is not None else os.getenv("FOOTBALL_DATA_TOKEN", "")).strip()

    def matches(
        self,
        competition: str,
        start: date,
        end: date,
        *,
        force: bool = False,
    ) -> ProviderResult:
        code = competition.strip().upper()
        if code not in FOOTBALL_DATA_COMPETITIONS:
            return ProviderResult.unavailable(f"unsupported football-data.org competition: {code}")
        if end < start:
            return ProviderResult.unavailable("football-data.org end date precedes start date")
        if not self._token:
            return ProviderResult.unavailable("TOKEN_NOT_CONFIGURED")
        parameters = {"competition": code, "dateFrom": start.isoformat(), "dateTo": end.isoformat()}
        endpoint = "competition_matches"
        cached = self.cache.latest(self.provider_id, "soccer", endpoint, parameters)
        if cached is not None and not force:
            return self._parse(cached.read_bytes(), cached.metadata)
        try:
            fetched = self.http.get(
                f"https://api.football-data.org/v4/competitions/{code}/matches",
                params={"dateFrom": start.isoformat(), "dateTo": end.isoformat()},
                headers={"X-Auth-Token": self._token},
            )
        except Exception as exc:  # noqa: BLE001 -- deliberately redact transport details/token
            return ProviderResult.unavailable(f"football-data.org transport failed ({type(exc).__name__})")
        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport="soccer",
            endpoint_family=endpoint,
            requested_parameters=parameters,
            request_time_utc=fetched.request_time_utc,
            retrieved_at_utc=fetched.retrieved_at_utc,
            observed_at_utc=fetched.retrieved_at_utc,
            http_status=fetched.status_code,
            content_hash=hashlib.sha256(fetched.body).hexdigest(),
            schema_hash=None,
            content_type=fetched.headers.get("content-type"),
            source_version="v4",
            source_grade=SourceGrade.A,
            **self.rights.metadata_kwargs(),
        )
        self.cache.store(metadata, fetched.body)
        if fetched.status_code != 200:
            reason = "TOKEN_REJECTED" if fetched.status_code in {401, 403} else f"HTTP_{fetched.status_code}"
            return ProviderResult.unavailable(reason, metadata)
        return self._parse(fetched.body, metadata)

    @staticmethod
    def _parse(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        try:
            payload = json.loads(body)
            matches = payload.get("matches")
            if not isinstance(matches, list):
                raise TypeError("payload lacks matches list")
            competition = payload.get("competition") or {}
            rows: list[dict[str, Any]] = []
            for match in matches:
                score = match.get("score") or {}
                full_time = score.get("fullTime") or {}
                home, away = match["homeTeam"], match["awayTeam"]
                rows.append(
                    {
                        "source_match_id": str(match["id"]),
                        "competition_id": str(
                            competition.get("code") or metadata.requested_parameters["competition"]
                        ),
                        "competition_name": str(
                            competition.get("name") or metadata.requested_parameters["competition"]
                        ),
                        "season_id": str((match.get("season") or {}).get("id") or "unknown"),
                        "event_start": str(match["utcDate"]),
                        "status": str(match.get("status") or "UNKNOWN"),
                        "completed": str(match.get("status")) == "FINISHED",
                        "home_team_id": str(home["id"]),
                        "home_team_name": str(home.get("name") or ""),
                        "away_team_id": str(away["id"]),
                        "away_team_name": str(away.get("name") or ""),
                        "home_score": full_time.get("home"),
                        "away_score": full_time.get("away"),
                        "venue_id": None,
                        "venue_name": None,
                        "provider_updated_at": match.get("lastUpdated"),
                    }
                )
            frame = (
                pl.DataFrame(rows)
                if rows
                else pl.DataFrame(
                    schema={
                        "source_match_id": pl.String,
                        "competition_id": pl.String,
                        "season_id": pl.String,
                        "event_start": pl.String,
                    }
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ProviderResult(
                ProviderStatus.DEGRADED, metadata, None, f"football-data.org schema drift: {exc}"
            )
        updated = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(
            ProviderStatus.AVAILABLE, updated, frame, "NO_MATCHES" if frame.is_empty() else None
        )
