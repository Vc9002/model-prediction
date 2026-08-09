"""Raw-first access to official nflverse release assets.

nflverse releases are mutable snapshots, not historical observation logs.
The exact Parquet bytes are therefore captured before parsing and every
retrieval keeps its real capture time. A season/week column never substitutes
for evidence that a row was available at an earlier decision time.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class NFLVerseAsset:
    release_tag: str
    filename_pattern: str
    minimum_season: int
    required_columns: frozenset[str]
    season_partitioned: bool = True
    project_license_id: str = "CC-BY-4.0"
    project_license_url: str = "https://creativecommons.org/licenses/by/4.0/"
    attribution_required: bool = True
    attribution_text: str = "nflverse project data; attribution required under CC BY 4.0"
    upstream_rights_status: str = "unresolved"
    commercial_use_status: str = "unresolved"
    production_allowed: bool = False

    def filename(self, season: int) -> str:
        return self.filename_pattern.format(season=season)

    def url(self, season: int) -> str:
        return (
            "https://github.com/nflverse/nflverse-data/releases/download/"
            f"{self.release_tag}/{self.filename(season)}"
        )


NFLVERSE_RELEASE_ASSETS: Final[dict[str, NFLVerseAsset]] = {
    "schedule": NFLVerseAsset(
        release_tag="schedules",
        filename_pattern="games.parquet",
        minimum_season=1999,
        required_columns=frozenset(
            {
                "game_id",
                "season",
                "game_type",
                "week",
                "gameday",
                "gametime",
                "away_team",
                "home_team",
            }
        ),
        season_partitioned=False,
    ),
    "pbp": NFLVerseAsset(
        release_tag="pbp",
        filename_pattern="play_by_play_{season}.parquet",
        minimum_season=1999,
        required_columns=frozenset(
            {
                "game_id",
                "play_id",
                "season",
                "season_type",
                "week",
                "posteam",
                "defteam",
                "qtr",
                "play_type",
                "game_seconds_remaining",
                "desc",
            }
        ),
    ),
    "weekly_rosters": NFLVerseAsset(
        release_tag="weekly_rosters",
        filename_pattern="roster_weekly_{season}.parquet",
        minimum_season=2002,
        required_columns=frozenset(
            {
                "season",
                "week",
                "team",
                "gsis_id",
                "full_name",
                "position",
            }
        ),
    ),
}


class NFLVerseProvider:
    """Direct, auditable nflverse release client using the shared raw cache."""

    provider_id = "nflverse"

    def __init__(self, http: HttpProviderClient, cache: ProviderRawCache) -> None:
        self.http = http
        self.cache = cache

    @staticmethod
    def _parse_parquet(body: bytes) -> pl.DataFrame:
        if len(body) < 8 or body[:4] != b"PAR1" or body[-4:] != b"PAR1":
            raise ValueError("response is not a complete Parquet file")
        return pl.read_parquet(io.BytesIO(body))

    @staticmethod
    def _parameters(table: str, season: int, asset: NFLVerseAsset) -> dict[str, Any]:
        parameters: dict[str, Any] = {"asset": asset.filename(season), "table": table}
        if asset.season_partitioned:
            parameters["season"] = season
        return parameters

    def season_table(self, table: str, season: int, *, force: bool = False) -> ProviderResult:
        asset = NFLVERSE_RELEASE_ASSETS.get(table)
        if asset is None:
            return ProviderResult.unavailable(f"unsupported nflverse NFL table: {table}")
        if season < asset.minimum_season:
            return ProviderResult.unavailable(f"nflverse {table} releases begin in {asset.minimum_season}")

        parameters = self._parameters(table, season, asset)
        endpoint = f"nfl_{table}"
        cached = self.cache.latest_success(self.provider_id, "nfl", endpoint, parameters)
        prior_schema_hash = cached.metadata.schema_hash if cached is not None else None
        if cached is not None and not force:
            try:
                raw_frame = self._parse_parquet(cached.read_bytes())
            except Exception as exc:  # noqa: BLE001 -- retained raw parser boundary
                return ProviderResult(
                    ProviderStatus.DEGRADED,
                    cached.metadata,
                    None,
                    f"cached nflverse parquet parse failed: {exc}",
                )
            return self._validate_and_filter(table, season, raw_frame, cached.metadata)

        try:
            fetched = self.http.get(asset.url(season))
        except Exception as exc:  # noqa: BLE001 -- external transport boundary
            return ProviderResult.unavailable(f"nflverse request failed: {exc}")

        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport="nfl",
            endpoint_family=endpoint,
            requested_parameters=parameters,
            request_time_utc=fetched.request_time_utc,
            retrieved_at_utc=fetched.retrieved_at_utc,
            observed_at_utc=fetched.retrieved_at_utc,
            http_status=fetched.status_code,
            content_hash=hashlib.sha256(fetched.body).hexdigest(),
            schema_hash=None,
            content_type=fetched.headers.get("content-type"),
            source_version=f"nflverse-data:{asset.release_tag}/{asset.filename(season)}",
            source_grade=SourceGrade.B,
            source_asset=f"nflverse-data release {asset.release_tag}",
            provider_chain="github.com/nflverse/nflverse-data releases",
            license_id=asset.project_license_id,
            license_url=asset.project_license_url,
            attribution_required=asset.attribution_required,
            attribution_text=asset.attribution_text,
            upstream_rights_status=asset.upstream_rights_status,
            commercial_use_status=asset.commercial_use_status,
            production_allowed=asset.production_allowed,
        )
        if fetched.status_code != 200:
            self.cache.store(metadata, fetched.body)
            return ProviderResult.unavailable(f"nflverse asset returned HTTP {fetched.status_code}", metadata)

        try:
            raw_frame = self._parse_parquet(fetched.body)
        except Exception as exc:  # noqa: BLE001 -- raw retained before explicit failure
            self.cache.store(metadata, fetched.body)
            return ProviderResult(
                ProviderStatus.DEGRADED,
                metadata,
                None,
                f"nflverse parquet parse failed: {exc}",
            )

        metadata = replace(metadata, schema_hash=dataframe_schema_hash(raw_frame))
        self.cache.store(metadata, fetched.body)
        if prior_schema_hash is not None and prior_schema_hash != metadata.schema_hash:
            return ProviderResult(
                ProviderStatus.DEGRADED,
                metadata,
                None,
                f"schema drift: {prior_schema_hash} -> {metadata.schema_hash}",
            )
        return self._validate_and_filter(table, season, raw_frame, metadata)

    @staticmethod
    def _validate_and_filter(
        table: str,
        season: int,
        frame: pl.DataFrame,
        metadata: SourceResponseMetadata,
    ) -> ProviderResult:
        asset = NFLVERSE_RELEASE_ASSETS[table]
        missing = sorted(asset.required_columns - set(frame.columns))
        if missing:
            return ProviderResult(
                ProviderStatus.DEGRADED,
                metadata,
                None,
                f"schema drift: missing required columns {missing}",
            )
        selected = frame.filter(pl.col("season") == season)
        if selected.is_empty():
            return ProviderResult.unavailable(
                f"nflverse {table} asset contained no rows for season {season}", metadata
            )
        return ProviderResult(ProviderStatus.AVAILABLE, metadata, selected)

    def schedule(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult:
        if sport != "nfl":
            return ProviderResult.unavailable(f"nflverse NFL provider does not serve {sport}")
        return self.season_table("schedule", season, force=bool(kwargs.get("force", False)))

    def rosters(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult:
        if sport != "nfl":
            return ProviderResult.unavailable(f"nflverse rosters are not implemented for {sport}")
        return self.season_table("weekly_rosters", season, force=bool(kwargs.get("force", False)))

    def play_by_play(self, *, sport: str, season: int, **kwargs: Any) -> ProviderResult:
        if sport != "nfl":
            return ProviderResult.unavailable(f"nflverse play-by-play is not implemented for {sport}")
        return self.season_table("pbp", season, force=bool(kwargs.get("force", False)))
