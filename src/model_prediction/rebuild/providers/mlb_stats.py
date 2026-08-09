"""Raw-first MLB Stats API access for MLB v3 research.

This module intentionally stops at source records.  Feature construction must
consume the normalized PIT store and must never make an API call.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from typing import Any

import polars as pl

from .base import ProviderResult, ProviderStatus, SourceGrade, SourceResponseMetadata, dataframe_schema_hash
from .cache import ProviderRawCache
from .http import HttpProviderClient

MLB_STATS_BASE = "https://statsapi.mlb.com/api"


class MLBStatsProvider:
    provider_id = "mlb_stats"

    def __init__(self, http: HttpProviderClient, cache: ProviderRawCache) -> None:
        self.http = http
        self.cache = cache

    def _json_endpoint(
        self,
        *,
        endpoint_family: str,
        url: str,
        parameters: dict[str, Any],
        parser: Any,
        source_event_id: str | None = None,
        force: bool = False,
    ) -> ProviderResult:
        cached = self.cache.latest(self.provider_id, "mlb", endpoint_family, parameters)
        if cached is not None and not force:
            try:
                return parser(cached.read_bytes(), cached.metadata)
            except Exception as exc:  # noqa: BLE001 - source parser boundary
                return ProviderResult(ProviderStatus.DEGRADED, cached.metadata, None, f"cached parse failed: {exc}")
        try:
            fetched = self.http.get(url, params=parameters)
        except Exception as exc:  # noqa: BLE001 - network boundary
            return ProviderResult.unavailable(f"MLB Stats request failed: {exc}")
        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport="mlb",
            endpoint_family=endpoint_family,
            requested_parameters=parameters,
            request_time_utc=fetched.request_time_utc,
            retrieved_at_utc=fetched.retrieved_at_utc,
            observed_at_utc=fetched.retrieved_at_utc,
            http_status=fetched.status_code,
            content_hash=hashlib.sha256(fetched.body).hexdigest(),
            schema_hash=None,
            source_event_id=source_event_id,
            content_type=fetched.headers.get("content-type"),
            source_version="MLB Stats API v1",
            source_grade=SourceGrade.A,
            commercial_use_status="mlb_stats_api_terms_review_required",
            production_allowed=False,
        )
        # The exact bytes are durable before any parser sees them.  A broken
        # schema therefore remains inspectable evidence rather than vanishing.
        self.cache.store(metadata, fetched.body)
        if fetched.status_code != 200:
            return ProviderResult.unavailable(f"MLB Stats returned HTTP {fetched.status_code}", metadata)
        try:
            return parser(fetched.body, metadata)
        except Exception as exc:  # noqa: BLE001 - source parser boundary
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"MLB Stats schema drift: {exc}")

    @staticmethod
    def _schedule_rows(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        payload = json.loads(body)
        dates = payload.get("dates")
        if not isinstance(dates, list):
            raise TypeError("schedule payload lacks dates list")
        rows: list[dict[str, Any]] = []
        for date_group in dates:
            for game in date_group.get("games", []):
                teams = game.get("teams", {})
                home = teams.get("home", {})
                away = teams.get("away", {})
                status = game.get("status", {})
                venue = game.get("venue") or {}
                rows.append({
                    "game_pk": int(game["gamePk"]),
                    "game_date": game["gameDate"],
                    "official_date": game.get("officialDate"),
                    "season": int(game.get("season") or str(game.get("officialDate", "0000"))[:4]),
                    "game_type": game.get("gameType"),
                    "game_number": int(game.get("gameNumber") or 1),
                    "double_header": str(game.get("doubleHeader") or "N"),
                    "scheduled_innings": int(game.get("scheduledInnings") or 9),
                    "status_abstract": status.get("abstractGameState"),
                    "status_detailed": status.get("detailedState"),
                    "status_code": status.get("statusCode"),
                    "home_team_id": int(home["team"]["id"]),
                    "away_team_id": int(away["team"]["id"]),
                    "home_probable_pitcher_id": (home.get("probablePitcher") or {}).get("id"),
                    "away_probable_pitcher_id": (away.get("probablePitcher") or {}).get("id"),
                    "venue_id": venue.get("id"),
                    "reschedule_date": game.get("rescheduleDate"),
                    "rescheduled_from_date": game.get("rescheduledFromDate"),
                    "resume_date": game.get("resumeDate"),
                    "original_date": game.get("originalDate"),
                })
        frame = pl.DataFrame(rows) if rows else pl.DataFrame(schema={"game_pk": pl.Int64})
        enriched = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(ProviderStatus.AVAILABLE, enriched, frame, "NO_GAMES" if frame.is_empty() else None)

    @staticmethod
    def _feed_rows(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        payload = json.loads(body)
        game_data = payload.get("gameData")
        live_data = payload.get("liveData")
        if not isinstance(game_data, dict) or not isinstance(live_data, dict):
            raise TypeError("game feed lacks gameData/liveData")
        game_pk = int(game_data["game"]["pk"])
        teams = game_data.get("teams", {})
        probable = game_data.get("probablePitchers", {})
        players = game_data.get("players", {})
        box_teams = (live_data.get("boxscore") or {}).get("teams", {})
        rows: list[dict[str, Any]] = []
        for side in ("home", "away"):
            team = teams.get(side) or {}
            box = box_teams.get(side) or {}
            probable_pitcher = probable.get(side) or {}
            batting_order = [int(value) for value in (box.get("battingOrder") or [])]
            roster_ids = [int(str(value).removeprefix("ID")) for value in (box.get("players") or {})]
            rows.append({
                "game_pk": game_pk,
                "team_side": side,
                "team_id": int(team["id"]),
                "probable_pitcher_id": probable_pitcher.get("id"),
                "probable_pitcher_name": probable_pitcher.get("fullName"),
                "batting_order_json": json.dumps(batting_order),
                "roster_player_ids_json": json.dumps(roster_ids),
                "players_json": json.dumps(players, sort_keys=True),
            })
        frame = pl.DataFrame(rows)
        enriched = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(ProviderStatus.AVAILABLE, enriched, frame)

    @staticmethod
    def _transaction_rows(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        payload = json.loads(body)
        transactions = payload.get("transactions")
        if not isinstance(transactions, list):
            raise TypeError("transactions payload lacks transactions list")
        frame = pl.DataFrame(transactions) if transactions else pl.DataFrame(schema={"id": pl.Int64})
        enriched = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(ProviderStatus.AVAILABLE, enriched, frame, "NO_TRANSACTIONS" if frame.is_empty() else None)

    def schedule(self, start: date, end: date, *, force: bool = False) -> ProviderResult:
        if end < start:
            return ProviderResult.unavailable("schedule end precedes start")
        params = {
            "sportId": 1,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": "probablePitcher,team,venue",
        }
        return self._json_endpoint(
            endpoint_family="schedule",
            url=f"{MLB_STATS_BASE}/v1/schedule",
            parameters=params,
            parser=self._schedule_rows,
            force=force,
        )

    def game_feed(self, game_pk: int, *, force: bool = False) -> ProviderResult:
        return self._json_endpoint(
            endpoint_family="game_feed",
            url=f"{MLB_STATS_BASE}/v1.1/game/{game_pk}/feed/live",
            parameters={"game_pk": game_pk},
            parser=self._feed_rows,
            source_event_id=str(game_pk),
            force=force,
        )

    def transactions(self, start: date, end: date, *, force: bool = False) -> ProviderResult:
        if end < start:
            return ProviderResult.unavailable("transactions end precedes start")
        params = {"sportId": 1, "startDate": start.isoformat(), "endDate": end.isoformat()}
        return self._json_endpoint(
            endpoint_family="transactions",
            url=f"{MLB_STATS_BASE}/v1/transactions",
            parameters=params,
            parser=self._transaction_rows,
            force=force,
        )
