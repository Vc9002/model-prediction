"""Kalshi Event Contracts API Client & Multi-Exchange Dutching Arbitrage Engine.

Fetches Kalshi sports event contract odds, maps them against Polymarket CLOB,
and calculates Dutching arbitrage opportunities across prediction markets.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

DEFAULT_KALSHI_API_URL = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class KalshiMarketQuote:
    ticker: str
    event_ticker: str
    title: str
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    volume: int
    open_interest: int
    status: str


@dataclass(frozen=True)
class DutchingArbitrageOpportunity:
    event_title: str
    polymarket_slug: str
    kalshi_ticker: str
    side_a_exchange: str
    side_a_selection: str
    side_a_price: float
    side_b_exchange: str
    side_b_selection: str
    side_b_price: float
    implied_sum: float
    profit_margin_pct: float
    is_arbitrage: bool


def calculate_dutching_arbitrage(
    event_title: str,
    poly_slug: str,
    kalshi_ticker: str,
    poly_yes_ask: float,
    poly_no_ask: float,
    kalshi_yes_ask: float,
    kalshi_no_ask: float,
) -> list[DutchingArbitrageOpportunity]:
    """Checks both cross-exchange book combinations for Dutching arbitrage:
    Combination 1: Buy YES on Polymarket, Buy NO on Kalshi
    Combination 2: Buy NO on Polymarket, Buy YES on Kalshi
    """
    opportunities = []

    # Comb 1: Poly YES + Kalshi NO
    if poly_yes_ask > 0 and kalshi_no_ask > 0:
        cost_sum_1 = poly_yes_ask + kalshi_no_ask
        margin_1 = (1.0 - cost_sum_1) * 100.0
        opportunities.append(
            DutchingArbitrageOpportunity(
                event_title=event_title,
                polymarket_slug=poly_slug,
                kalshi_ticker=kalshi_ticker,
                side_a_exchange="polymarket",
                side_a_selection="YES",
                side_a_price=round(poly_yes_ask, 4),
                side_b_exchange="kalshi",
                side_b_selection="NO",
                side_b_price=round(kalshi_no_ask, 4),
                implied_sum=round(cost_sum_1, 4),
                profit_margin_pct=round(margin_1, 2),
                is_arbitrage=cost_sum_1 < 1.0,
            )
        )

    # Comb 2: Poly NO + Kalshi YES
    if poly_no_ask > 0 and kalshi_yes_ask > 0:
        cost_sum_2 = poly_no_ask + kalshi_yes_ask
        margin_2 = (1.0 - cost_sum_2) * 100.0
        opportunities.append(
            DutchingArbitrageOpportunity(
                event_title=event_title,
                polymarket_slug=poly_slug,
                kalshi_ticker=kalshi_ticker,
                side_a_exchange="polymarket",
                side_a_selection="NO",
                side_a_price=round(poly_no_ask, 4),
                side_b_exchange="kalshi",
                side_b_selection="YES",
                side_b_price=round(kalshi_yes_ask, 4),
                implied_sum=round(cost_sum_2, 4),
                profit_margin_pct=round(margin_2, 2),
                is_arbitrage=cost_sum_2 < 1.0,
            )
        )

    return opportunities


class KalshiClient:
    """Client for querying Kalshi event market contracts."""

    def __init__(self, base_url: str = DEFAULT_KALSHI_API_URL, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def fetch_markets(self, series_ticker: str | None = None, limit: int = 100) -> list[KalshiMarketQuote]:
        url = f"{self.base_url}/markets?limit={limit}"
        if series_ticker:
            url += f"&series_ticker={series_ticker}"

        req = Request(url, headers={"User-Agent": "ModelPrediction/1.0"})
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")

        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch Kalshi markets from %s: %s", url, e)
            return []

        quotes = []
        for m in data.get("markets", []):
            try:
                quotes.append(
                    KalshiMarketQuote(
                        ticker=m.get("ticker", ""),
                        event_ticker=m.get("event_ticker", ""),
                        title=m.get("title", ""),
                        yes_bid=float(m.get("yes_bid", 0)) / 100.0 if m.get("yes_bid") is not None else 0.0,
                        yes_ask=float(m.get("yes_ask", 0)) / 100.0 if m.get("yes_ask") is not None else 0.0,
                        no_bid=float(m.get("no_bid", 0)) / 100.0 if m.get("no_bid") is not None else 0.0,
                        no_ask=float(m.get("no_ask", 0)) / 100.0 if m.get("no_ask") is not None else 0.0,
                        volume=int(m.get("volume", 0)),
                        open_interest=int(m.get("open_interest", 0)),
                        status=m.get("status", "open"),
                    )
                )
            except (ValueError, TypeError):
                continue
        return quotes
