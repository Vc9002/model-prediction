import json
from datetime import UTC, datetime

from model_prediction.data_sources.mlb_market_odds import (
    MarketOddsSnapshotStore,
    MLBMarketOddsFeed,
)

OBSERVED = datetime(2026, 7, 17, 18, tzinfo=UTC)


class FakePolymarket:
    def __init__(self, events):
        self.events = events

    def slate(self, league, game_date):
        assert league == "MLB"
        assert game_date.isoformat() == "2026-07-17"
        return self.events

    def snapshot(self, slug, observed_at):
        # (long_bid, long_ask) per market; short side is the complement book.
        books = {
            "ml": (0.42, 0.44),
            "spread": (0.46, 0.48),
            "total": (0.49, 0.51),
        }
        bid, ask = books[slug]
        return {
            "market_slug": slug,
            "observed_at_utc": observed_at.isoformat(),
            "long": {"bid": bid, "ask": ask, "midpoint": round((bid + ask) / 2, 6)},
            "short": {
                "bid": round(1 - ask, 6),
                "ask": round(1 - bid, 6),
                "midpoint": round(1 - (bid + ask) / 2, 6),
            },
        }


class FakeOddsAPI:
    def __init__(self, events):
        self.events = events

    def odds(self, league):
        assert league == "MLB"
        return self.events


def polymarket_event():
    return {
        "event_id": "poly-1",
        "markets": [
            {
                "market_type": "moneyline",
                "market_slug": "ml",
                "sides": [
                    {
                        "selection": "away",
                        "team": "New York Yankees",
                        "line": None,
                        "is_long": True,
                    },
                    {
                        "selection": "home",
                        "team": "Boston Red Sox",
                        "line": None,
                        "is_long": False,
                    },
                ],
            },
            {
                "market_type": "spread",
                "market_slug": "spread",
                "sides": [
                    {
                        "selection": "away",
                        "team": "New York Yankees",
                        "line": 1.5,
                        "is_long": True,
                    },
                    {
                        "selection": "home",
                        "team": "Boston Red Sox",
                        "line": -1.5,
                        "is_long": False,
                    },
                ],
            },
            {
                "market_type": "total",
                "market_slug": "total",
                "sides": [
                    {"selection": "over", "team": None, "line": 8.5, "is_long": True},
                    {"selection": "under", "team": None, "line": 8.5, "is_long": False},
                ],
            },
        ],
    }


def draftkings_event():
    return {
        "id": "odds-1",
        "away_team": "New York Yankees",
        "home_team": "Boston Red Sox",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "New York Yankees", "price": -105},
                            {"name": "Boston Red Sox", "price": -115},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "New York Yankees", "price": -110, "point": 1.5},
                            {"name": "Boston Red Sox", "price": -110, "point": -1.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -108, "point": 8.5},
                            {"name": "Under", "price": -112, "point": 8.5},
                        ],
                    },
                ],
            }
        ],
    }


def test_feed_prices_polymarket_at_executable_ask_and_stores_raw_snapshot(tmp_path, registry) -> None:
    store = MarketOddsSnapshotStore(tmp_path / "market.jsonl")
    feed = MLBMarketOddsFeed(
        registry,
        store,
        polymarket=FakePolymarket([polymarket_event()]),
        odds_api=FakeOddsAPI([draftkings_event()]),
        observed_at=OBSERVED,
    )
    feed.load("2026-07-17")
    odds = feed.for_game(
        "espn-1",
        "2026-07-17T23:00:00Z",
        "New York Yankees",
        "Boston Red Sox",
    )

    assert odds.provider == "polymarket_us"
    # Decision price is the executable ASK for the selected side, not midpoint.
    assert odds.markets["moneyline"]["away"].decision_probability == 0.44
    assert odds.markets["moneyline"]["away"].midpoint_probability == 0.43
    # Short (home) side ask = 1 - long bid.
    assert odds.markets["moneyline"]["home"].decision_probability == 0.58
    assert odds.markets["spread"]["home"].line == -1.5
    stored = json.loads((tmp_path / "market.jsonl").read_text())
    assert stored["raw_response"]["event"]["event_id"] == "poly-1"
    closing = store.closing_quote(
        "espn-1",
        "2026-07-17T23:00:00Z",
        "total",
        "over",
    )
    assert closing["decision_probability"] == 0.51


def test_feed_falls_back_to_draftkings_when_polymarket_event_is_unavailable(
    tmp_path,
    registry,
) -> None:
    feed = MLBMarketOddsFeed(
        registry,
        MarketOddsSnapshotStore(tmp_path / "market.jsonl"),
        polymarket=FakePolymarket([]),
        odds_api=FakeOddsAPI([draftkings_event()]),
        observed_at=OBSERVED,
    )
    feed.load("2026-07-17")
    odds = feed.for_game(
        "espn-1",
        "2026-07-17T23:00:00Z",
        "New York Yankees",
        "Boston Red Sox",
    )

    assert odds.provider == "draftkings_via_the_odds_api"
    assert odds.markets["moneyline"]["away"].american_odds == -105
    assert odds.markets["total"]["under"].line == 8.5
