"""Wires MLBV3Foundation into the `rebuild-data` CLI's DataFoundation seam.

Kept in this package (not in data_foundation.py) so the CLI registry stays
sport-agnostic -- data_foundation.py only needs to import and register this
one class, matching how sport_adapter.py imports each sport's adapter from
its own module rather than defining sport logic inline.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Self

from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.config import load_rebuild_sources_config
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.mlb_stats import MLBStatsProvider
from model_prediction.rebuild.providers.statcast import StatcastProvider

from .foundation import MLBV3Foundation

SUPPORTED_PROVIDERS = ("mlb_stats", "statcast")


class MLBV3DataFoundation:
    """Adapts MLBV3Foundation's two provider-specific backfill methods to
    the CLI's single `backfill(**kwargs)` shape. Owns exactly one
    HttpProviderClient for the lifetime of one CLI invocation -- callers
    must use this as a context manager."""

    def __init__(self, data_root: Path, *, repo_root: Path) -> None:
        self._data_root = data_root
        self._repo_root = repo_root
        self._http: HttpProviderClient | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._http is not None:
            self._http.close()

    def _foundation(self, provider_name: str) -> MLBV3Foundation:
        if provider_name not in SUPPORTED_PROVIDERS:
            raise ValueError(f"unsupported MLB v3 provider: {provider_name}; choose one of {SUPPORTED_PROVIDERS}")
        sources = load_rebuild_sources_config()
        policy = sources.mlb_stats if provider_name == "mlb_stats" else sources.statcast
        self._http = HttpProviderClient(
            retry=RetryPolicy(attempts=policy.retries),
            min_interval_seconds=policy.min_interval_seconds,
        )
        cache = ProviderRawCache(self._data_root / "raw")
        normalized_root = self._data_root / "normalized"
        if provider_name == "mlb_stats":
            return MLBV3Foundation(
                normalized_root, repo_root=self._repo_root, mlb_stats=MLBStatsProvider(self._http, cache)
            )
        return MLBV3Foundation(
            normalized_root, repo_root=self._repo_root, statcast=StatcastProvider(self._http, cache)
        )

    def backfill(
        self,
        *,
        provider: str = "mlb_stats",
        start: str | None = None,
        end: str | None = None,
        tables: tuple[str, ...] | None = None,
        force: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        if not start or not end:
            raise ValueError("MLB v3 backfill requires --start and --end")
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        foundation = self._foundation(provider)
        if provider == "mlb_stats":
            return foundation.backfill_mlb_stats(start_date, end_date, tables=tables or ("schedule",), force=force)
        if tables:
            raise ValueError("Statcast backfill does not accept --table")
        return foundation.backfill_statcast(start_date, end_date, force=force)

    def audit(self, *, season: int | None = None, **_: Any) -> dict[str, Any]:
        if season is None:
            raise ValueError("MLB v3 audit requires --season")
        foundation = self._foundation("mlb_stats")
        return foundation.audit(season)
