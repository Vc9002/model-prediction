"""OpenStreetMap Nominatim geocoder client -- raw capture only.

Keyless. Usage policy (https://operations.osmfoundation.org/policies/nominatim/)
caps clients at 1 request per second and requires an identifying
``User-Agent``. ``NominatimClient`` enforces the 1 req/s floor itself, so
callers can loop over venues without tripping a block. Results are cached in
memory for the life of the client.

Intended for the one-off venue gaps that ``features/mlb_venue_geocoding.py``
deliberately leaves unresolved (exhibition and neutral-site parks). Callers
must treat a failed lookup as unavailable, never as coordinates 0,0.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

BASE_URL = "https://nominatim.openstreetmap.org"
DEFAULT_USER_AGENT = "model-prediction/1.0 (vincent.chen9002@gmail.com)"
MIN_INTERVAL_SECONDS = 1.0


class NominatimClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        min_interval: float = MIN_INTERVAL_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": user_agent})
        self.min_interval = min_interval
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def search(self, query: str, *, limit: int = 1) -> list[dict[str, Any]]:
        """Forward geocode a free-text query. Returns Nominatim's raw result rows."""
        if query in self._cache:
            return self._cache[query]
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            response = self.client.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "jsonv2", "limit": limit},
            )
            self._last_request = time.monotonic()
        response.raise_for_status()
        rows = response.json()
        self._cache[query] = rows
        return rows
