"""Raw-first ESPN Site v2 soccer schedules from the SportsDataverse catalog."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from typing import Any, Final

import polars as pl

from .base import ProviderResult, ProviderStatus, SourceGrade, SourceResponseMetadata, dataframe_schema_hash
from .cache import ProviderRawCache
from .http import HttpProviderClient
from .soccer_rights import ESPN_SOCCER_RIGHTS

ESPN_SOCCER_LEAGUES: Final[dict[str, str]] = {
    "epl": "eng.1",
    "eng.1": "eng.1",
    "laliga": "esp.1",
    "esp.1": "esp.1",
    "bundesliga": "ger.1",
    "ger.1": "ger.1",
    "serie_a": "ita.1",
    "ita.1": "ita.1",
    "ligue_1": "fra.1",
    "fra.1": "fra.1",
    "mls": "usa.1",
    "usa.1": "usa.1",
}


class ESPNSoccerProvider:
    """Capture exact ESPN JSON before applying a small, strict parser."""

    provider_id = "espn_site_v2"
    rights = ESPN_SOCCER_RIGHTS

    def __init__(self, http: HttpProviderClient, cache: ProviderRawCache) -> None:
        self.http = http
        self.cache = cache

    @staticmethod
    def _league(value: str) -> str | None:
        return ESPN_SOCCER_LEAGUES.get(value.strip().lower())

    def current_schedule(
        self, game_date: date, league: str = "eng.1", *, force: bool = False
    ) -> ProviderResult:
        league_code = self._league(league)
        if league_code is None:
            return ProviderResult.unavailable(f"unsupported ESPN soccer league: {league}")
        parameters = {"dates": game_date.strftime("%Y%m%d"), "limit": 1000, "league": league_code}
        endpoint = "soccer_scoreboard"
        cached = self.cache.latest(self.provider_id, "soccer", endpoint, parameters)
        if cached is not None and not force:
            return self._parse(cached.read_bytes(), cached.metadata)

        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/scoreboard"
        try:
            fetched = self.http.get(url, params={"dates": parameters["dates"], "limit": 1000})
        except Exception as exc:  # noqa: BLE001 -- external transport boundary
            return ProviderResult.unavailable(f"ESPN soccer transport failed ({type(exc).__name__})")
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
            source_version="espn-site-v2",
            source_grade=SourceGrade.C,
            **self.rights.metadata_kwargs(),
        )
        # Raw bytes are durable even when HTTP status or parsing is bad.
        self.cache.store(metadata, fetched.body)
        if fetched.status_code != 200:
            return ProviderResult.unavailable(f"ESPN soccer returned HTTP {fetched.status_code}", metadata)
        return self._parse(fetched.body, metadata)

    @staticmethod
    def _parse(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        try:
            payload = json.loads(body)
            events = payload.get("events")
            if not isinstance(events, list):
                raise TypeError("payload lacks events list")
            rows: list[dict[str, Any]] = []
            for event in events:
                competition = event["competitions"][0]
                competitors = {item["homeAway"]: item for item in competition["competitors"]}
                home, away = competitors["home"], competitors["away"]
                status = competition.get("status", {}).get("type", {})
                league = event.get("league") or {}
                season = event.get("season") or {}
                venue = competition.get("venue") or {}
                rows.append(
                    {
                        "source_match_id": str(event["id"]),
                        "competition_id": str(league.get("slug") or metadata.requested_parameters["league"]),
                        "competition_name": str(
                            league.get("name") or metadata.requested_parameters["league"]
                        ),
                        "season_id": str(season.get("year") or "unknown"),
                        "event_start": str(event["date"]),
                        "status": str(status.get("name") or "UNKNOWN"),
                        "completed": bool(status.get("completed", False)),
                        "home_team_id": str(home["team"]["id"]),
                        "home_team_name": str(home["team"].get("displayName") or ""),
                        "away_team_id": str(away["team"]["id"]),
                        "away_team_name": str(away["team"].get("displayName") or ""),
                        "home_score": int(home["score"]) if home.get("score") not in (None, "") else None,
                        "away_score": int(away["score"]) if away.get("score") not in (None, "") else None,
                        "venue_id": str(venue.get("id") or "") or None,
                        "venue_name": venue.get("fullName"),
                        "provider_updated_at": None,
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
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"ESPN soccer schema drift: {exc}")
        updated = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(
            ProviderStatus.AVAILABLE, updated, frame, "NO_EVENTS" if frame.is_empty() else None
        )

    def events(self, *, sport: str, **kwargs: Any) -> ProviderResult:
        game_date = kwargs.get("date")
        if sport != "soccer" or not isinstance(game_date, date):
            return ProviderResult.unavailable("ESPN soccer events require sport=soccer and a date")
        return self.current_schedule(
            game_date, str(kwargs.get("league", "eng.1")), force=bool(kwargs.get("force"))
        )
