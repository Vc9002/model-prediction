"""National Weather Service (api.weather.gov) client -- raw capture only.

Keyless, US-only, but requires a descriptive ``User-Agent`` header per NWS
policy. Two endpoints are used:

- ``/points/{lat},{lon}`` resolves a coordinate to its forecast office and
  nearest observation-station list (``observationStations`` URL).
- ``/stations/{id}/observations`` returns surface observations. Observations
  are time-stamped by the station (``timestamp``), so they are point-in-time
  queryable with a ``start``/``end`` window, unlike the live-only forecast.

This module returns raw JSON and is never called from a live forecast path
directly. Capture through ``provider_capture.write_provider_snapshot``.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://api.weather.gov"
DEFAULT_USER_AGENT = "model-prediction/1.0 (vincent.chen9002@gmail.com)"


class NWSClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(
            timeout=30,
            headers={"User-Agent": user_agent, "Accept": "application/geo+json"},
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    def points(self, lat: float, lon: float) -> dict[str, Any]:
        """Grid + forecast-office + observation-station URLs for a coordinate."""
        return self._get(f"/points/{lat:.4f},{lon:.4f}")

    def station_ids(self, lat: float, lon: float) -> list[str]:
        """Observation station identifiers nearest to a coordinate, closest first."""
        stations_url = self.points(lat, lon)["properties"]["observationStations"]
        payload = self.client.get(stations_url)
        payload.raise_for_status()
        features = payload.json().get("features", [])
        return [feature["properties"]["stationIdentifier"] for feature in features]

    def observations(
        self, station_id: str, *, start: str | None = None, end: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Surface observations for one station, optionally bounded by ISO-8601 times."""
        params: dict[str, Any] = {"limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        payload = self._get(f"/stations/{station_id}/observations", params)
        return payload.get("features", [])
