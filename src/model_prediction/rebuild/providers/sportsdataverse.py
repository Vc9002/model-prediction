"""Raw-first access to SportsDataverse's versioned WNBA release assets.

The package is pinned for catalog/parser parity, but historical evidence is
downloaded as exact release bytes through our HTTPX transport. That avoids
letting an external package cache or silently-empty parser become the source
of truth.

WNBA-scoped today (the only sport with a real, tested release-asset table).
Other sports wanting SportsDataverse-catalogued data should add their own
`<SPORT>_RELEASE_ASSETS`-shaped table and dispatch branch here rather than
building a second copy of this file -- see the `providers/` survey in
`rebuild/free-first-providers-v1`'s PR description for why the previous
per-branch copies of this exact file were near-identical dead weight.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
from dataclasses import replace
from datetime import date
from typing import Any, Final

import polars as pl

from .base import (
    ProviderResult,
    ProviderStatus,
    SourceGrade,
    SourceResponseMetadata,
    dataframe_schema_hash,
)
from .cache import ProviderRawCache
from .http import HttpProviderClient
from .rights import SourceRightsProfile

SPORTSDATAVERSE_PIN: Final = "0.0.72"

# The package license covers its code, not necessarily the upstream ESPN
# data. Until data rights are affirmatively reviewed, these assets are
# research/shadow inputs only and cannot support economic or production
# operation.
SPORTSDATAVERSE_RIGHTS = SourceRightsProfile(
    source_asset="SportsDataverse-catalogued ESPN WNBA release assets",
    provider_chain="SportsDataverse release catalog -> ESPN",
    license_id="sportsdataverse-espn-data-rights-unresolved",
    license_url="https://github.com/sportsdataverse/sportsdataverse-data",
    attribution_required=False,
    attribution_text=None,
    subscription_required=False,
    subscription_scope="none",
    upstream_rights_status="unresolved",
    commercial_use_status="unresolved",
    use_scope="research_shadow_only",
    production_allowed=False,
    policy_note=(
        "The SportsDataverse package/catalog being open-source does not "
        "grant commercial rights to the underlying ESPN-derived data."
    ),
)

WNBA_RELEASE_ASSETS: Final[dict[str, tuple[str, str, frozenset[str]]]] = {
    "schedule": (
        "espn_wnba_schedules",
        "wnba_schedule_{season}.parquet",
        frozenset({
            "game_id", "season", "game_date_time", "home_id", "away_id",
            "home_display_name", "away_display_name",
        }),
    ),
    "team_box": (
        "espn_wnba_team_boxscores",
        "team_box_{season}.parquet",
        frozenset({
            "game_id", "season", "game_date_time", "team_id", "team_display_name",
            "opponent_team_id", "team_home_away", "team_score", "field_goals_made",
            "field_goals_attempted", "three_point_field_goals_made",
            "three_point_field_goals_attempted", "free_throws_made", "free_throws_attempted",
            "offensive_rebounds", "defensive_rebounds", "turnovers",
        }),
    ),
    "player_box": (
        "espn_wnba_player_boxscores",
        "player_box_{season}.parquet",
        frozenset({
            "game_id", "season", "game_date_time", "team_id", "athlete_id",
            "athlete_display_name",
        }),
    ),
    "rosters": (
        "espn_wnba_rosters",
        "rosters_{season}.parquet",
        frozenset({"season", "team_id", "team_display_name", "athlete_id", "display_name"}),
    ),
    "pbp": (
        "espn_wnba_pbp",
        "play_by_play_{season}.parquet",
        frozenset({"game_id", "season", "game_date_time", "id", "period_number"}),
    ),
    "standings": (
        "espn_wnba_standings",
        "standings_{season}.parquet",
        frozenset({"season", "team_id"}),
    ),
}


class SportsDataverseProvider:
    provider_id = "sportsdataverse"
    rights = SPORTSDATAVERSE_RIGHTS

    def __init__(self, http: HttpProviderClient, cache: ProviderRawCache) -> None:
        self.http = http
        self.cache = cache

    @staticmethod
    def installed_version() -> str | None:
        try:
            return importlib.metadata.version("sportsdataverse")
        except importlib.metadata.PackageNotFoundError:
            return None

    @staticmethod
    def _asset_url(table: str, season: int) -> str:
        tag, pattern, _required = WNBA_RELEASE_ASSETS[table]
        filename = pattern.format(season=season)
        return (
            "https://github.com/sportsdataverse/sportsdataverse-data/"
            f"releases/download/{tag}/{filename}"
        )

    @staticmethod
    def _parse_parquet(body: bytes) -> pl.DataFrame:
        return pl.read_parquet(io.BytesIO(body))

    def season_table(self, table: str, season: int, *, force: bool = False) -> ProviderResult:
        if table not in WNBA_RELEASE_ASSETS:
            return ProviderResult.unavailable(f"unsupported SportsDataverse WNBA table: {table}")
        if season < 2002:
            return ProviderResult.unavailable("SportsDataverse WNBA releases begin in 2002")

        parameters = {"season": season, "table": table}
        endpoint = f"wnba_{table}"
        cached = self.cache.latest_success(self.provider_id, "wnba", endpoint, parameters)
        prior_schema_hash = cached.metadata.schema_hash if cached is not None else None
        if cached is not None and not force:
            try:
                frame = self._parse_parquet(cached.read_bytes())
            except Exception as exc:  # noqa: BLE001 -- parser boundary; raw is retained and failure is explicit
                return ProviderResult(
                    ProviderStatus.DEGRADED,
                    cached.metadata,
                    None,
                    f"cached parquet parse failed: {exc}",
                )
            return self._validate_table(table, frame, cached.metadata)

        try:
            fetched = self.http.get(self._asset_url(table, season))
        except Exception as exc:  # noqa: BLE001 -- external transport boundary; failure is explicit
            return ProviderResult.unavailable(f"SportsDataverse request failed: {exc}")

        content_hash = hashlib.sha256(fetched.body).hexdigest()
        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport="wnba",
            endpoint_family=endpoint,
            requested_parameters=parameters,
            request_time_utc=fetched.request_time_utc,
            retrieved_at_utc=fetched.retrieved_at_utc,
            observed_at_utc=fetched.retrieved_at_utc,
            http_status=fetched.status_code,
            content_hash=content_hash,
            schema_hash=None,
            content_type=fetched.headers.get("content-type"),
            source_version=self.installed_version() or SPORTSDATAVERSE_PIN,
            source_grade=SourceGrade.B,
            **self.rights.metadata_kwargs(),
        )
        self.cache.store_blob(metadata, fetched.body)
        if fetched.status_code != 200:
            self.cache.store(metadata, fetched.body)
            return ProviderResult.unavailable(
                f"SportsDataverse asset returned HTTP {fetched.status_code}", metadata
            )

        try:
            frame = self._parse_parquet(fetched.body)
        except Exception as exc:  # noqa: BLE001 -- parser boundary; raw is retained and failure is explicit
            self.cache.store(metadata, fetched.body)
            return ProviderResult(
                ProviderStatus.DEGRADED, metadata, None, f"SportsDataverse parquet parse failed: {exc}"
            )

        metadata = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        self.cache.store(metadata, fetched.body)
        if prior_schema_hash is not None and prior_schema_hash != metadata.schema_hash:
            return ProviderResult(
                ProviderStatus.DEGRADED,
                metadata,
                None,
                f"schema drift: {prior_schema_hash} -> {metadata.schema_hash}",
            )
        return self._validate_table(table, frame, metadata)

    def current_schedule(self, game_date: date, *, force: bool = False) -> ProviderResult:
        """Capture the current ESPN WNBA scoreboard using SDV's endpoint catalog."""
        parameters = {"dates": game_date.strftime("%Y%m%d"), "limit": 500}
        endpoint = "wnba_scoreboard"
        cached = self.cache.latest_success(self.provider_id, "wnba", endpoint, parameters)
        if cached is not None and not force:
            return self._parse_scoreboard(cached.read_bytes(), cached.metadata)
        try:
            fetched = self.http.get(
                "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
                params=parameters,
            )
        except Exception as exc:  # noqa: BLE001 -- external transport boundary; failure is explicit
            return ProviderResult.unavailable(f"SportsDataverse ESPN request failed: {exc}")
        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport="wnba",
            endpoint_family=endpoint,
            requested_parameters=parameters,
            request_time_utc=fetched.request_time_utc,
            retrieved_at_utc=fetched.retrieved_at_utc,
            observed_at_utc=fetched.retrieved_at_utc,
            http_status=fetched.status_code,
            content_hash=hashlib.sha256(fetched.body).hexdigest(),
            schema_hash=None,
            content_type=fetched.headers.get("content-type"),
            source_version=self.installed_version() or SPORTSDATAVERSE_PIN,
            source_grade=SourceGrade.A,
            **self.rights.metadata_kwargs(),
        )
        self.cache.store_blob(metadata, fetched.body)
        if fetched.status_code != 200:
            self.cache.store(metadata, fetched.body)
            return ProviderResult.unavailable(f"WNBA scoreboard returned HTTP {fetched.status_code}", metadata)
        result = self._parse_scoreboard(fetched.body, metadata)
        stored_metadata = result.metadata or metadata
        self.cache.store(stored_metadata, fetched.body)
        return result

    @staticmethod
    def _parse_scoreboard(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
                raise TypeError("scoreboard payload lacks an events list")
            rows: list[dict[str, Any]] = []
            for event in payload["events"]:
                competition = event["competitions"][0]
                competitors = {item["homeAway"]: item for item in competition["competitors"]}
                home, away = competitors["home"], competitors["away"]
                status = competition.get("status", {}).get("type", {})
                venue = competition.get("venue") or {}
                rows.append({
                    "game_id": int(event["id"]),
                    "season": int(event["season"]["year"]),
                    "season_type": int(event["season"]["type"]),
                    "game_date_time": event["date"],
                    "status_type_name": status.get("name", "UNKNOWN"),
                    "status_type_completed": bool(status.get("completed", False)),
                    "home_id": int(home["team"]["id"]),
                    "away_id": int(away["team"]["id"]),
                    "home_display_name": home["team"].get("displayName", ""),
                    "away_display_name": away["team"].get("displayName", ""),
                    "home_score": int(home["score"]) if home.get("score") not in (None, "") else None,
                    "away_score": int(away["score"]) if away.get("score") not in (None, "") else None,
                    "venue_id": int(venue["id"]) if venue.get("id") else None,
                    "venue_full_name": venue.get("fullName"),
                })
            frame = pl.DataFrame(rows) if rows else pl.DataFrame(schema={
                "game_id": pl.Int64,
                "season": pl.Int64,
                "game_date_time": pl.String,
                "home_id": pl.Int64,
                "away_id": pl.Int64,
            })
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"scoreboard schema drift: {exc}")
        metadata = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(ProviderStatus.AVAILABLE, metadata, frame, "NO_EVENTS" if frame.is_empty() else None)

    @staticmethod
    def _validate_table(
        table: str, frame: pl.DataFrame, metadata: SourceResponseMetadata
    ) -> ProviderResult:
        required = WNBA_RELEASE_ASSETS[table][2]
        missing = sorted(required - set(frame.columns))
        if missing:
            return ProviderResult(
                ProviderStatus.DEGRADED,
                metadata,
                None,
                f"schema drift: missing required columns {missing}",
            )
        if frame.is_empty():
            return ProviderResult.unavailable("SportsDataverse asset contained no rows", metadata)
        return ProviderResult(ProviderStatus.AVAILABLE, metadata, frame)

    def schedule(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult:
        if sport != "wnba":
            return ProviderResult.unavailable(f"SportsDataverse WNBA provider does not serve {sport}")
        if kwargs.get("date") is not None:
            return self.current_schedule(kwargs["date"], force=bool(kwargs.get("force", False)))
        return self.season_table("schedule", season)

    def events(self, *, sport: str, **kwargs: Any) -> ProviderResult:
        if sport == "wnba" and isinstance(kwargs.get("date"), date):
            return self.current_schedule(kwargs["date"], force=bool(kwargs.get("force", False)))
        return ProviderResult.unavailable(f"events require a date for {sport}")

    def teams(self, *, sport: str, season: int, **_kwargs: Any) -> ProviderResult:
        return ProviderResult.unavailable(f"teams are not implemented for {sport} {season}")

    def players(self, *, sport: str, season: int, **_kwargs: Any) -> ProviderResult:
        return ProviderResult.unavailable(f"players are not implemented for {sport} {season}")

    def rosters(self, *, sport: str, season: int, **_kwargs: Any) -> ProviderResult:
        if sport != "wnba":
            return ProviderResult.unavailable(f"rosters are not implemented for {sport}")
        return self.season_table("rosters", season)

    def boxscores(
        self, *, sport: str, season: int, level: str = "team", **_kwargs: Any
    ) -> ProviderResult:
        if sport != "wnba":
            return ProviderResult.unavailable(f"boxscores are not implemented for {sport}")
        if level not in {"team", "player"}:
            return ProviderResult.unavailable(f"unsupported WNBA boxscore level: {level}")
        return self.season_table(f"{level}_box", season)

    def play_by_play(self, *, sport: str, season: int, **_kwargs: Any) -> ProviderResult:
        if sport != "wnba":
            return ProviderResult.unavailable(f"play-by-play is not implemented for {sport}")
        return self.season_table("pbp", season)

    def standings(self, *, sport: str, season: int, **_kwargs: Any) -> ProviderResult:
        if sport != "wnba":
            return ProviderResult.unavailable(f"standings are not implemented for {sport}")
        return self.season_table("standings", season)
