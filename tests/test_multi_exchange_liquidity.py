import json
from unittest.mock import MagicMock, patch

from model_prediction.portfolio.kalshi_client import (
    KalshiClient,
    calculate_dutching_arbitrage,
)
from model_prediction.portfolio.polymarket_ws import PolymarketWebSocketFeed


def test_polymarket_ws_snapshot_and_delta():
    feed = PolymarketWebSocketFeed()
    received = []
    feed.register_listener(
        lambda tid, book: received.append((tid, book.best_bid, book.best_ask, book.spread))
    )

    # 1. Snapshot message
    snapshot_msg = json.dumps(
        {
            "event_type": "book",
            "asset_id": "tok_123",
            "bids": [{"price": "0.50", "size": "100"}, {"price": "0.49", "size": "200"}],
            "asks": [{"price": "0.52", "size": "150"}, {"price": "0.53", "size": "300"}],
            "timestamp": "2026-08-31T07:00:00Z",
        }
    )
    feed.handle_message(snapshot_msg)
    assert len(received) == 1
    assert received[0] == ("tok_123", 0.50, 0.52, 0.02)
    book = feed.books["tok_123"]
    assert book.midpoint == 0.51

    # 2. Price change message (delta)
    delta_msg = json.dumps(
        {
            "event_type": "price_change",
            "asset_id": "tok_123",
            "changes": [
                {"side": "buy", "price": "0.51", "size": "250"},
                {"side": "sell", "price": "0.52", "size": "0"},  # ask removed
            ],
        }
    )
    feed.handle_message(delta_msg)
    assert len(received) == 2
    book = feed.books["tok_123"]
    assert book.best_bid == 0.51
    assert book.best_ask == 0.53
    assert book.spread == 0.02


def test_dutching_arbitrage_detection():
    # Scenario: Cross-book mispricing between Polymarket and Kalshi
    # Poly: YES ask 0.45, NO ask 0.58
    # Kalshi: YES ask 0.55, NO ask 0.51
    # Poly YES (0.45) + Kalshi NO (0.51) = 0.96 (< 1.00 -> 4.0% risk-free arb!)
    arbs = calculate_dutching_arbitrage(
        event_title="Team A vs Team B",
        poly_slug="poly-slug-1",
        kalshi_ticker="KALSHI-1",
        poly_yes_ask=0.45,
        poly_no_ask=0.58,
        kalshi_yes_ask=0.55,
        kalshi_no_ask=0.51,
    )
    assert len(arbs) == 2
    arb1 = next(a for a in arbs if a.side_a_selection == "YES" and a.side_b_selection == "NO")
    assert arb1.is_arbitrage is True
    assert arb1.implied_sum == 0.96
    assert arb1.profit_margin_pct == 4.0


def test_kalshi_client_market_parsing():
    client = KalshiClient()
    mock_payload = json.dumps(
        {
            "markets": [
                {
                    "ticker": "MLB-NYY-BOS-26AUG31",
                    "event_ticker": "MLB-NYY-BOS",
                    "title": "New York Yankees vs Boston Red Sox",
                    "yes_bid": 54,
                    "yes_ask": 56,
                    "no_bid": 44,
                    "no_ask": 47,
                    "volume": 12500,
                    "open_interest": 4500,
                    "status": "open",
                }
            ]
        }
    ).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_payload
    mock_resp.__enter__.return_value = mock_resp

    with patch("model_prediction.portfolio.kalshi_client.urlopen", return_value=mock_resp):
        quotes = client.fetch_markets()
        assert len(quotes) == 1
        q = quotes[0]
        assert q.ticker == "MLB-NYY-BOS-26AUG31"
        assert q.yes_bid == 0.54
        assert q.yes_ask == 0.56
        assert q.no_bid == 0.44
        assert q.no_ask == 0.47
