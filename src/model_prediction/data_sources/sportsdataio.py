from __future__ import annotations

from typing import Any

import httpx


class SportsDataIOClient:
    """Optional paid provider adapter; no write endpoints exist."""

    def __init__(self, api_key: str, base_url: str = "https://api.sportsdata.io/v3") -> None:
        if not api_key:
            raise ValueError("SPORTSDATAIO_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def get(self, league: str, resource_path: str) -> Any:
        response = httpx.get(
            f"{self.base_url}/{league.lower()}/{resource_path.lstrip('/')}",
            headers={"Ocp-Apim-Subscription-Key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
