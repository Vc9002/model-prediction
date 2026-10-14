"""NHL public web API (api-web.nhle.com) client -- raw capture only.

Keyless and undocumented-but-stable; this is the same feed the NHL.com
website reads. Used for schedule, final scores, and standings by date.

Point-in-time: a completed game's score is fixed once final. Schedules and
standings are live reads as of the call time and must be captured with their
own ``observed_at_utc``, not treated as retroactively queryable.

NHL is not yet a modelled sport in this repo (see ``domain.py``). This adapter
only fetches data.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://api-web.nhle.com/v1"


class NHLClient:
    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def _get(self, path: str) -> dict[str, Any]:
        response = self.client.get(f"{self.base_url}{path}")
        response.raise_for_status()
        return response.json()

    def schedule(self, date: str) -> dict[str, Any]:
        """Week of games starting at ``date`` (YYYY-MM-DD), as NHL returns it."""
        return self._get(f"/schedule/{date}")

    def scores(self, date: str) -> dict[str, Any]:
        """Scores for games on ``date`` (YYYY-MM-DD)."""
        return self._get(f"/score/{date}")

    def standings(self, date: str) -> dict[str, Any]:
        """League standings as of ``date`` (YYYY-MM-DD)."""
        return self._get(f"/standings/{date}")
