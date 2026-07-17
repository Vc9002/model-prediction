import json
from datetime import date, datetime, timezone

import httpx

from model_prediction.data_sources.polymarket_us import (
    POLYMARKET_SPORT_LEAGUES,
    PolymarketSnapshotStore,
    PolymarketUSClient,
    capture_slate_snapshots,
    probability_to_american,
    refresh_contract_snapshots,
)


def test_all_qualification_sports_are_available_to_slate_and_cli() -> None:
    assert {"mlb", "nba", "wnba", "nfl"} <= set(POLYMARKET_SPORT_LEAGUES)


def test_slate_normalizes_executable_side_prices_and_lines() -> None:
    event = {
        "id": "event-1",
        "slug": "wnba-away-home-2026-07-14",
        "title": "Away vs. Home",
        "startTime": "2026-07-14T23:00:00Z",
        "markets": [
            {
                "id": "market-1",
                "slug": "total-market",
                "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_TOTAL",
                "question": "O/U 160.5",
                "line": 160.5,
                "marketSides": [
                    {
                        "id": "long",
                        "description": "Over",
                        "long": True,
                        "quote": {"value": "0.47"},
                    },
                    {
                        "id": "short",
                        "description": "Under",
                        "long": False,
                        "quote": {"value": "0.55"},
                    },
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/leagues/wnba/events"
        return httpx.Response(200, json={"events": [event]})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    slate = PolymarketUSClient("https://example.test", http_client).slate("WNBA", date(2026, 7, 14))

    sides = slate[0]["markets"][0]["sides"]
    assert [side["selection"] for side in sides] == ["over", "under"]
    assert [side["line"] for side in sides] == [160.5, 160.5]
    assert sides[0]["american_odds"] == 113
    assert sides[1]["american_odds"] == -122


def test_slate_skips_market_with_boundary_probability_instead_of_aborting() -> None:
    event = {
        "id": "event-boundary",
        "slug": "wnba-boundary-2026-07-17",
        "title": "Away vs. Home",
        "startTime": "2026-07-17T23:00:00Z",
        "markets": [
            {
                "id": "bad-market",
                "slug": "bad-market",
                "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
                "marketSides": [
                    {"id": "bad-long", "description": "Away", "long": True, "quote": {"value": "0"}},
                    {"id": "bad-short", "description": "Home", "long": False, "quote": {"value": "1"}},
                ],
            },
            {
                "id": "good-market",
                "slug": "good-market",
                "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
                "marketSides": [
                    {"id": "good-long", "description": "Away", "long": True, "quote": {"value": "0.45"}},
                    {"id": "good-short", "description": "Home", "long": False, "quote": {"value": "0.57"}},
                ],
            },
        ],
    }

    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"events": [event]}))
    )
    slate = PolymarketUSClient("https://example.test", http_client).slate(
        "WNBA", date(2026, 7, 17)
    )

    assert [market["market_slug"] for market in slate[0]["markets"]] == ["good-market"]


def test_snapshot_store_uses_last_observation_before_game(tmp_path) -> None:
    store = PolymarketSnapshotStore(tmp_path / "snapshots.jsonl")
    for observed, price in (
        ("2026-07-14T22:00:00Z", 0.51),
        ("2026-07-14T22:59:00Z", 0.53),
        ("2026-07-14T23:01:00Z", 0.99),
    ):
        store.append(
            {
                "market_slug": "market",
                "observed_at_utc": observed,
                "long": {"price": price},
                "short": {"price": 1 - price},
            }
        )

    closing = store.closing_snapshot("market", "2026-07-14T23:00:00Z")
    assert closing["long"]["price"] == 0.53


def test_snapshot_uses_executable_asks_for_both_sides() -> None:
    market = {
        "market": {
            "id": "1",
            "gameStartTime": "2026-07-14T23:00:00Z",
            "marketSides": [
                {"description": "Away", "long": True},
                {"description": "Home", "long": False},
            ],
        }
    }
    book = {
        "marketData": {
            "state": "MARKET_STATE_OPEN",
            "transactTime": "2026-07-14T22:00:00Z",
            "bestAsk": {"value": "0.46"},
            "bestBid": {"value": "0.43"},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=market if "/market/slug/" in request.url.path else book)

    client = PolymarketUSClient("https://example.test", httpx.Client(transport=httpx.MockTransport(handler)))
    snapshot = client.snapshot("market", datetime(2026, 7, 14, 22, tzinfo=timezone.utc))
    assert snapshot["long"]["price"] == 0.46
    assert snapshot["short"]["price"] == 0.57
    assert snapshot["long"]["midpoint"] == 0.445
    assert snapshot["short"]["midpoint"] == 0.555
    assert json.dumps(snapshot)


def test_even_price_converts_to_valid_american_odds() -> None:
    assert probability_to_american(0.5) == 100


def test_slate_capture_stores_prospective_bbo_by_sport_and_date(tmp_path) -> None:
    class FakeClient:
        def snapshot(self, slug: str) -> dict:
            return {
                "market_slug": slug,
                "event_start_utc": "2026-07-18T23:00:00Z",
                "observed_at_utc": "2026-07-17T12:00:00Z",
                "long": {"ask": 0.55},
                "short": {"ask": 0.47},
            }

    result = capture_slate_snapshots(
        FakeClient(),
        {
            "MLB": [
                {
                    "event_id": "event-1",
                    "markets": [
                        {"market_slug": "market-1", "market_type": "moneyline", "line": None}
                    ],
                }
            ]
        },
        tmp_path,
        "2026-07-18",
    )

    assert result["captured"] == 1
    stored = json.loads(
        (tmp_path / "odds/mlb/2026-07-18/polymarket_snapshots.jsonl").read_text()
    )
    assert stored["timestamp_valid"] is True
    assert stored["usage"] == "prospective_executable_bbo"


def test_slate_capture_skips_sports_outside_qualification_scope(tmp_path) -> None:
    class FailIfCalled:
        def snapshot(self, slug: str) -> dict:
            raise AssertionError(f"unexpected snapshot call for {slug}")

    result = capture_slate_snapshots(
        FailIfCalled(),
        {"EPL": [{"event_id": "event-1", "markets": [{"market_slug": "soccer-1"}]}]},
        tmp_path,
        "2026-07-18",
    )

    assert result["captured"] == 0
    assert result["skipped_nonqualification_contracts"] == 1


def test_ledger_price_refresh_deduplicates_and_never_discovers_a_broad_slate(tmp_path) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.slugs = []

        def snapshot(self, slug: str) -> dict:
            self.slugs.append(slug)
            return {
                "market_slug": slug,
                "event_start_utc": (
                    "2026-07-19T23:00:00Z" if slug == "mlb-next-day" else "2026-07-18T23:00:00Z"
                ),
                "observed_at_utc": "2026-07-17T12:05:00Z",
                "long": {"ask": 0.55},
                "short": {"ask": 0.47},
            }

    store = PolymarketSnapshotStore.for_sport_date(tmp_path, "wnba", "2026-07-18")
    store.append(
        {
            "market_slug": "wnba-away-home",
            "event_start_utc": "2026-07-18T23:00:00Z",
            "observed_at_utc": "2026-07-17T12:00:00Z",
            "league": "WNBA",
            "event_id": "event-1",
            "market_type": "moneyline",
            "line": None,
            "long": {"ask": 0.54},
            "short": {"ask": 0.48},
        }
    )
    next_day_store = PolymarketSnapshotStore.for_sport_date(
        tmp_path, "mlb", "2026-07-19"
    )
    next_day_store.append(
        {
            "market_slug": "mlb-next-day",
            "event_start_utc": "2026-07-19T23:00:00Z",
            "observed_at_utc": "2026-07-17T12:00:00Z",
            "league": "MLB",
            "event_id": "event-2",
            "market_type": "moneyline",
            "line": None,
            "long": {"ask": 0.57},
            "short": {"ask": 0.45},
        }
    )
    client = FakeClient()

    result = refresh_contract_snapshots(
        client,
        [
            {"sport": "wnba", "game_date": "2026-07-18", "market_slug": "wnba-away-home"},
            {"sport": "wnba", "game_date": "2026-07-18", "market_slug": "wnba-away-home"},
            {"sport": "mlb", "game_date": "2026-07-19", "market_slug": "mlb-next-day"},
        ],
        tmp_path,
        "2026-07-18",
    )

    assert client.slugs == ["mlb-next-day", "wnba-away-home"]
    assert result["requested_contracts"] == 3
    assert result["unique_contracts"] == 2
    assert result["refreshed"] == 2
    assert result["broad_discovery_attempted"] is False
    assert result["scope"] == "all_open_visible_ledger_contracts_only"
    latest = store.latest("wnba-away-home")
    assert latest["market_type"] == "moneyline"
    assert latest["event_id"] == "event-1"
    assert next_day_store.latest("mlb-next-day")["event_id"] == "event-2"


def test_ledger_price_refresh_skips_unmapped_contract_without_network_call(tmp_path) -> None:
    class FailIfCalled:
        def snapshot(self, slug: str) -> dict:
            raise AssertionError(f"unexpected broad lookup for {slug}")

    result = refresh_contract_snapshots(
        FailIfCalled(),
        [{"sport": "mlb", "market_slug": "unknown-market"}],
        tmp_path,
        "2026-07-18",
    )

    assert result["refreshed"] == 0
    assert result["failures"] == 1
    assert "broad discovery was not attempted" in result["failure_details"][0]["reason"]
