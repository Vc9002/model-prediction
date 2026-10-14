"""Squiggle (api.squiggle.com.au) client -- raw capture only.

Keyless AFL fixtures, results, and model tips. Requires a descriptive
``User-Agent``. Squiggle's ``games`` query returns scheduled and completed
matches for a season; completed rows carry final scores in ``hscore`` and
``ascore``, and the row's ``complete`` field is 100 once the match is final.

Raw JSON only. AFL is not a modelled sport in this repo.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://api.squiggle.com.au/"
DEFAULT_USER_AGENT = "model-prediction/1.0 (vincent.chen9002@gmail.com)"


class SquiggleClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": user_agent})

    def games(self, year: int) -> list[dict[str, Any]]:
        """All scheduled and completed games for one AFL season."""
        response = self.client.get(self.base_url, params={"q": "games", "year": year})
        response.raise_for_status()
        return response.json().get("games", [])
