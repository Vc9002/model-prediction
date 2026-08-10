"""Wires WNBAFoundation into the `rebuild-data` CLI's DataFoundation seam.

Kept in this package (not in data_foundation.py) so the CLI registry stays
sport-agnostic, matching mlb_v3/cli_adapter.py's precedent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

from model_prediction.rebuild.identity import IdentityRegistry
from model_prediction.rebuild.metadata import MetadataDB
from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.config import load_rebuild_sources_config
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.sportsdataverse import SportsDataverseProvider

from .foundation import WNBAFoundation

DEFAULT_TABLES = ("schedule", "team_box", "player_box", "rosters", "pbp")


class WNBADataFoundation:
    """Owns one HttpProviderClient and one MetadataDB connection for the
    lifetime of one CLI invocation -- callers must use this as a context
    manager, same convention as MLBV3DataFoundation."""

    def __init__(self, data_root: Path, *, repo_root: Path) -> None:
        self._data_root = data_root
        self._repo_root = repo_root
        self._http: HttpProviderClient | None = None
        self._metadata: MetadataDB | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._http is not None:
            self._http.close()
        if self._metadata is not None:
            self._metadata.close()

    def _foundation(self) -> WNBAFoundation:
        policy = load_rebuild_sources_config().sportsdataverse
        self._http = HttpProviderClient(
            retry=RetryPolicy(attempts=policy.retries),
            min_interval_seconds=policy.min_interval_seconds,
        )
        cache = ProviderRawCache(self._data_root / "raw")
        provider = SportsDataverseProvider(self._http, cache)
        # metadata.db lives alongside shadow.db under the same resolved
        # data_root -- matches mlb_shadow_pipeline.py's/sport_adapter.py's
        # existing MetadataDB(f"{data_root}/metadata.db") convention.
        self._metadata = MetadataDB(self._data_root / "metadata.db")
        identity = IdentityRegistry(self._metadata)
        return WNBAFoundation(provider, self._data_root / "normalized", identity)

    def backfill(
        self,
        *,
        seasons: tuple[int, ...] | None = None,
        tables: tuple[str, ...] | None = None,
        force: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        if not seasons:
            raise ValueError("WNBA backfill requires --season (repeatable)")
        foundation = self._foundation()
        return foundation.backfill(seasons, tables=tables or DEFAULT_TABLES, force=force)

    def audit(self, *, season: int | None = None, **_: Any) -> dict[str, Any]:
        if season is None:
            raise ValueError("WNBA audit requires --season")
        foundation = self._foundation()
        return foundation.audit(season)
