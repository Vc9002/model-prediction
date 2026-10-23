import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from model_prediction.data_sources.polymarket_us import (
    POLYMARKET_SPORT_LEAGUES,
    PolymarketSnapshotStore,
    PolymarketUSClient,
    capture_slate_snapshots,
    probability_to_american,
    refresh_contract_snapshots,
)


def test_events_paginates_across_multiple_pages() -> None:
    """events() used to hard-cap at a single page (limit=200) -- a league
    with more events than that silently lost the rest. Confirms pagination
    walks pages via `offset` until a short page signals the last one."""
    pages = {
        0: [{"id": f"evt-{i}"} for i in range(3)],
        3: [{"id": f"evt-{i}"} for i in range(3, 5)],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        assert int(request.url.params["limit"]) == 3
        return httpx.Response(200, json={"events": pages.get(offset, [])})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    events = PolymarketUSClient("https://example.test", http_client).events("WNBA", limit=3)
    assert [event["id"] for event in events] == [f"evt-{i}" for i in range(5)]


def test_events_raises_instead_of_looping_forever_on_a_runaway_api() -> None:
    """P1: the pagination loop added for the above fix had no upper bound --
    an API that ignores `offset` and always returns a full page would hang
    the daily pipeline in an unbounded network loop with ever-growing
    memory. Confirms a buggy/malicious always-full-page API raises a clear
    error instead."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"events": [{"id": "same"}] * 3})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PolymarketUSClient("https://example.test", http_client)
    try:
        client.events("WNBA", limit=3)
    except RuntimeError as error:
        assert "did not terminate" in str(error)
    else:
        raise AssertionError("a runaway paginated API did not raise")


def test_get_retries_past_transient_429_then_succeeds(monkeypatch) -> None:
    """The gateway throttles bursts from the snapshot pool with plain 429s
    (2026-08-24: ~1,700 dropped book fetches in one day, concentrated in
    ITF tennis and esports). `_get` used to raise on the first 429 --
    losing that market's snapshot for the day -- instead of retrying past
    what is usually a transient burst-limit response."""
    sleeps: list[float] = []
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.time.sleep", sleeps.append)

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, json={"events": []})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PolymarketUSClient("https://example.test", http_client)
    result = client._get("/v2/leagues/wnba/events")

    assert result == {"events": []}
    assert attempts["count"] == 3
    assert len(sleeps) == 2


def test_get_raises_after_exhausting_429_retries(monkeypatch) -> None:
    """A persistently rate-limited endpoint must still surface as an error
    (caught by the existing snapshot-loop failure_details path) rather than
    retrying forever."""
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Too Many Requests")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PolymarketUSClient("https://example.test", http_client)
    try:
        client._get("/v2/leagues/wnba/events")
    except httpx.HTTPStatusError as error:
        assert error.response.status_code == 429
    else:
        raise AssertionError("persistent 429s did not raise")


def test_all_qualification_sports_are_available_to_slate_and_cli() -> None:
    assert {"mlb", "nba", "wnba", "nfl"} <= set(POLYMARKET_SPORT_LEAGUES)
    assert "esports" in POLYMARKET_SPORT_LEAGUES


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
        if request.url.path == "/v2/leagues/wnba/events":
            return httpx.Response(200, json={"events": [{**event, "markets": []}]})
        assert request.url.path == "/v1/events/slug/wnba-away-home-2026-07-14"
        return httpx.Response(200, json={"event": event})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    slate = PolymarketUSClient("https://example.test", http_client).slate("WNBA", date(2026, 7, 14))

    sides = slate[0]["markets"][0]["sides"]
    assert [side["selection"] for side in sides] == ["over", "under"]
    assert [side["line"] for side in sides] == [160.5, 160.5]
    assert sides[0]["american_odds"] == 113
    assert sides[1]["american_odds"] == -122


def test_mlb_slate_hydrates_game_detail_and_captures_live_nrfi_yrfi_contract() -> None:
    summary = {
        "id": "95706",
        "slug": "mlb-phi-az-2026-09-01",
        "title": "Philadelphia Phillies vs. Arizona Diamondbacks",
        "startTime": "2026-09-02T01:40:00Z",
        "markets": [],
    }
    detail = {
        **summary,
        "markets": [
            {
                "id": "584529",
                "slug": "astatc-mlb-phi-az-2026-09-01-yrfi",
                "question": "Will any run be scored in the 1st inning of PHI vs AZ?",
                "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_PROP",
                "sportsMarketType": "baseball_team_first_inning_run",
                "line": 1,
                "marketSides": [
                    {"id": "yes", "description": "Yes", "long": True, "quote": {"value": "0.45"}},
                    {"id": "no", "description": "No", "long": False, "quote": {"value": "0.56"}},
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/leagues/mlb/events":
            return httpx.Response(200, json={"events": [summary]})
        assert request.url.path == "/v1/events/slug/mlb-phi-az-2026-09-01"
        return httpx.Response(200, json={"event": detail})

    client = PolymarketUSClient("https://example.test", httpx.Client(transport=httpx.MockTransport(handler)))
    slate = client.slate("MLB", date(2026, 9, 1))

    assert len(slate) == 1
    market = slate[0]["markets"][0]
    assert market["market_type"] == "nrfi"
    assert market["market_slug"] == "astatc-mlb-phi-az-2026-09-01-yrfi"
    assert [(side["selection"], side["is_long"]) for side in market["sides"]] == [
        ("yes", True),
        ("no", False),
    ]


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
    slate = PolymarketUSClient("https://example.test", http_client).slate("WNBA", date(2026, 7, 17))

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
    snapshot = client.snapshot("market", datetime(2026, 7, 14, 22, tzinfo=UTC))
    assert snapshot["long"]["price"] == 0.46
    assert snapshot["short"]["price"] == 0.57
    assert snapshot["long"]["midpoint"] == 0.445
    assert snapshot["short"]["midpoint"] == 0.555
    assert snapshot["reconstructed"] is False
    assert snapshot["reconstruction_status_source"] == "live_gateway_market_and_book_response"
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
                    "markets": [{"market_slug": "market-1", "market_type": "moneyline", "line": None}],
                }
            ]
        },
        tmp_path,
        "2026-07-18",
    )

    assert result["captured"] == 1
    stored = json.loads((tmp_path / "odds/mlb/2026-07-18/polymarket_snapshots.jsonl").read_text())
    assert stored["timestamp_valid"] is True
    assert stored["reconstructed"] is None
    assert stored["reconstruction_status_source"] is None
    assert stored["usage"] == "prospective_executable_bbo"


def test_sport_slate_surfaces_a_failed_league_distinctly_from_an_empty_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/leagues/lol/events" in request.url.path:
            return httpx.Response(500, json={"error": "gateway down"})
        return httpx.Response(200, json={"events": []})

    client = PolymarketUSClient("https://example.test", httpx.Client(transport=httpx.MockTransport(handler)))

    result = client.sport_slate("esports", date(2026, 7, 27))

    assert result.events["LOL"] == []
    assert "LOL" in result.errors
    # A league whose fetch genuinely returned zero events must not appear in
    # errors -- that's what distinguishes a real outage from an empty day.
    assert "CS2" not in result.errors
    assert result.events["CS2"] == []


def test_slate_capture_summary_reflects_missing_ask_and_invalid_timestamp(tmp_path) -> None:
    # Two contracts: one clean, one with a missing ask AND an observed_at_utc
    # after event_start_utc (stale/invalid). The aggregate summary must not
    # hardcode timestamp_valid=True or report status="ok" when either
    # per-snapshot problem is present -- both must be visible to the caller.
    class FakeClient:
        def snapshot(self, slug: str) -> dict:
            if slug == "clean":
                return {
                    "market_slug": slug,
                    "event_start_utc": "2026-07-18T23:00:00Z",
                    "observed_at_utc": "2026-07-17T12:00:00Z",
                    "long": {"ask": 0.55},
                    "short": {"ask": 0.47},
                }
            return {
                "market_slug": slug,
                "event_start_utc": "2026-07-18T23:00:00Z",
                "observed_at_utc": "2026-07-19T00:00:00Z",  # after event start
                "long": {"ask": None},
                "short": {"ask": 0.47},
            }

    result = capture_slate_snapshots(
        FakeClient(),
        {
            "MLB": [
                {
                    "event_id": "event-1",
                    "markets": [
                        {"market_slug": "clean", "market_type": "moneyline", "line": None},
                        {"market_slug": "stale", "market_type": "moneyline", "line": None},
                    ],
                }
            ]
        },
        tmp_path,
        "2026-07-18",
    )

    assert result["captured"] == 2
    assert result["failures"] == 0
    assert result["missing_executable_ask"] == 1
    assert result["timestamp_invalid_count"] == 1
    assert result["timestamp_valid"] is False
    assert result["status"] == "partial"


def test_slate_capture_skips_sports_outside_qualification_scope(tmp_path) -> None:
    # Soccer is now in BBO_CAPTURE_SPORTS (research pipeline); EPL contracts
    # should be captured, not skipped.
    class FakeClient:
        def snapshot(self, slug: str) -> dict:
            return {
                "market_slug": slug,
                "event_start_utc": "2026-07-18T19:00:00Z",
                "observed_at_utc": "2026-07-18T18:00:00Z",
                "long": {
                    "description": "home",
                    "ask": 0.52,
                    "bid": 0.50,
                    "bid_size": 100,
                    "ask_size": 100,
                    "midpoint": 0.51,
                },
                "short": {
                    "description": "away",
                    "ask": 0.50,
                    "bid": 0.48,
                    "bid_size": 100,
                    "ask_size": 100,
                    "midpoint": 0.49,
                },
            }

    result = capture_slate_snapshots(
        FakeClient(),
        {"EPL": [{"event_id": "event-1", "markets": [{"market_slug": "soccer-1"}]}]},
        tmp_path,
        "2026-07-18",
    )

    assert result["captured"] == 1
    assert result["skipped_nonqualification_contracts"] == 0


def test_slate_capture_includes_ncaaf_college_football(tmp_path) -> None:
    class FakeClient:
        def snapshot(self, slug: str) -> dict:
            return {
                "market_slug": slug,
                "event_start_utc": "2026-09-05T19:00:00Z",
                "observed_at_utc": "2026-09-05T18:00:00Z",
                "long": {"description": "home", "ask": 0.55, "bid": 0.50},
                "short": {"description": "away", "ask": 0.50, "bid": 0.45},
            }

    result = capture_slate_snapshots(
        FakeClient(),
        {"NCAAF": [{"event_id": "cfb-1", "markets": [{"market_slug": "asc-cfb-test"}]}]},
        tmp_path,
        "2026-09-05",
    )

    assert result["captured"] == 1
    assert result["skipped_nonqualification_contracts"] == 0
    assert (tmp_path / "odds" / "ncaaf" / "2026-09-05" / "polymarket_snapshots.jsonl").exists()


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
    next_day_store = PolymarketSnapshotStore.for_sport_date(tmp_path, "mlb", "2026-07-19")
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


def test_get_honors_retry_after_and_backs_off_with_jitter(monkeypatch) -> None:
    """A numeric Retry-After is used as-is (capped at 10s); without it the
    delay grows with attempts instead of retrying every worker in lockstep."""
    sleeps: list[float] = []
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.time.sleep", sleeps.append)

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        if attempts["count"] == 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = PolymarketUSClient("https://example.test", http_client)

    assert client._get("/v1/markets/x/book") == {"ok": True}
    assert sleeps[0] == 3.0
    assert 1.0 <= sleeps[1] <= 1.25


def _line_market(market_id: str, legacy_type: str, v2: str, line: float) -> dict:
    return {
        "id": market_id,
        "slug": f"slug-{market_id}",
        "sportsMarketTypeV2": v2,
        "sportsMarketType": legacy_type,
        "line": line,
        "marketSides": [
            {"id": f"{market_id}-o", "description": "Over", "long": True, "quote": {"value": "0.52"}},
            {"id": f"{market_id}-u", "description": "Under", "long": False, "quote": {"value": "0.49"}},
        ],
    }


def test_nba_slate_hydrates_detail_and_keeps_only_full_game_totals() -> None:
    """The NBA league feed carries only the winner market; spreads/totals live in
    the per-event detail, where the generic TOTAL type also covers quarter, half
    and team-total markets that must not be priced as the full-game total."""
    total = "SPORTS_MARKET_TYPE_TOTAL"
    summary = {
        "id": "1",
        "slug": "nba-mil-was-2026-10-21",
        "title": "MIL vs WAS",
        "startTime": "2026-10-21T23:00:00Z",
        "markets": [],
    }
    detail = {
        **summary,
        "markets": [
            _line_market("full", "basketball_team_full_game_total", total, 224.5),
            _line_market("q1", "basketball_team_first_quarter_total", total, 58.5),
            _line_market("half", "basketball_team_first_half_total", total, 114.5),
            _line_market("team", "basketball_team_total_points", total, 111.5),
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/leagues/nba/events":
            return httpx.Response(200, json={"events": [summary]})
        assert request.url.path == "/v1/events/slug/nba-mil-was-2026-10-21"
        return httpx.Response(200, json={"event": detail})

    client = PolymarketUSClient("https://example.test", httpx.Client(transport=httpx.MockTransport(handler)))
    markets = client.slate("NBA", date(2026, 10, 21))[0]["markets"]
    assert [(m["market_type"], m["line"]) for m in markets] == [("total", 224.5)]


def test_hydrated_leagues_keep_only_full_game_lines_for_hockey_soccer_and_esports() -> None:
    spread, total = "SPORTS_MARKET_TYPE_SPREAD", "SPORTS_MARKET_TYPE_TOTAL"
    cases = {
        "NHL": (
            "nhl",
            [
                _line_market("full", "hockey_team_full_game_total", total, 5.5),
                _line_market("p1", "hockey_team_first_period_total", total, 1.5),
                _line_market("pl", "hockey_team_full_game_spread", spread, -1.5),
                _line_market("p2", "hockey_team_second_period_spread", spread, -0.5),
            ],
            [("total", 5.5), ("spread", -1.5)],
        ),
        "EPL": (
            "epl",
            [
                _line_market("full", "soccer_team_full_game_total", total, 2.5),
                _line_market("half", "soccer_team_second_half_total", total, 1.5),
                _line_market("hcp", "soccer_team_full_game_spread", spread, -1.5),
            ],
            [("total", 2.5), ("spread", -1.5)],
        ),
        "CS2": (
            "cs2",
            [
                _line_market("maps", "esports_series_total_maps", total, 2.5),
                _line_market("hcp", "esports_series_map_handicap", spread, -1.5),
                _line_market("kills", "esports_map_total_kills", total, 40.5),
            ],
            [("total", 2.5), ("spread", -1.5)],
        ),
    }
    for league, (slug_league, markets, expected) in cases.items():
        summary = {
            "id": "1",
            "slug": f"{slug_league}-a-b-2026-10-21",
            "title": "A vs B",
            "startTime": "2026-10-21T23:00:00Z",
            "markets": [],
        }

        def handler(request: httpx.Request, summary=summary, markets=markets) -> httpx.Response:
            if request.url.path.endswith("/events"):
                return httpx.Response(200, json={"events": [summary]})
            return httpx.Response(200, json={"event": {**summary, "markets": markets}})

        client = PolymarketUSClient(
            "https://example.test", httpx.Client(transport=httpx.MockTransport(handler))
        )
        got = client.slate(league, date(2026, 10, 21))[0]["markets"]
        assert sorted((m["market_type"], m["line"]) for m in got) == sorted(expected), league


def _slate_market(slug: str = "mkt-1") -> dict:
    return {
        "market_id": "42",
        "market_slug": slug,
        "market_type": "spread",
        "line": -3.5,
        "sides": [
            {"description": "Hawks -3.5", "team": "Hawks", "is_long": True, "line": -3.5},
            {"description": "Magic +3.5", "team": "Magic", "is_long": False, "line": 3.5},
        ],
    }


def test_snapshot_from_slate_matches_snapshot_with_one_request() -> None:
    raw_market = {
        "market": {
            "id": "42",
            "gameStartTime": "2026-10-22T23:00:00Z",
            "line": -3.5,
            "marketSides": [
                {"description": "Hawks -3.5", "long": True, "team": {"name": "Hawks"}},
                {"description": "Magic +3.5", "long": False, "team": {"name": "Magic"}},
            ],
        }
    }
    book = {
        "marketData": {
            "state": "MARKET_STATE_OPEN",
            "transactTime": "2026-10-22T20:00:00Z",
            "bestAsk": {"value": "0.46"},
            "bestBid": {"value": "0.43"},
        }
    }
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=raw_market if "/market/slug/" in request.url.path else book)

    client = PolymarketUSClient("https://example.test", httpx.Client(transport=httpx.MockTransport(handler)))
    moment = datetime(2026, 10, 22, 20, tzinfo=UTC)
    full = client.snapshot("mkt-1", moment)
    assert len(paths) == 2
    paths.clear()
    event = {"event_start_utc": "2026-10-22T23:00:00Z"}
    fast = client.snapshot_from_slate(event, _slate_market(), moment)
    assert paths == ["/v1/markets/mkt-1/book"]
    assert fast == full


def test_get_retries_connect_errors_then_succeeds_and_raises_when_exhausted(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.time.sleep", sleeps.append)
    calls = {"count": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
        return httpx.Response(200, json={"ok": True})

    client = PolymarketUSClient("https://example.test", httpx.Client(transport=httpx.MockTransport(flaky)))
    assert client._get("/v1/x") == {"ok": True}
    assert calls["count"] == 3 and len(sleeps) == 2

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

    client = PolymarketUSClient("https://example.test", httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(httpx.ConnectError):
        client._get("/v1/x")


class _SlateClient:
    def __init__(self) -> None:
        self.slate_calls: list[str] = []
        self.snapshot_calls: list[str] = []

    def snapshot_from_slate(self, event: dict, market: dict) -> dict:
        self.slate_calls.append(market["market_slug"])
        return {
            "market_slug": market["market_slug"],
            "event_start_utc": event["event_start_utc"],
            "observed_at_utc": "2026-10-22T10:00:00Z",
            "long": {"ask": 0.55},
            "short": {"ask": 0.47},
        }

    def snapshot(self, slug: str) -> dict:
        self.snapshot_calls.append(slug)
        raise AssertionError("snapshot() must not be used when the slate market is complete")


def _events(now: datetime) -> dict:
    def event(event_id: str, hours: float, slug: str) -> dict:
        start = (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"event_id": event_id, "event_start_utc": start, "markets": [_slate_market(slug)]}

    return {
        "MLB": [event("soon", 2, "soon-mkt"), event("later", 30, "later-mkt"), event("live", -1, "live-mkt")]
    }


def test_capture_horizon_skips_only_events_starting_beyond_it(tmp_path) -> None:
    now = datetime(2026, 10, 22, 10, tzinfo=UTC)
    client = _SlateClient()
    result = capture_slate_snapshots(client, _events(now), tmp_path, "2026-10-22", horizon_hours=12, now=now)
    assert sorted(client.slate_calls) == ["live-mkt", "soon-mkt"]
    assert client.snapshot_calls == []
    assert result["captured"] == 2
    assert result["skipped_beyond_horizon"] == 1
    assert result["horizon_hours"] == 12


def test_capture_without_horizon_keeps_every_event(tmp_path) -> None:
    now = datetime(2026, 10, 22, 10, tzinfo=UTC)
    client = _SlateClient()
    result = capture_slate_snapshots(client, _events(now), tmp_path, "2026-10-22")
    assert result["captured"] == 3
    assert result["skipped_beyond_horizon"] == 0


def test_capture_falls_back_to_snapshot_for_incomplete_slate_market(tmp_path) -> None:
    class Legacy:
        def snapshot(self, slug: str) -> dict:
            return {
                "market_slug": slug,
                "event_start_utc": "2026-10-22T23:00:00Z",
                "observed_at_utc": "2026-10-22T10:00:00Z",
                "long": {"ask": 0.55},
                "short": {"ask": 0.47},
            }

    events = {
        "MLB": [
            {"event_id": "e", "markets": [{"market_slug": "m", "market_type": "moneyline", "line": None}]}
        ]
    }
    assert capture_slate_snapshots(Legacy(), events, tmp_path, "2026-10-22")["captured"] == 1
