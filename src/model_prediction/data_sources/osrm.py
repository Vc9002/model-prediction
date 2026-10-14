"""OSRM routing client -- raw capture only.

Keyless. Defaults to the public demo server at ``router.project-osrm.org``,
which the OSRM project documents as best-effort, with no uptime guarantee and
a fair-use policy. Pass ``base_url`` to point at a self-hosted OSRM instance
for anything that runs on a schedule.

Returns the road duration (seconds) and distance (metres) between two
coordinates, one route per call. Used for travel-fatigue research, not as a
live feature.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://router.project-osrm.org"


class OSRMClient:
    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def route(
        self, origin: tuple[float, float], destination: tuple[float, float], *, profile: str = "driving"
    ) -> dict[str, Any]:
        """Route from (lat, lon) origin to (lat, lon) destination. Raises on OSRM error codes."""
        (o_lat, o_lon), (d_lat, d_lon) = origin, destination
        coords = f"{o_lon},{o_lat};{d_lon},{d_lat}"
        response = self.client.get(
            f"{self.base_url}/route/v1/{profile}/{coords}",
            params={"overview": "false"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise httpx.HTTPError(f"OSRM route failed: {payload.get('code')}")
        route = payload["routes"][0]
        return {"duration_s": route["duration"], "distance_m": route["distance"]}
