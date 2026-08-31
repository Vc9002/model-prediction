"""Polymarket CLOB WebSocket Real-Time Orderbook & Liquidity Streaming.

Subscribes to Polymarket WebSocket market channels, tracks sub-second BBO
(best bid/ask), book depth, and line-movement deltas across active token IDs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass
class BookLevel:
    price: float
    size: float


@dataclass
class MarketOrderbook:
    token_id: str
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    last_update_utc: str = ""

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def midpoint(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None:
            return round((self.best_bid + self.best_ask) / 2.0, 4)
        return None

    @property
    def spread(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None:
            return round(self.best_ask - self.best_bid, 4)
        return None


class PolymarketWebSocketFeed:
    """Manages live WebSocket subscriptions and book state."""

    def __init__(self, ws_url: str = DEFAULT_WS_URL) -> None:
        self.ws_url = ws_url
        self.books: dict[str, MarketOrderbook] = {}
        self.listeners: list[Callable[[str, MarketOrderbook], None]] = []

    def register_listener(self, callback: Callable[[str, MarketOrderbook], None]) -> None:
        self.listeners.append(callback)

    def handle_message(self, message_str: str) -> None:
        """Process an incoming WebSocket JSON message payload."""
        try:
            msg = json.loads(message_str)
        except json.JSONDecodeError:
            return

        event_type = msg.get("event_type") or msg.get("type")
        token_id = str(msg.get("asset_id") or msg.get("token_id") or "")
        if not token_id:
            return

        book = self.books.setdefault(token_id, MarketOrderbook(token_id=token_id))

        if event_type in ("book", "snapshot"):
            bids_raw = msg.get("bids", [])
            asks_raw = msg.get("asks", [])
            book.bids = [
                BookLevel(price=float(b.get("price", 0)), size=float(b.get("size", 0)))
                for b in sorted(bids_raw, key=lambda x: -float(x.get("price", 0)))
            ]
            book.asks = [
                BookLevel(price=float(a.get("price", 0)), size=float(a.get("size", 0)))
                for a in sorted(asks_raw, key=lambda x: float(x.get("price", 0)))
            ]
            book.last_update_utc = msg.get("timestamp", "")
            self._notify(token_id, book)

        elif event_type in ("price_change", "delta"):
            changes = msg.get("changes", [])
            for ch in changes:
                side = ch.get("side", "").lower()
                px = float(ch.get("price", 0))
                sz = float(ch.get("size", 0))
                target_list = book.bids if side == "buy" else book.asks
                # Update existing or append
                existing = next((lvl for lvl in target_list if abs(lvl.price - px) < 1e-4), None)
                if sz <= 0:
                    if existing:
                        target_list.remove(existing)
                else:
                    if existing:
                        existing.size = sz
                    else:
                        target_list.append(BookLevel(price=px, size=sz))
            # Re-sort
            book.bids.sort(key=lambda x: -x.price)
            book.asks.sort(key=lambda x: x.price)
            self._notify(token_id, book)

    def _notify(self, token_id: str, book: MarketOrderbook) -> None:
        for listener in self.listeners:
            try:
                listener(token_id, book)
            except Exception as e:  # noqa: BLE001
                logger.warning("Error in Polymarket WS listener: %s", e)
