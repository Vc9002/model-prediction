"""FanLine Wire (fanlinewire.com) public odds snapshot -- raw capture only.

Keyless REST snapshot of FanDuel Sportsbook prices at ``/odds.json``. It
replaces The Odds API as a free market-price source. Important limits:

- It is one sportsbook (FanDuel), not a multi-book consensus, and it is not a
  sharp-book reference. Treat it as one market price, not a no-vig benchmark.
- The snapshot is a shared public endpoint. The vendor asks for no more than
  one poll every 10 seconds, and this client enforces that floor.
- Prices carry only ``selection_id`` and ``american`` odds in the REST
  payload. Runner-level status and handicap need the paid WebSocket.

Each fixture has a ``generated_at`` / ``updated_at`` timestamp, so capture
with the snapshot's own time as ``observed_at_utc``. Raw JSON only.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

BASE_URL = "https://fanlinewire.com"
MIN_INTERVAL_SECONDS = 10.0


class FanLineWireClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        min_interval: float = MIN_INTERVAL_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)
        self.min_interval = min_interval
        self._last_request = 0.0
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        """Full public snapshot: ``generated_at``, ``sequence``, and ``fixtures``."""
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            response = self.client.get(f"{self.base_url}/odds.json")
            self._last_request = time.monotonic()
        response.raise_for_status()
        return response.json()

    def fixtures_for_sport(self, sport: str) -> list[dict[str, Any]]:
        """Fixtures whose ``sport`` matches, case-insensitively (e.g. ``Basketball``)."""
        wanted = sport.casefold()
        return [f for f in self.snapshot().get("fixtures", []) if f.get("sport", "").casefold() == wanted]
