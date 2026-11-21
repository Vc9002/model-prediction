import json
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import httpx
import pytest

from model_prediction.data_sources import polymarket_us
from model_prediction.data_sources.polymarket_us import (
    POLYMARKET_SPORT_LEAGUES,
    PolymarketSnapshotStore,
    PolymarketUSClient,
    capture_slate_snapshots,
    probability_to_american,
    refresh_contract_snapshots,
)


@pytest.fixture(autouse=True)
def _unpaced_capture(monkeypatch) -> None:
    """Stub clients answer instantly; the production 0.5 s spacing would only slow these tests.

    Pacing itself is pinned by the tests that set the interval explicitly.
    """
    monkeypatch.setattr(polymarket_us, "CAPTURE_MIN_REQUEST_INTERVAL_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(polymarket_us, "EVENT_DETAIL_MIN_REQUEST_INTERVAL_SECONDS", 0.0, raising=False)


class _FakeClock:
    """Deterministic stand-in for time.monotonic/time.sleep: sleeping advances the clock."""

    def __init__(self, monkeypatch) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        monkeypatch.setattr("model_prediction.data_sources.polymarket_us.time.monotonic", lambda: self.now)
        monkeypatch.setattr("model_prediction.data_sources.polymarket_us.time.sleep", self.sleep)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def snapshot(self, slug: str) -> dict:
        self.calls.append(slug)
        return _stub_snapshot(slug)


def _rate_limit_error(slug: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://example.test/{slug}")
    return httpx.HTTPStatusError(
        "rate limited", request=request, response=httpx.Response(429, request=request)
    )


def _stub_snapshot(slug: str) -> dict:
    return {
        "market_slug": slug,
        "event_start_utc": "2026-07-18T23:00:00Z",
        "observed_at_utc": "2026-07-17T12:00:00Z",
        "long": {"ask": 0.55},
        "short": {"ask": 0.47},
    }


def _moneyline_events(count: int) -> dict:
    return {
        "MLB": [
            {
                "event_id": "event-1",
                "markets": [
                    {"market_slug": f"market-{index}", "market_type": "moneyline"} for index in range(count)
                ],
            }
        ]
    }


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


def test_forecast_client_can_bound_retries_without_changing_capture_default(monkeypatch) -> None:
    sleeps = []
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.time.sleep", sleeps.append)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "10"}, request=request)

    client = PolymarketUSClient(
        "https://example.test",
        httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=2,
    )
    with pytest.raises(httpx.HTTPStatusError):
        client._get("/book")
    assert attempts == 2
    assert sleeps == [10]
    assert PolymarketUSClient().max_attempts == 6


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


def test_closing_snapshot_never_returns_a_quote_taken_after_the_contracts_own_kickoff(tmp_path) -> None:
    """2026-10-24: six NCAAF rows kicking 19:30Z-02:00Z had been priced on a 16:00Z game's
    total contract; bounded only by the row's kickoff, its 17:03Z in-play 99c book became
    their close."""
    store = PolymarketSnapshotStore(tmp_path / "snapshots.jsonl")
    for observed, under_ask in (("2026-10-24T11:45:30Z", 0.56), ("2026-10-24T17:03:06Z", 0.99)):
        store.append(
            {
                "market_slug": "tsc-cfb-nhc-cencon-2026-10-24-total-56pt5",
                "event_start_utc": "2026-10-24T16:00:00Z",
                "observed_at_utc": observed,
                "market_state": "MARKET_STATE_OPEN",
                "market_type": "total",
                "line": 56.5,
                "long": {"description": "Over", "ask": 0.5},
                "short": {"description": "Under", "ask": under_ask},
            }
        )

    closing = store.closing_snapshot("tsc-cfb-nhc-cencon-2026-10-24-total-56pt5", "2026-10-24T19:30Z")

    assert closing is not None
    assert closing["short"]["ask"] == 0.56


def test_closing_snapshot_skips_a_book_that_is_not_open(tmp_path) -> None:
    store = PolymarketSnapshotStore(tmp_path / "snapshots.jsonl")
    for observed, state, price in (
        ("2026-11-14T15:00:00Z", "MARKET_STATE_OPEN", 0.52),
        ("2026-11-14T15:55:00Z", "MARKET_STATE_HALTED", 0.97),
    ):
        store.append(
            {
                "market_slug": "market",
                "event_start_utc": "2026-11-14T16:00:00Z",
                "observed_at_utc": observed,
                "market_state": state,
                "long": {"ask": price},
            }
        )

    closing = store.closing_snapshot("market", "2026-11-14T16:00:00Z")

    assert closing is not None and closing["long"]["ask"] == 0.52


def test_snapshot_store_tolerates_corrupted_lines_and_bad_timestamps(tmp_path) -> None:
    snapshot_path = tmp_path / "snapshots_corrupted.jsonl"
    store = PolymarketSnapshotStore(snapshot_path)
    store.append(
        {"market_slug": "market", "observed_at_utc": "2026-07-14T22:00:00Z", "long": {"price": 0.51}}
    )
    # Append malformed JSON line and missing/invalid timestamps
    with snapshot_path.open("a", encoding="utf-8") as handle:
        handle.write("MALFORMED JSON {{{ line\n")
        handle.write('{"market_slug": "market"}\n')
        handle.write('{"market_slug": "market", "observed_at_utc": "not-a-date"}\n')
    store.append(
        {"market_slug": "market", "observed_at_utc": "2026-07-14T22:30:00Z", "long": {"price": 0.55}}
    )

    closing = store.closing_snapshot("market", "2026-07-14T23:00:00Z")
    assert closing is not None
    assert closing["long"]["price"] == 0.55


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


def test_slate_capture_abandons_queue_only_after_consecutive_rate_limit_budget(tmp_path, monkeypatch) -> None:
    # Contract changed 2026-11-14: the first post-retry 429 used to end the queue
    # (1,750-2,050 contracts skipped per daily run). Now each 429 opens a shared
    # pause and the queue is abandoned only after the consecutive-429 budget.
    clock = _FakeClock(monkeypatch)

    class RateLimitedClient:
        calls = 0

        def snapshot(self, slug: str) -> dict:
            self.calls += 1
            raise _rate_limit_error(slug)

    source = RateLimitedClient()
    result = capture_slate_snapshots(
        source, _moneyline_events(3), tmp_path, "2026-07-18", max_workers=1, rate_limit_budget=2
    )
    assert source.calls == 2
    assert result["captured"] == 0
    assert result["failures"] == 3
    assert result["source_rate_limited"] is True
    assert result["skipped_after_rate_limit"] == 2
    assert result["rate_limit_pauses"] == 2
    assert result["status"] == "partial"
    # The first episode was waited out (5 s) before the contract was retried.
    assert sum(clock.sleeps) >= 5.0


def test_slate_capture_resumes_after_a_single_rate_limit(tmp_path, monkeypatch) -> None:
    clock = _FakeClock(monkeypatch)

    class OnceThrottledClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def snapshot(self, slug: str) -> dict:
            self.calls.append(slug)
            if len(self.calls) == 1:
                raise _rate_limit_error(slug)
            return _stub_snapshot(slug)

    source = OnceThrottledClient()
    result = capture_slate_snapshots(source, _moneyline_events(3), tmp_path, "2026-07-18", max_workers=1)

    assert result["captured"] == 3
    assert result["failures"] == 0
    assert result["source_rate_limited"] is False
    assert result["skipped_after_rate_limit"] == 0
    # The throttled contract is retried after the pause, not dropped.
    assert source.calls == ["market-0", "market-0", "market-1", "market-2"]
    assert result["rate_limit_pauses"] == 1
    assert sum(clock.sleeps) >= 5.0


def test_slate_capture_spaces_requests_by_the_minimum_interval(tmp_path, monkeypatch) -> None:
    clock = _FakeClock(monkeypatch)
    monkeypatch.setattr(polymarket_us, "CAPTURE_MIN_REQUEST_INTERVAL_SECONDS", 0.5, raising=False)
    seen: list[float] = []

    class TimedClient:
        def snapshot(self, slug: str) -> dict:
            seen.append(clock.now)
            return _stub_snapshot(slug)

    result = capture_slate_snapshots(
        TimedClient(), _moneyline_events(4), tmp_path, "2026-07-18", max_workers=1
    )

    assert result["captured"] == 4
    assert [round(later - earlier, 6) for earlier, later in pairwise(seen)] == [0.5] * 3


def test_slate_capture_pacing_is_shared_across_workers(tmp_path, monkeypatch) -> None:
    import threading
    import time

    monkeypatch.setattr(polymarket_us, "CAPTURE_MIN_REQUEST_INTERVAL_SECONDS", 0.1, raising=False)
    seen: list[float] = []
    lock = threading.Lock()

    class TimedClient:
        def snapshot(self, slug: str) -> dict:
            with lock:
                seen.append(time.perf_counter())
            return _stub_snapshot(slug)

    result = capture_slate_snapshots(
        TimedClient(), _moneyline_events(5), tmp_path, "2026-07-18", max_workers=4
    )

    assert result["captured"] == 5
    ordered = sorted(seen)
    # time.monotonic ticks at 15.6 ms on Windows, so allow that much slack per gap.
    assert min(later - earlier for earlier, later in pairwise(ordered)) >= 0.08


def _scheduled_event(event_id: str, league_now: datetime, hours: float, slugs: list[str]) -> dict:
    start = (league_now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "event_id": event_id,
        "event_start_utc": start,
        "markets": [{"market_slug": slug, "market_type": "moneyline"} for slug in slugs],
    }


def _scope_slate(now: datetime) -> dict:
    # Out-of-scope soccer kicks off first and has the most contracts; an NCAAF game
    # is already under way. Before 2026-11-14 this order (NBA, then kickoff time
    # across every league) spent the budget on EPL before any NCAAF game.
    return {
        "EPL": [_scheduled_event("epl-1", now, 1, ["epl-a", "epl-b", "epl-c"])],
        "NCAAF": [
            _scheduled_event("cfb-live", now, -1, ["cfb-live"]),
            _scheduled_event("cfb-2", now, 6, ["cfb-2"]),
            _scheduled_event("cfb-1", now, 5, ["cfb-1"]),
        ],
        "NBA": [_scheduled_event("nba-1", now, 3, ["nba-1"]), _scheduled_event("nba-2", now, 4, ["nba-2"])],
    }


def test_capture_fetches_scope_leagues_round_robin_before_out_of_scope_and_started(tmp_path) -> None:
    now = datetime(2026, 11, 14, 15, tzinfo=UTC)

    source = _Recorder()
    result = capture_slate_snapshots(
        source,
        _scope_slate(now),
        tmp_path,
        "2026-11-14",
        now=now,
        priority_leagues=frozenset({"NBA", "NCAAF"}),
        ladder_keep_slugs=frozenset({"epl-c"}),
        max_workers=1,
    )

    assert source.calls == [
        # Scope leagues interleaved, each in kickoff order.
        "nba-1",
        "cfb-1",
        "nba-2",
        "cfb-2",
        # Then out-of-scope leagues and started games, interleaved. The open pick
        # in EPL (epl-c) waits here: its league is switched off, so it must not
        # take primary budget ahead of in-scope games.
        "epl-a",
        "cfb-live",
        "epl-b",
        "epl-c",
    ]
    assert result["primary_contracts"] == 4 and result["secondary_contracts"] == 4


def test_capture_time_budget_reaches_every_scope_league_before_any_out_of_scope_one(
    tmp_path, monkeypatch
) -> None:
    clock = _FakeClock(monkeypatch)
    monkeypatch.setattr(polymarket_us, "CAPTURE_MIN_REQUEST_INTERVAL_SECONDS", 1.0, raising=False)
    now = datetime(2026, 11, 14, 15, tzinfo=UTC)

    source = _Recorder()
    # Room for three requests at one per second.
    result = capture_slate_snapshots(
        source,
        _scope_slate(now),
        tmp_path,
        "2026-11-14",
        now=now,
        priority_leagues=frozenset({"NBA", "NCAAF"}),
        max_workers=1,
        max_capture_seconds=2.5,
    )

    assert source.calls == ["nba-1", "cfb-1", "nba-2"]
    assert result["captured"] == 3
    assert result["skipped_after_time_budget"] == 5
    assert result["source_rate_limited"] is False
    assert clock.now <= 1003.0


def test_capture_time_budget_gives_every_event_its_moneyline_before_any_second_line(
    tmp_path, monkeypatch
) -> None:
    """Kickoff order alone kept an event's markets together, so a cut budget left late
    kickoffs with no quote at all (NCAAF Saturdays: ~470 /book contracts, ~357 per run)."""
    _FakeClock(monkeypatch)
    monkeypatch.setattr(polymarket_us, "CAPTURE_MIN_REQUEST_INTERVAL_SECONDS", 1.0, raising=False)
    now = datetime(2026, 11, 21, 15, tzinfo=UTC)

    def _line(slug: str, market_type: str, long_price: float) -> dict:
        return {
            "market_slug": slug,
            "market_type": market_type,
            "sides": [{"is_long": True, "price_probability": long_price}],
        }

    def _game(event_id: str, hours: float) -> dict:
        return {
            "event_id": event_id,
            "event_start_utc": (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "markets": [
                {"market_slug": f"{event_id}-ml", "market_type": "moneyline"},
                _line(f"{event_id}-spread-alt", "spread", 0.80),
                _line(f"{event_id}-spread-main", "spread", 0.51),
                _line(f"{event_id}-total-main", "total", 0.49),
            ],
        }

    source = _Recorder()
    capture_slate_snapshots(
        source,
        {"NCAAF": [_game("early", 1), _game("mid", 2), _game("late", 3)]},
        tmp_path,
        "2026-11-21",
        now=now,
        priority_leagues=frozenset({"NCAAF"}),
        max_workers=1,
        max_capture_seconds=5.5,
    )

    assert source.calls == [
        "early-ml",
        "mid-ml",
        "late-ml",
        # Then each event's most balanced spread and total, before any alternate rung.
        "early-spread-main",
        "early-total-main",
        "mid-spread-main",
    ]


def test_capture_secondary_tier_has_its_own_short_budget(tmp_path) -> None:
    now = datetime(2026, 11, 14, 15, tzinfo=UTC)

    source = _Recorder()
    result = capture_slate_snapshots(
        source,
        _scope_slate(now),
        tmp_path,
        "2026-11-14",
        now=now,
        priority_leagues=frozenset({"NBA", "NCAAF"}),
        max_workers=1,
        secondary_max_seconds=0,
    )

    assert source.calls == ["nba-1", "cfb-1", "nba-2", "cfb-2"]
    assert result["skipped_after_time_budget"] == 4
    assert result["captured_by_league"] == {"NBA": 2, "NCAAF": 2}


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


def test_slate_snapshot_uses_verified_bbo_when_full_book_is_rate_limited(monkeypatch) -> None:
    paths = []
    sleeps = []
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.time.sleep", sleeps.append)

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/book"):
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(
            200,
            json={
                "marketData": {
                    "marketSlug": "mkt-1",
                    "state": "MARKET_STATE_OPEN",
                    "bestBid": {"value": "0.58"},
                    "bestAsk": {"value": "0.61"},
                }
            },
        )

    client = PolymarketUSClient(
        "https://example.test", httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=1
    )
    event = {"event_start_utc": "2026-10-22T23:00:00Z"}
    snapshot = client.snapshot_from_slate(event, _slate_market(), datetime(2026, 10, 22, 20, tzinfo=UTC))
    assert paths == ["/v1/markets/mkt-1/book", "/v1/markets/mkt-1/bbo"]
    assert sleeps and sleeps[0] >= 1.0
    assert snapshot["long"]["ask"] == 0.61
    assert snapshot["short"]["ask"] == 0.42
    assert snapshot["long"]["ask_size"] is None
    assert snapshot["transact_time_utc"] is None
    assert snapshot["reconstruction_status_source"] == "live_gateway_market_and_bbo_response"


def test_slate_snapshot_prefers_bbo_without_requesting_full_book() -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "marketData": {
                    "marketSlug": "mkt-1",
                    "state": "MARKET_STATE_OPEN",
                    "bestBid": {"value": "0.58"},
                    "bestAsk": {"value": "0.61"},
                }
            },
        )

    client = PolymarketUSClient(
        "https://example.test", httpx.Client(transport=httpx.MockTransport(handler)), prefer_bbo=True
    )
    snapshot = client.snapshot_from_slate({"event_start_utc": "2026-10-22T23:00:00Z"}, _slate_market())
    assert paths == ["/v1/markets/mkt-1/bbo"]
    assert snapshot["long"]["ask"] == 0.61
    assert snapshot["long"]["ask_size"] is None


@pytest.mark.parametrize(
    "bad_bbo",
    [
        {
            "marketSlug": "other",
            "state": "MARKET_STATE_OPEN",
            "bestBid": {"value": "0.58"},
            "bestAsk": {"value": "0.61"},
        },
        {
            "marketSlug": "mkt-1",
            "state": "MARKET_STATE_SUSPENDED",
            "bestBid": {"value": "0.58"},
            "bestAsk": {"value": "0.61"},
        },
        {
            "marketSlug": "mkt-1",
            "state": "MARKET_STATE_OPEN",
            "bestBid": {"value": "0.61"},
            "bestAsk": {"value": "0.58"},
        },
    ],
)
def test_slate_snapshot_rejects_invalid_bbo_fallback(bad_bbo: dict, monkeypatch) -> None:
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.time.sleep", lambda duration: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/book"):
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, json={"marketData": bad_bbo})

    client = PolymarketUSClient(
        "https://example.test", httpx.Client(transport=httpx.MockTransport(handler)), max_attempts=1
    )
    with pytest.raises(ValueError, match="invalid_executable_bbo"):
        client.snapshot_from_slate({"event_start_utc": "2026-10-22T23:00:00Z"}, _slate_market())


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


def _ladder_event(league_prices: dict[str, float | None], market_type: str = "spread") -> dict:
    markets = []
    for slug, price in league_prices.items():
        market = _slate_market(slug)
        market["market_type"] = market_type
        if price is not None:
            market["sides"][0]["price_probability"] = price
        markets.append(market)
    return {"event_id": "g1", "event_start_utc": "2026-10-22T11:00:00Z", "markets": markets}


def test_ladder_keep_fetches_only_the_most_balanced_alternates_for_listed_leagues(tmp_path) -> None:
    prices = {"far": 0.95, "near": 0.52, "mid": 0.70, "close": 0.49, "nocache": None}
    events = {"NCAAF": [_ladder_event(prices)], "NBA": [_ladder_event(prices)]}
    client = _SlateClient()
    result = capture_slate_snapshots(client, events, tmp_path, "2026-10-22", ladder_keep=2)
    ncaaf_nba = sorted(client.slate_calls)
    # NCAAF keeps its 2 most balanced plus the uncached contract; NBA is untouched.
    assert ncaaf_nba.count("close") == 2 and ncaaf_nba.count("near") == 2
    assert ncaaf_nba.count("far") == 1 and ncaaf_nba.count("mid") == 1
    assert ncaaf_nba.count("nocache") == 2
    assert result["skipped_ladder_alternates"] == 2


def test_priority_league_shortlists_each_game_before_other_sports(tmp_path) -> None:
    first = _ladder_event({"far": 0.95, "near": 0.52, "close": 0.49})
    first["event_id"] = "nba-1"
    second = _ladder_event({"far2": 0.90, "near2": 0.51, "close2": 0.48})
    second["event_id"] = "nba-2"
    client = _SlateClient()
    result = capture_slate_snapshots(
        client,
        {"MLB": [_ladder_event({"mlb": 0.5})], "NBA": [first, second]},
        tmp_path,
        "2026-10-22",
        now=datetime(2026, 10, 22, 10, tzinfo=UTC),
        ladder_keep=2,
        ladder_leagues=frozenset({"NBA"}),
        priority_leagues=frozenset({"NBA"}),
        max_workers=1,
    )
    # Breadth-first: each game's most balanced line, then each game's second line.
    assert client.slate_calls[:4] == ["close", "near2", "near", "close2"]
    assert client.slate_calls[-1] == "mlb"
    assert result["skipped_ladder_alternates"] == 2
    assert result["nba_missing_quote_event_ids"]["spread"] == []
    assert result["nba_missing_quote_event_ids"]["total"] == ["nba-1", "nba-2"]


def test_ladder_keep_mapping_prices_each_league_its_own_rung_count(tmp_path) -> None:
    # 2026-11-14: one integer chosen first-match (NBA on the slate => 3) gave
    # NCAAB 3 rungs instead of 1 and NCAAF 3 instead of 2.
    prices = {"far": 0.95, "near": 0.52, "mid": 0.70, "close": 0.49}
    client = _SlateClient()
    result = capture_slate_snapshots(
        client,
        {"NBA": [_ladder_event(prices)], "NCAAB": [_ladder_event(prices)], "NCAAF": [_ladder_event(prices)]},
        tmp_path,
        "2026-10-22",
        ladder_keep={"NBA": 3, "NCAAF": 2, "NCAAB": 1},
        ladder_leagues=frozenset({"NBA", "NCAAF", "NCAAB"}),
    )
    calls = sorted(client.slate_calls)
    assert calls.count("close") == 3  # every league keeps its most balanced line
    assert calls.count("near") == 2  # NBA and NCAAF
    assert calls.count("mid") == 1  # NBA only
    assert calls.count("far") == 0
    assert result["skipped_ladder_alternates"] == 1 + 2 + 3
    assert result["ladder_keep"] == {"NBA": 3, "NCAAF": 2, "NCAAB": 1}


def test_cli_capture_passes_per_league_ladders_and_scope_priority(monkeypatch, tmp_path) -> None:
    from argparse import Namespace
    from types import SimpleNamespace

    from model_prediction.cli import commands

    events = {"NBA": [{"event_id": "n"}], "NCAAB": [{"event_id": "b"}], "EPL": [{"event_id": "e"}]}

    class FakeClient:
        base_url = "https://example.test"
        client = None

        def __init__(self, *args, **kwargs) -> None:
            pass

        def sport_slate(self, sport, game_date, timezone_name):
            return SimpleNamespace(events=events, errors={})

    seen: dict = {}

    def fake_capture(client, events_by_league, root, game_date, **kwargs):
        seen.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(commands, "PolymarketUSClient", FakeClient)
    monkeypatch.setattr(commands, "capture_slate_snapshots", fake_capture)
    monkeypatch.setattr(commands, "_open_pick_slugs", lambda root, sport=None: frozenset({"open-pick"}))
    monkeypatch.delenv("POLYMARKET_CAPTURE_LADDER_KEEP", raising=False)
    config = {
        "project": {"ledger_path": str(tmp_path / "ledger.xlsx")},
        "models": {"NBA": {"enabled": True}, "NCAAB": {"enabled": True}, "SOCCER": {"enabled": True}},
    }
    args = Namespace(
        provider="polymarket",
        league=None,
        all=False,
        sport="basketball",
        date="2026-11-14",
        timezone="America/New_York",
        no_snapshot_bbo=False,
    )

    commands._polymarket_slate(args, config)

    # NBA on the slate must not raise NCAAB's rung count above 1.
    assert seen["ladder_keep"]["NCAAB"] == 1 and seen["ladder_keep"]["NBA"] == 3
    assert {"NBA", "NCAAB", "NCAAF", "NFL"} <= seen["ladder_leagues"]
    assert seen["priority_leagues"] == frozenset({"NBA", "NCAAB"})
    assert seen["ladder_keep_slugs"] == frozenset({"open-pick"})

    monkeypatch.setenv("POLYMARKET_CAPTURE_LADDER_KEEP", "4")
    commands._polymarket_slate(args, config)
    assert set(seen["ladder_keep"].values()) == {4}


def test_ladder_keep_slugs_survive_pruning_without_using_up_the_quota(tmp_path) -> None:
    prices = {"far": 0.95, "near": 0.52, "mid": 0.70, "close": 0.49}
    client = _SlateClient()
    result = capture_slate_snapshots(
        client,
        {"NCAAF": [_ladder_event(prices)]},
        tmp_path,
        "2026-10-22",
        ladder_keep=1,
        ladder_keep_slugs=frozenset({"far"}),
    )
    # The open pick's line ("far") is fetched on top of the single shortlisted line.
    assert sorted(client.slate_calls) == ["close", "far"]
    assert result["skipped_ladder_alternates"] == 2


def test_open_pick_slugs_reads_main_and_flat_and_survives_a_ledger_error(monkeypatch, tmp_path) -> None:
    from model_prediction import main_ledgers
    from model_prediction.cli import commands

    class _Ledger:
        def __init__(self, rows: list[dict] | Exception) -> None:
            self._rows = rows

        def rows(self, filters: dict) -> list[dict]:
            assert filters == {"status": "open"}
            if isinstance(self._rows, Exception):
                raise self._rows
            return self._rows

    rows = [{"rationale": "Over 55.5 (tsc-cfb-aaa-bbb-2026-10-24-total-55pt5)"}, {"rationale": ""}]
    monkeypatch.setattr(main_ledgers, "main_ledger", lambda root, sport: _Ledger(rows))
    monkeypatch.setattr(main_ledgers, "flat_ledger", lambda root, sport: _Ledger(RuntimeError("boom")))
    assert commands._open_pick_slugs(tmp_path) == frozenset({"tsc-cfb-aaa-bbb-2026-10-24-total-55pt5"})


def test_ladder_keep_never_prunes_moneylines_or_other_market_types(tmp_path) -> None:
    events = {"NCAAF": [_ladder_event({"a": 0.9, "b": 0.8, "c": 0.7}, market_type="moneyline")]}
    client = _SlateClient()
    result = capture_slate_snapshots(client, events, tmp_path, "2026-10-22", ladder_keep=1)
    assert len(client.slate_calls) == 3 and result["skipped_ladder_alternates"] == 0


def test_ladder_keep_off_by_default(tmp_path) -> None:
    events = {"NCAAF": [_ladder_event({"a": 0.9, "b": 0.8, "c": 0.5})]}
    client = _SlateClient()
    result = capture_slate_snapshots(client, events, tmp_path, "2026-10-22")
    assert len(client.slate_calls) == 3 and result["skipped_ladder_alternates"] == 0


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


# --- event-detail quote pass (quick-261114-mzz) ------------------------------------------------

_DETAIL_NOW = datetime(2026, 10, 22, 10, tzinfo=UTC)


def _detail_market(slug: str, bid: float | None, ask: float | None, **overrides) -> dict:
    def quote(value: float | None):
        return None if value is None else {"value": f"{value:.4f}", "currency": "USD"}

    market = {
        "slug": slug,
        "status": "MARKET_STATUS_OPEN",
        "active": True,
        "closed": False,
        "ep3Status": "OPEN",
        "line": -3.5,
        "bestBidQuote": quote(bid),
        "bestAskQuote": quote(ask),
    }
    market.update(overrides)
    return market


def _detail(event_slug: str, markets: list[dict], age: float | None = 12.0):
    return polymarket_us.EventDetail(event={"slug": event_slug, "markets": markets}, cache_age_seconds=age)


def _detail_slate_event(event_slug: str, slugs: list[str], hours: float = 2) -> dict:
    start = (_DETAIL_NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "event_id": f"id-{event_slug}",
        "event_slug": event_slug,
        "title": f"Title {event_slug}",
        "event_start_utc": start,
        "markets": [_slate_market(slug) for slug in slugs],
    }


class _EventDetailClient(_SlateClient):
    def __init__(self, details: dict) -> None:
        super().__init__()
        self.details = details
        self.detail_calls: list[str] = []
        self.fetch_stamps: list[datetime] = []
        self.book_failures: dict[str, Exception] = {}

    def event_detail(self, slug: str):
        self.detail_calls.append(slug)
        self.fetch_stamps.append(polymarket_us.utc_now())
        outcome = self.details[slug]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def snapshot_from_slate(self, event: dict, market: dict) -> dict:
        failure = self.book_failures.get(market["market_slug"])
        if failure is not None:
            self.slate_calls.append(market["market_slug"])
            raise failure
        return super().snapshot_from_slate(event, market)


class _AdvancingClock:
    """utc_now() that moves one second per call, so capture order is observable."""

    def __init__(self, monkeypatch, start: datetime) -> None:
        self.now = start
        monkeypatch.setattr(polymarket_us, "utc_now", self)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def _read_rows(tmp_path, sport: str, day: str) -> list[dict]:
    path = PolymarketSnapshotStore.for_sport_date(tmp_path, sport, day).path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _detail_rows(tmp_path, sport: str, day: str) -> list[dict]:
    return [
        row
        for row in _read_rows(tmp_path, sport, day)
        if row.get("reconstruction_status_source") == "live_gateway_event_detail_quote"
    ]


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/x")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status, request=request))


def test_event_detail_returns_event_and_cdn_cache_age() -> None:
    slug = "cbb-a-b-2026-11-14"

    def make(headers: dict[str, str]) -> PolymarketUSClient:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == f"/v1/events/slug/{slug}"
            return httpx.Response(200, json={"event": {"slug": slug, "markets": []}}, headers=headers)

        return PolymarketUSClient(
            "https://example.test", httpx.Client(transport=httpx.MockTransport(handler))
        )

    detail = make({"Age": "27"}).event_detail(slug)
    assert isinstance(detail, polymarket_us.EventDetail)
    assert detail.event == {"slug": slug, "markets": []}
    assert detail.cache_age_seconds == 27.0
    assert make({}).event_detail(slug).cache_age_seconds is None
    assert make({"Age": "abc"}).event_detail(slug).cache_age_seconds is None


def test_event_detail_pass_writes_one_quote_per_quoted_slate_market_from_one_request(
    tmp_path, monkeypatch
) -> None:
    clock = _AdvancingClock(monkeypatch, _DETAIL_NOW + timedelta(seconds=60))
    quotes = {"m1": (0.55, 0.59), "m2": (0.40, 0.45), "m3": (0.62, 0.66)}
    client = _EventDetailClient(
        {"ev-1": _detail("ev-1", [_detail_market(slug, bid, ask) for slug, (bid, ask) in quotes.items()])}
    )
    events = {"NBA": [_detail_slate_event("ev-1", list(quotes))]}
    result = capture_slate_snapshots(client, events, tmp_path, "2026-10-22", now=_DETAIL_NOW, max_workers=1)

    assert client.detail_calls == ["ev-1"]
    assert result["event_detail_events"] == 1
    assert result["event_detail_captured"] == 3
    assert result["event_detail_failures"] == 0
    assert result["captured_by_source"]["live_gateway_event_detail_quote"] == 3
    rows = _detail_rows(tmp_path, "nba", "2026-10-22")
    assert sorted(row["market_slug"] for row in rows) == ["m1", "m2", "m3"]
    fetch_stamp = client.fetch_stamps[0]
    assert clock.now > fetch_stamp
    for row in rows:
        bid, ask = quotes[row["market_slug"]]
        assert row["usage"] == "prospective_executable_bbo"
        assert row["market_state"] == "MARKET_STATE_OPEN"
        assert row["reconstructed"] is False
        assert row["long"]["ask"] == ask and row["long"]["bid"] == bid
        assert row["short"]["ask"] == round(1 - bid, 6)
        assert row["short"]["bid"] == round(1 - ask, 6)
        assert row["long"]["bid_size"] is None and row["long"]["ask_size"] is None
        assert row["transact_time_utc"] is None
        assert row["quote_cache_age_seconds"] == 12.0
        assert row["timestamp_valid"] is True
        assert row["league"] == "NBA"
        assert row["event_id"] == "id-ev-1" and row["event_slug"] == "ev-1"
        assert row["market_type"] == "spread" and row["line"] == -3.5
        # The quote cannot predate the response that carried it.
        assert polymarket_us.parse_utc(row["observed_at_utc"]) > fetch_stamp


def test_event_detail_pass_skipped_for_clients_without_event_detail(tmp_path) -> None:
    client = _SlateClient()
    events = {"NBA": [_detail_slate_event("ev-1", ["m1", "m2", "m3"])]}
    result = capture_slate_snapshots(client, events, tmp_path, "2026-10-22", now=_DETAIL_NOW, max_workers=1)
    assert client.slate_calls == ["m1", "m2", "m3"]
    assert result["captured"] == 3
    assert result["event_detail_events"] == result["event_detail_captured"] == 0
    assert result["event_detail_failures"] == 0
    assert "live_gateway_event_detail_quote" not in result["captured_by_source"]


def test_event_detail_unquoted_crossed_or_closed_markets_fall_through_to_book_pass_first(tmp_path) -> None:
    markets = [
        _detail_market("m1", 0.50, 0.54),
        _detail_market("m2", 0.50, None),
        _detail_market("m3", 0.50, 0.54),
        _detail_market("m4", 0.60, 0.55),
        _detail_market("m5", 0.50, 0.54, closed=True),
    ]
    client = _EventDetailClient({"ev-1": _detail("ev-1", markets)})
    events = {"NBA": [_detail_slate_event("ev-1", ["m1", "m2", "m3", "m4", "m5"])]}
    result = capture_slate_snapshots(client, events, tmp_path, "2026-10-22", now=_DETAIL_NOW, max_workers=1)

    assert sorted(row["market_slug"] for row in _detail_rows(tmp_path, "nba", "2026-10-22")) == ["m1", "m3"]
    assert result["event_detail_captured"] == 2
    assert client.slate_calls == ["m2", "m4", "m5", "m1", "m3"]


def test_event_detail_http_error_falls_through_and_429_stops_the_pass(tmp_path) -> None:
    def build(first_outcome):
        client = _EventDetailClient(
            {
                "ev-a": first_outcome,
                "ev-b": _detail("ev-b", [_detail_market("b1", 0.50, 0.54), _detail_market("b2", 0.40, 0.44)]),
            }
        )
        events = {
            "NBA": [
                _detail_slate_event("ev-a", ["a1", "a2"], hours=2),
                _detail_slate_event("ev-b", ["b1", "b2"], hours=3),
            ]
        }
        return client, events

    client, events = build(_http_status_error(500))
    result = capture_slate_snapshots(
        client, events, tmp_path / "a", "2026-10-22", now=_DETAIL_NOW, max_workers=1
    )
    assert client.detail_calls == ["ev-a", "ev-b"]
    assert result["event_detail_events"] == 2
    assert result["event_detail_failures"] == 1
    assert result["event_detail_captured"] == 2
    assert result["failures"] == 0
    assert {"a1", "a2"} <= set(client.slate_calls)

    client, events = build(_rate_limit_error("ev-a"))
    result = capture_slate_snapshots(
        client, events, tmp_path / "b", "2026-10-22", now=_DETAIL_NOW, max_workers=1
    )
    assert client.detail_calls == ["ev-a"]
    assert result["event_detail_failures"] == 1
    assert result["event_detail_captured"] == 0
    assert sorted(client.slate_calls) == ["a1", "a2", "b1", "b2"]


def test_event_detail_quotes_every_ladder_rung_while_book_pass_keeps_the_shortlist(tmp_path) -> None:
    event = _ladder_event({"far": 0.95, "near": 0.52, "mid": 0.70, "close": 0.49})
    event["event_slug"] = "ev-ladder"
    detail = _detail(
        "ev-ladder", [_detail_market(slug, 0.45, 0.50) for slug in ("far", "near", "mid", "close")]
    )
    client = _EventDetailClient({"ev-ladder": detail})
    result = capture_slate_snapshots(
        client,
        {"NCAAF": [event]},
        tmp_path,
        "2026-10-22",
        now=_DETAIL_NOW,
        ladder_keep=1,
        max_workers=1,
    )
    # NCAAF is excluded from event-detail (no transactTime), so the kept rung is
    # priced by /book alone and the alternates are still pruned.
    assert result["event_detail_captured"] == 0
    assert client.slate_calls == ["close"]
    assert result["skipped_ladder_alternates"] == 3


def test_event_detail_covered_market_book_failure_is_not_a_capture_failure(tmp_path) -> None:
    detail = _detail("ev-1", [_detail_market("m1", 0.50, 0.54), _detail_market("m2", 0.50, None)])
    client = _EventDetailClient({"ev-1": detail})
    client.book_failures = {"m1": ValueError("book gone"), "m2": ValueError("no quote anywhere")}
    events = {"NBA": [_detail_slate_event("ev-1", ["m1", "m2"])]}
    result = capture_slate_snapshots(client, events, tmp_path, "2026-10-22", now=_DETAIL_NOW, max_workers=1)

    assert result["failures"] == 1
    assert [detail["market_slug"] for detail in result["failure_details"]] == ["m2"]
    assert result["failures_by_league"] == {"NBA": 1}
    assert result["covered_book_failures"] == 1
    assert result["captured"] == 1
    assert result["event_detail_captured"] == 1


def test_event_detail_row_loses_to_a_later_book_row_in_latest_capture_readers(tmp_path, monkeypatch) -> None:
    from model_prediction.cli.forecast_market_select import _fresh_latest_per_slug
    from model_prediction.models.college_football import _latest_capture_per_market

    _AdvancingClock(monkeypatch, _DETAIL_NOW + timedelta(seconds=60))

    class _ClockedBook(_EventDetailClient):
        def snapshot_from_slate(self, event: dict, market: dict) -> dict:
            self.slate_calls.append(market["market_slug"])
            return {
                "market_slug": market["market_slug"],
                "event_start_utc": event["event_start_utc"],
                "observed_at_utc": polymarket_us.iso_utc(polymarket_us.utc_now()),
                "reconstruction_status_source": "live_gateway_market_and_book_response",
                "market_state": "MARKET_STATE_OPEN",
                "long": {"ask": 0.57},
                "short": {"ask": 0.45},
            }

    client = _ClockedBook({"ev-1": _detail("ev-1", [_detail_market("m1", 0.50, 0.54)])})
    events = {"NBA": [_detail_slate_event("ev-1", ["m1"])]}
    capture_slate_snapshots(client, events, tmp_path, "2026-10-22", now=_DETAIL_NOW, max_workers=1)

    rows = _read_rows(tmp_path, "nba", "2026-10-22")
    assert [row["reconstruction_status_source"] for row in rows] == [
        "live_gateway_event_detail_quote",
        "live_gateway_market_and_book_response",
    ]
    for pick in (_latest_capture_per_market(rows), _fresh_latest_per_slug(rows, None)):
        assert [row["reconstruction_status_source"] for row in pick] == [
            "live_gateway_market_and_book_response"
        ]


def test_open_pick_in_switched_off_league_does_not_take_primary_budget() -> None:
    """An open pick in an out-of-scope league must not ahead-of-queue in-scope games."""
    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    future = (now + timedelta(hours=3)).isoformat()
    nba_event = {"event_start_utc": future}
    atp_event = {"event_start_utc": future}
    nba_market = {"market_slug": "nba-open-market"}
    atp_open_market = {"market_slug": "atp-open-pick"}
    entries = [
        ("ATP", "tennis", atp_event, atp_open_market),
        ("NBA", "basketball", nba_event, nba_market),
    ]
    primary, secondary = polymarket_us._capture_tiers(
        entries,
        priority_leagues=frozenset({"NBA"}),
        keep_slugs=frozenset({"atp-open-pick"}),
        now=now,
    )
    assert [entry[3]["market_slug"] for entry in primary] == ["nba-open-market"]
    assert [entry[3]["market_slug"] for entry in secondary] == ["atp-open-pick"]


def test_event_detail_pass_skips_ncaaf_because_its_quotes_carry_no_transact_time() -> None:
    """NCAAF quotes need a transactTime for the evidence check, which event-detail never carries."""
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)
    future = (now + timedelta(hours=5)).isoformat()

    class _Detail:
        event: dict = {"markets": []}  # noqa: RUF012 - fixture, never mutated
        cache_age_seconds = 0.0

    class _Client:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def event_detail(self, slug: str) -> _Detail:
            self.requested.append(slug)
            return _Detail()

    event = {"event_slug": "cfb-miss-okl", "event_start_utc": future, "event_id": "1"}
    market = {"market_slug": "tsc-cfb-miss-okl-total", "market_id": "9", "sides": [{}]}
    client = _Client()
    outcome = polymarket_us._capture_event_detail_quotes(
        client,
        [("NCAAF", "football", event, market)],
        priority_leagues=None,
        keep_slugs=frozenset(),
        now=now,
        deadline=None,
    )
    assert client.requested == []
    assert outcome.results == []
