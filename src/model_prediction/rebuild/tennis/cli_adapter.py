"""Wires TennisFoundation into the `rebuild-data` CLI's DataFoundation seam.

Kept in this package (not in data_foundation.py) so the CLI registry stays
sport-agnostic, matching the other sports' cli_adapter.py precedent.
Backfill (`--tour --season ... --kind`) uses TennisMyLife; a season-less
audit or a `--current` collection uses ESPN's live scoreboard instead --
the two providers serve genuinely different time horizons (historical
season files vs. today's scoreboard), so both are wired through the same
context manager rather than picking one per invocation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.config import load_rebuild_sources_config
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.tennis_espn import ESPNTennisProvider
from model_prediction.rebuild.providers.tennis_mylife import TennisMyLifeProvider

from .foundation import TennisFoundation


class TennisDataFoundation:
    """Owns two HttpProviderClients (TennisMyLife + ESPN each have their
    own min_interval_seconds policy) for the lifetime of one CLI
    invocation -- callers must use this as a context manager, same
    convention as the other sport adapters."""

    def __init__(self, data_root: Path, *, repo_root: Path) -> None:
        self._data_root = data_root
        self._repo_root = repo_root
        self._tennis_mylife_http: HttpProviderClient | None = None
        self._espn_http: HttpProviderClient | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._tennis_mylife_http is not None:
            self._tennis_mylife_http.close()
        if self._espn_http is not None:
            self._espn_http.close()

    def _foundation(self, *, need_mylife: bool, need_espn: bool) -> TennisFoundation:
        sources = load_rebuild_sources_config()
        cache = ProviderRawCache(self._data_root / "raw")

        tennis_mylife = None
        if need_mylife:
            policy = sources.tennis_mylife
            self._tennis_mylife_http = HttpProviderClient(
                retry=RetryPolicy(attempts=policy.retries),
                min_interval_seconds=policy.min_interval_seconds,
            )
            tennis_mylife = TennisMyLifeProvider(self._tennis_mylife_http, cache)

        espn = None
        if need_espn:
            policy = sources.tennis_espn
            self._espn_http = HttpProviderClient(
                retry=RetryPolicy(attempts=policy.retries),
                min_interval_seconds=policy.min_interval_seconds,
            )
            espn = ESPNTennisProvider(self._espn_http, cache)

        return TennisFoundation(self._data_root / "normalized", tennis_mylife=tennis_mylife, espn=espn)

    def backfill(
        self,
        *,
        tour: str | None = None,
        seasons: tuple[int, ...] | None = None,
        match_kind: str = "main",
        current: bool = False,
        force: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        if not tour:
            raise ValueError("tennis backfill requires --tour")
        if current:
            foundation = self._foundation(need_mylife=False, need_espn=True)
            return foundation.collect_current(tour, force=force)  # type: ignore[arg-type]
        if not seasons:
            raise ValueError("tennis backfill requires --season (repeatable) unless --current is passed")
        foundation = self._foundation(need_mylife=True, need_espn=False)
        return foundation.backfill_matches(tour, seasons, kind=match_kind, force=force)  # type: ignore[arg-type]

    def audit(self, *, tour: str | None = None, **_: Any) -> dict[str, Any]:
        foundation = self._foundation(need_mylife=False, need_espn=False)
        return foundation.audit(tour=tour)
