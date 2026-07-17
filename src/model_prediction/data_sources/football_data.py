"""Soccer data source for standings, match stats, and xG.

ESPN's soccer scoreboards (see ``espn.py``) already provide scores for every
supported league, so this source is an *enrichment* layer. It targets the
football-data.org v4 API, which needs a free API token; without one, every
method reports unavailability honestly instead of fabricating values.

Set ``FOOTBALL_DATA_TOKEN`` to activate.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


COMPETITIONS = {
    "EPL": "PL",
    "LA_LIGA": "PD",
    "BUNDESLIGA": "BL1",
    "SERIE_A": "SA",
    "LIGUE_1": "FL1",
    "EREDIVISIE": "DED",
    "PRIMEIRA_LIGA": "PPL",
    "CHAMPIONSHIP": "ELC",
    "UCL": "CL",
    "WORLD_CUP": "WC",
}


class FootballDataNotConfiguredError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "football-data.org token missing; set FOOTBALL_DATA_TOKEN to enable soccer "
            "enrichment (standings, match stats). Scores flow from ESPN regardless."
        )


class FootballDataClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.football-data.org/v4",
        client: httpx.Client | None = None,
    ) -> None:
        self.token = token or os.getenv("FOOTBALL_DATA_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise FootballDataNotConfiguredError()
        response = self.client.get(
            f"{self.base_url}{path}",
            params=params,
            headers={"X-Auth-Token": str(self.token)},
        )
        response.raise_for_status()
        return response.json()

    def standings(self, league: str) -> dict[str, Any]:
        competition = COMPETITIONS.get(league.upper())
        if competition is None:
            raise ValueError(f"unsupported football-data league: {league}")
        return self._get(f"/competitions/{competition}/standings")

    def matches(self, league: str, date_from: str, date_to: str) -> dict[str, Any]:
        competition = COMPETITIONS.get(league.upper())
        if competition is None:
            raise ValueError(f"unsupported football-data league: {league}")
        return self._get(
            f"/competitions/{competition}/matches",
            {"dateFrom": date_from, "dateTo": date_to},
        )
