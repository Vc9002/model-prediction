"""Wires NFLFoundation into the `rebuild-data` CLI's DataFoundation seam.

Kept in this package (not in data_foundation.py) so the CLI registry stays
sport-agnostic, matching mlb_v3/cli_adapter.py's and wnba/cli_adapter.py's
precedent. No IdentityRegistry dependency here (unlike WNBA) -- NFLFoundation
only needs a provider and a normalized-store root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.config import load_rebuild_sources_config
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.nflverse import NFLVerseProvider

from .foundation import NFLFoundation

DEFAULT_TABLES = ("schedule", "pbp", "weekly_rosters")


class NFLDataFoundation:
    """Owns one HttpProviderClient for the lifetime of one CLI invocation --
    callers must use this as a context manager, same convention as
    MLBV3DataFoundation/WNBADataFoundation."""

    def __init__(self, data_root: Path, *, repo_root: Path) -> None:
        self._data_root = data_root
        self._repo_root = repo_root
        self._http: HttpProviderClient | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._http is not None:
            self._http.close()

    def _foundation(self) -> NFLFoundation:
        policy = load_rebuild_sources_config().nflverse
        self._http = HttpProviderClient(
            retry=RetryPolicy(attempts=policy.retries),
            min_interval_seconds=policy.min_interval_seconds,
        )
        cache = ProviderRawCache(self._data_root / "raw")
        provider = NFLVerseProvider(self._http, cache)
        return NFLFoundation(provider, self._data_root / "normalized")

    def backfill(
        self,
        *,
        seasons: tuple[int, ...] | None = None,
        tables: tuple[str, ...] | None = None,
        force: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        if not seasons:
            raise ValueError("NFL backfill requires --season (repeatable)")
        foundation = self._foundation()
        return foundation.backfill(seasons, tables=tables or DEFAULT_TABLES, force=force)

    def audit(self, *, season: int | None = None, **_: Any) -> dict[str, Any]:
        if season is None:
            raise ValueError("NFL audit requires --season")
        foundation = self._foundation()
        return foundation.audit(season)
