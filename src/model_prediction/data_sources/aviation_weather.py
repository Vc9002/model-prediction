"""NOAA Aviation Weather Center client (aviationweather.gov) -- raw capture only.

Keyless. Serves METAR (observed) and TAF (forecast) reports for airports by
ICAO identifier, e.g. ``KJFK``. Each METAR row carries ``obsTime`` (epoch
seconds, when the observation was made) and ``receiptTime`` (when NOAA
received it). A point-in-time filter should use ``receiptTime`` or
``obsTime``, never ``reportTime``, which can be rounded forward to the top
of the hour.

Raw JSON only. Not called from any live decision path.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://aviationweather.gov/api/data"


class AviationWeatherClient:
    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def _get(self, endpoint: str, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        response = self.client.get(
            f"{self.base_url}/{endpoint}",
            params={"ids": ",".join(ids), "format": "json"},
        )
        response.raise_for_status()
        return response.json()

    def metars(self, icao_ids: list[str]) -> list[dict[str, Any]]:
        """Latest observed METAR for each airport (temp, dewpoint, wind, altimeter)."""
        return self._get("metar", icao_ids)

    def tafs(self, icao_ids: list[str]) -> list[dict[str, Any]]:
        """Terminal aerodrome forecast for each airport."""
        return self._get("taf", icao_ids)
