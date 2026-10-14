"""OpenLigaDB (api.openligadb.de) client -- raw capture only.

Keyless, crowd-maintained results for German football (Bundesliga and
related competitions). ``getavailableleagues`` lists league shortcuts and
seasons. ``getmatchdata/{league}/{season}`` returns every match for a season,
with ``matchIsFinished`` and the final result in ``matchResults``.

Raw JSON only. Not a modelled source in this repo. Soccer is already covered
by the Dixon-Coles pipeline through other feeds.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://api.openligadb.de"


class OpenLigaDBClient:
    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def _get(self, path: str) -> list[dict[str, Any]]:
        response = self.client.get(f"{self.base_url}{path}")
        response.raise_for_status()
        return response.json()

    def available_leagues(self) -> list[dict[str, Any]]:
        return self._get("/getavailableleagues")

    def match_data(self, league: str, season: int) -> list[dict[str, Any]]:
        """Every match in one league and season, e.g. ``bl1`` / ``2025``."""
        return self._get(f"/getmatchdata/{league}/{season}")
