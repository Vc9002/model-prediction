"""Kalshi data source — DEFERRED STUB.

Kalshi is a CFTC-regulated US-only exchange. The account holder is outside the
US, so this integration is intentionally not implemented. The class exists so
the CLI can expose ``--provider kalshi`` with an honest error and so the
activation path is documented.

Activation path (when US residency applies):
1. Create API credentials at https://kalshi.com (or the demo environment).
2. Set ``KALSHI_API_KEY_ID`` and ``KALSHI_PRIVATE_KEY`` environment variables.
3. Implement the methods below against https://api.elections.kalshi.com/trade-api/v2
   (markets, orderbooks, and settlement endpoints).
4. Flip ``data_sources.kalshi.status`` from ``deferred`` to ``active`` in
   ``config/model.yaml``.
"""

from __future__ import annotations

from datetime import date
from typing import Any


STATUS = "deferred"
DEFERRED_MESSAGE = (
    "Kalshi integration is deferred: it requires US residency and CFTC-regulated "
    "account access. Polymarket US is the primary market data source."
)


class KalshiDeferredError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(DEFERRED_MESSAGE)


class KalshiClient:
    """Structure-only stub. Every method raises ``KalshiDeferredError``."""

    def __init__(self, base_url: str = "https://api.elections.kalshi.com/trade-api/v2") -> None:
        self.base_url = base_url

    def slate(self, league: str, game_date: date) -> list[dict[str, Any]]:
        """Would return normalized sports events with market metadata."""
        raise KalshiDeferredError()

    def orderbook(self, ticker: str) -> dict[str, Any]:
        """Would return the executable bid/ask book for one market ticker."""
        raise KalshiDeferredError()

    def snapshot(self, ticker: str) -> dict[str, Any]:
        """Would freeze a decision-time BBO snapshot (executable ask, never midpoint)."""
        raise KalshiDeferredError()

    def settlement(self, ticker: str) -> dict[str, Any]:
        """Would return the official market resolution for grading."""
        raise KalshiDeferredError()
