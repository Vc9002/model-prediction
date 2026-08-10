"""Wires SoccerFoundation into the `rebuild-data` CLI's DataFoundation seam.

Kept in this package (not in data_foundation.py) so the CLI registry stays
sport-agnostic, matching mlb_v3/wnba/nfl's cli_adapter.py precedent. Unlike
those, soccer's foundation is date-keyed (`collect_date`), not
season-keyed -- ESPN scoreboards and football-data.org matches are both
fetched per calendar date, not per season.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Self

from model_prediction.rebuild.providers.cache import ProviderRawCache
from model_prediction.rebuild.providers.config import load_rebuild_sources_config
from model_prediction.rebuild.providers.football_data import FootballDataProvider
from model_prediction.rebuild.providers.http import HttpProviderClient, RetryPolicy
from model_prediction.rebuild.providers.soccer_espn import ESPNSoccerProvider

from .foundation import SoccerFoundation

DEFAULT_ESPN_LEAGUES = ("eng.1",)


class SoccerDataFoundation:
    """Owns two HttpProviderClients (ESPN + football-data.org each have
    their own min_interval_seconds policy) for the lifetime of one CLI
    invocation -- callers must use this as a context manager, same
    convention as the other sport adapters."""

    def __init__(self, data_root: Path, *, repo_root: Path) -> None:
        self._data_root = data_root
        self._repo_root = repo_root
        self._espn_http: HttpProviderClient | None = None
        self._football_data_http: HttpProviderClient | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._espn_http is not None:
            self._espn_http.close()
        if self._football_data_http is not None:
            self._football_data_http.close()

    def _foundation(self, *, use_football_data: bool) -> SoccerFoundation:
        sources = load_rebuild_sources_config()
        cache = ProviderRawCache(self._data_root / "raw")

        # ESPN has no dedicated policy entry in RebuildSourcesConfig (the
        # source branch never rate-limited it independently either); use
        # HttpProviderClient's own safe default RetryPolicy() rather than
        # inventing an unconfigured policy field.
        self._espn_http = HttpProviderClient(retry=RetryPolicy())
        espn = ESPNSoccerProvider(self._espn_http, cache)

        football_data: FootballDataProvider | None = None
        if use_football_data:
            policy = sources.football_data
            self._football_data_http = HttpProviderClient(
                retry=RetryPolicy(attempts=policy.retries),
                min_interval_seconds=policy.min_interval_seconds,
            )
            football_data = FootballDataProvider(self._football_data_http, cache)

        return SoccerFoundation(espn, self._data_root / "normalized", football_data=football_data)

    def backfill(
        self,
        *,
        game_date: str | None = None,
        espn_leagues: tuple[str, ...] | None = None,
        football_data_competitions: tuple[str, ...] | None = None,
        force: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        if not game_date:
            raise ValueError("soccer backfill requires --date")
        foundation = self._foundation(use_football_data=bool(football_data_competitions))
        return foundation.collect_date(
            date.fromisoformat(game_date),
            espn_leagues=espn_leagues or DEFAULT_ESPN_LEAGUES,
            football_data_competitions=football_data_competitions or (),
            force=force,
        )

    def audit(self, **_: Any) -> dict[str, Any]:
        foundation = self._foundation(use_football_data=False)
        return foundation.audit()
