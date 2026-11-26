"""Executor-level ops fixes (2026-11-25): IOC fills trusted only when final, the GTD fallback
read-back's eventual-consistency retry and open-orders verification, refusal classification and the
read-only balance call. Every exchange call is a FakeExchange (no network, no sleeps)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_live_order_safety import (  # the shared fake exchange and order builders
    CREDS,
    GTC,
    GTD,
    SLUG,
    FakeExchange,
    _executor,
    _pick_row,
    _snapshot,
    _start,
    _ticket,
)

from model_prediction.audit import AuditLog
from model_prediction.data_sources import polymarket_execute as pe
from model_prediction.domain import iso_utc


class SequencedExchange(FakeExchange):
    """FakeExchange whose ``GET /v1/order/{id}`` answers 404 the first ``not_found[id]`` times."""

    def __init__(self) -> None:
        super().__init__()
        self.not_found: dict[str, int] = {}
        # The (status, body) a counted not-found answers with; the default is the exchange's own 404.
        self.not_found_reply: tuple[int, str] = (404, '{"code":5, "message":"Order not found", "details":[]}')
        self.sync_ioc: dict[str, Any] | None = None

    def default_post(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if body.get("tif") != GTD and self.sync_ioc is not None:
            order = self.store(body, prefix="ioc", state=self.sync_ioc["state"], cum=self.sync_ioc["cum"])
            return 200, dict(order)
        return super().default_post(body)

    def __call__(self, method: str, url: str, headers=None, json=None, timeout=None, **kw):
        path = url[len(pe.API_HOST) :]
        if method == "GET" and path.startswith("/v1/order/"):
            order_id = path.split("/")[3]
            if self.not_found.get(order_id, 0) > 0:
                self.not_found[order_id] -= 1
                self.calls.append({"method": method, "path": path, "headers": {}, "json": None})
                status, text = self.not_found_reply
                return httpx.Response(status, text=text, request=httpx.Request(method, url))
        return super().__call__(method, url, headers=headers, json=json, timeout=timeout, **kw)


@pytest.fixture
def exchange(monkeypatch):
    fake = SequencedExchange()
    monkeypatch.setattr(pe.httpx, "request", fake)
    monkeypatch.setattr(pe.time, "sleep", lambda *_a: None)
    yield fake
    assert not [c for c in fake.calls if isinstance(c["json"], dict) and c["json"].get("tif") == GTC]


def _execute(tmp_path, exchange, *, size: float = 10.0) -> dict[str, Any]:
    start = _start()
    return _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start, size_shares=size, estimated_cost_usd=round(0.52 * size, 4), maximum_cost_usd=5.3),
        _pick_row(start),
        execute_flag=True,
        user_command=True,
    )


# ------------------------------------------------------------------ item 5: IOC fill finality


@pytest.mark.parametrize("sync_state", ["ORDER_STATE_NEW", "ORDER_STATE_PENDING_NEW", ""])
def test_ioc_zero_cum_quantity_in_a_non_terminal_state_is_polled_not_trusted(tmp_path, exchange, sync_state):
    """The synchronous answer says 0 filled while the IOC is still NEW; the poll then shows it FILLED.
    Trusting the 0 rested a full-size GTD fallback on top of the filled IOC (a doubled position)."""
    exchange.sync_ioc = {"state": sync_state, "cum": 0.0}

    def fill_on_poll(body):
        status, order = exchange.default_post(body)
        exchange.readback_overrides[order["id"]] = {"state": "ORDER_STATE_FILLED", "cumQuantity": 10.0}
        return status, order

    exchange.post_script = [fill_on_poll]
    result = _execute(tmp_path, exchange)
    assert len(exchange.order_posts()) == 1  # no fallback leg
    assert result["filled_size_shares"] == 10.0
    assert result["fallback_order_id"] is None
    assert result["order_state"] == "ORDER_STATE_FILLED"


def test_ioc_that_never_reaches_a_final_state_is_tracked_and_rests_nothing(tmp_path, exchange):
    exchange.sync_ioc = {"state": "ORDER_STATE_NEW", "cum": 0.0}
    result = _execute(tmp_path, exchange)
    assert len(exchange.order_posts()) == 1
    assert result["fill_known"] is False
    assert result["fallback_status"] == "unknown_fill"


def test_ioc_terminal_zero_fill_still_rests_the_remainder(tmp_path, exchange):
    exchange.sync_ioc = {"state": "ORDER_STATE_CANCELED", "cum": 0.0}
    result = _execute(tmp_path, exchange)
    assert len(exchange.order_posts()) == 2
    assert result["fallback_status"] == "resting"
    assert result["filled_size_shares"] == 0.0


def test_ioc_partial_with_nothing_left_working_is_final(tmp_path, exchange):
    exchange.ioc_fill = 4.0  # FakeExchange answers PARTIALLY_FILLED, leavesQuantity 0
    result = _execute(tmp_path, exchange)
    assert result["filled_size_shares"] == 4.0
    assert result["fallback_status"] == "resting"
    assert result["fallback_resting_shares"] == 6.0


# ------------------------------------------------------------------ item 8: GTD read-back


def _fallback_id(exchange: SequencedExchange) -> str:
    return f"gtd-{exchange._next_id}"


def test_readback_404_then_found_keeps_the_fallback_resting(tmp_path, exchange):
    def fallback(body):
        status, order = exchange.default_post(body)
        exchange.not_found[order["id"]] = 1
        return status, order

    exchange.post_script = [exchange.default_post, fallback]
    result = _execute(tmp_path, exchange)
    assert exchange.cancels() == []
    assert result["fallback_status"] == "resting"
    assert result["fallback_verified_via"] == "order_readback"
    events = [e for e in AuditLog(tmp_path / "audit.jsonl").events() if e["event_type"] == "order_executed"]
    assert events[-1]["payload"]["fallback_verified_via"] == "order_readback"


def test_readback_retries_a_not_found_that_carries_only_the_text_not_a_404(tmp_path, exchange):
    """The read-back retry test is the loose one (a 404 status OR the "Order not found" text): the
    strict gRPC/404 test that order_snapshots uses once replaced it by sharing its name, so a
    not-found answered with only the text stopped retrying and the live fallback was cancelled."""
    exchange.not_found_reply = (400, '{"message":"Order not found"}')

    def fallback(body):
        status, order = exchange.default_post(body)
        exchange.not_found[order["id"]] = 1
        return status, order

    exchange.post_script = [exchange.default_post, fallback]
    result = _execute(tmp_path, exchange)
    assert exchange.cancels() == []
    assert result["fallback_status"] == "resting"
    assert result["fallback_verified_via"] == "order_readback"


def test_the_two_not_found_tests_stay_distinct_and_tolerate_any_exception():
    assert pe._is_order_not_found(RuntimeError("Order not found"))  # text only, no status_code at all
    assert pe._is_order_not_found(pe.ExecutionGateError("gone", status_code=404))
    assert not pe._is_order_not_found(pe.ExecutionGateError("busy", status_code=503))
    # order_snapshots' strict test must not raise on an exception that has no status_code.
    assert not pe._is_exchange_order_missing(RuntimeError("Order not found"))
    assert pe._is_exchange_order_missing(pe.ExecutionGateError("gone", status_code=404))
    assert pe._is_exchange_order_missing(pe.ExecutionGateError('{"code":5}', status_code=400))
    assert not pe._is_exchange_order_missing(pe.ExecutionGateError('{"code":5}', status_code=429))
    assert not pe._is_exchange_order_missing(pe.ExecutionGateError("busy", status_code=503))


def test_executor_module_defines_no_top_level_name_twice():
    """ruff's F811 skips underscore-prefixed names, so a duplicate private helper (one silently
    replacing the other for the whole module) passes lint; this is the guard for it."""
    import ast
    from collections import Counter

    tree = ast.parse(Path(pe.__file__).read_text(encoding="utf-8"))
    counts = Counter(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    )
    assert [name for name, n in counts.items() if n > 1] == []


def test_readback_never_found_but_open_orders_show_it_gtd_keeps_it_resting(tmp_path, exchange):
    def fallback(body):
        status, order = exchange.default_post(body)
        exchange.not_found[order["id"]] = 99
        exchange.open_orders = [dict(order)]
        return status, order

    exchange.post_script = [exchange.default_post, fallback]
    result = _execute(tmp_path, exchange)
    assert exchange.cancels() == []
    assert result["fallback_status"] == "resting"
    assert result["fallback_verified_via"] == "open_orders"
    # Bounded: the first read plus three retries, then the open-orders read.
    reads = [c["path"] for c in exchange.calls if c["method"] == "GET"]
    assert sum(p.startswith("/v1/order/gtd") for p in reads) == 4
    assert any(p.startswith(f"/v1/orders/open?slugs={SLUG}") for p in reads)


@pytest.mark.parametrize(
    "shown",
    [
        pytest.param({"tif": GTC}, id="open-orders-gtc"),
        pytest.param({"goodTillTime": None}, id="open-orders-no-expiry"),
        pytest.param(None, id="absent-from-open-orders"),
    ],
)
def test_readback_never_found_and_open_orders_do_not_confirm_gtd_cancels(tmp_path, exchange, shown):
    def fallback(body):
        status, order = exchange.default_post(body)
        exchange.not_found[order["id"]] = 99
        exchange.open_orders = [] if shown is None else [{**order, **shown}]
        return status, order

    exchange.post_script = [exchange.default_post, fallback]
    result = _execute(tmp_path, exchange)
    assert exchange.cancels() == [result["fallback_order_id"]]
    assert result["fallback_status"] == "cancelled_unverified_gtd"
    assert result["fallback_verified_via"] == "none"


def test_short_tif_spelling_and_epoch_expiry_are_read_as_gtd(tmp_path, exchange):
    def fallback(body):
        status, order = exchange.default_post(body)
        epoch_ms = int(pe.parse_utc(body["goodTillTime"]).timestamp() * 1000)
        exchange.readback_overrides[order["id"]] = {"tif": "GOOD_TILL_DATE", "goodTillTime": epoch_ms}
        return status, order

    exchange.post_script = [exchange.default_post, fallback]
    result = _execute(tmp_path, exchange)
    assert exchange.cancels() == []
    assert result["fallback_status"] == "resting"


# ------------------------------------------------------------------ 2026-11-26 audit B1: epoch units, overflow, slugs

_ECHO_INSTANT = datetime(2026, 11, 27, 0, 30, tzinfo=UTC)
_ECHO_SECONDS = int(_ECHO_INSTANT.timestamp())


@pytest.mark.parametrize("scale", [1, 10**3, 10**6, 10**9], ids=["seconds", "millis", "micros", "nanos"])
def test_exchange_time_reads_an_epoch_in_every_unit_as_the_same_instant(scale):
    """A nanosecond echo used to be divided by 1000 only, then fromtimestamp raised OSError on Windows."""
    for value in (_ECHO_SECONDS * scale, float(_ECHO_SECONDS * scale), str(_ECHO_SECONDS * scale)):
        assert pe._exchange_time(value) == _ECHO_INSTANT


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        -1.5e18,
        1e30,
        float("nan"),
        float("inf"),
        "nan",
        "garbage",
        "",
        None,
        "0001-01-01T00:00:00+05:00",
        pytest.param(10**400, id="int-too-large-for-float"),
    ],
)
def test_exchange_time_is_none_for_anything_unreadable(value):
    assert pe._exchange_time(value) is None


def test_exchange_time_still_reads_rfc3339_text():
    assert pe._exchange_time("2026-11-27T00:30:00Z") == _ECHO_INSTANT


def test_a_nanosecond_goodtilltime_echo_verifies_the_fallback_as_resting(tmp_path, exchange):
    def fallback(body):
        status, order = exchange.default_post(body)
        seconds = int(pe.parse_utc(body["goodTillTime"]).timestamp())
        exchange.readback_overrides[order["id"]] = {"tif": GTD, "goodTillTime": seconds * 10**9}
        return status, order

    exchange.post_script = [exchange.default_post, fallback]
    result = _execute(tmp_path, exchange)
    assert exchange.cancels() == []
    assert result["fallback_status"] == "resting"
    assert result["fallback_verified_via"] == "order_readback"


def test_an_out_of_range_goodtilltime_echo_cancels_the_fallback_and_still_audits_the_buy(tmp_path, exchange):
    """OSError out of fromtimestamp escaped _submit after both POSTs succeeded: no order_executed audit."""

    def fallback(body):
        status, order = exchange.default_post(body)
        exchange.readback_overrides[order["id"]] = {"tif": GTD, "goodTillTime": 1e30}
        return status, order

    exchange.post_script = [exchange.default_post, fallback]
    result = _execute(tmp_path, exchange)
    assert exchange.cancels() == [result["fallback_order_id"]]
    assert result["fallback_status"] == "cancelled_unverified_gtd"
    events = [e for e in AuditLog(tmp_path / "audit.jsonl").events() if e["event_type"] == "order_executed"]
    assert len(events) == 1


_OVERFLOWING_START = "0001-01-01T00:00:00+05:00"


def test_exchange_event_start_refuses_a_start_that_overflows_utc():
    with pytest.raises(pe.ExecutionGateError, match="unreadable event_start_utc"):
        pe._exchange_event_start({"event_start_utc": _OVERFLOWING_START})


def test_earliest_event_start_skips_a_start_that_overflows_utc():
    good = _start(minutes=10)
    row = {"event_start_utc": _OVERFLOWING_START}
    assert pe._earliest_event_start(row, {"event_start_utc": iso_utc(good)}) == pe.parse_utc(iso_utc(good))
    assert pe._earliest_event_start(row, None) is None


def test_fallback_expiry_survives_an_overflowing_ticket_or_exchange_start(tmp_path):
    start = _start()
    client = _executor(tmp_path, _snapshot(start))
    assert (
        client._fallback_expiry(_ticket(start, resting_expires_at_utc=_OVERFLOWING_START), _snapshot(start))
        is None
    )
    ticket = _ticket(start)
    snap = _snapshot(start, event_start_utc=_OVERFLOWING_START)
    assert client._fallback_expiry(ticket, snap) == pe.parse_utc(ticket.resting_expires_at_utc)


# _extract_market_slug: the explicit token, the end-anchored legacy form, and the NFL shadow inline form.

_INLINE_SLUG = "aec-nfl-kc-buf-2026-11-30"


def test_extract_market_slug_reads_the_nfl_shadow_inline_form():
    text = f"Learned LR call at threshold 0.6; executable ask 0.5100 ({_INLINE_SLUG}); model edge vs ask +0.0300."
    assert pe._extract_market_slug(text) == _INLINE_SLUG


@pytest.mark.parametrize(
    "text",
    [
        "challenger (home) call; model edge vs ask +0.0300.",
        "challenger (anchor-blended): fair margin +3.1; model edge vs ask +0.0300.",
        "edge (v5) note",
        "plain text with no slug",
    ],
)
def test_extract_market_slug_ignores_parenthesised_words_that_are_not_contracts(text):
    assert pe._extract_market_slug(text) is None


def test_extract_market_slug_prefers_the_explicit_token_then_the_end_anchored_form():
    both = f"auto_buy market_slug=asc-cbb-a-b-2026-11-25 ({_INLINE_SLUG}); edge."
    assert pe._extract_market_slug(both) == "asc-cbb-a-b-2026-11-25"
    anchored = f"edge ({_INLINE_SLUG}); more text (tsc-nfl-kc-buf-2026-11-30-44pt5)."
    assert pe._extract_market_slug(anchored) == "tsc-nfl-kc-buf-2026-11-30-44pt5"


# ------------------------------------------------------------------ item 2: refusals and balance


def test_http_refusals_carry_their_status_and_classify(tmp_path, exchange, monkeypatch):
    client = _executor(tmp_path, _snapshot(_start()))

    def answer(status: int, text: str):
        def fake(method, url, headers=None, json=None, timeout=None, **_kw):
            return httpx.Response(status, text=text, request=httpx.Request(method, url))

        return fake

    monkeypatch.setattr(pe.httpx, "request", answer(401, '{"code":16,"message":"unauthenticated"}'))
    with pytest.raises(pe.ExecutionGateError) as auth:
        client._request("GET", "/v1/account/balances")
    assert auth.value.status_code == 401
    assert pe.classify_gate_refusal(auth.value) == "auth"

    monkeypatch.setattr(pe.httpx, "request", answer(400, '{"code":9,"message":"insufficient buying power"}'))
    with pytest.raises(pe.ExecutionGateError) as balance:
        client._request("GET", "/v1/account/balances")
    assert pe.classify_gate_refusal(balance.value) == "balance"

    assert pe.classify_gate_refusal(pe.ExecutionGateError("REFUSED: POLYMARKET_KEY_ID is not set.")) == "auth"
    assert (
        pe.classify_gate_refusal(
            pe.ExecutionGateError("REFUSED by Polymarket US (503): busy", status_code=503)
        )
        is None
    )
    assert pe.classify_gate_refusal(pe.ExecutionGateError("REFUSED: the live ask moved")) is None


@pytest.mark.parametrize(
    ("balances", "expected"),
    [
        ([{"currency": "USD", "buyingPower": {"value": "206.08", "currency": "USD"}}], 206.08),
        ([{"currency": "USD", "buyingPower": 20}], 20.0),
        ([{"currency": "USD", "currentBalance": 235.8}], None),
        ([], None),
    ],
)
def test_account_balance_reads_buying_power_or_reports_unknown(tmp_path, monkeypatch, balances, expected):
    client = pe.PolymarketExecutor(
        AuditLog(tmp_path / "audit.jsonl"), confirm=lambda _p: "N", environ=dict(CREDS)
    )
    seen: list[tuple[str, str]] = []

    def fake_request(method, path, payload=None):
        seen.append((method, path))
        return {"balances": balances}

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.account_balance()["available_cash_usd"] == expected
    assert seen == [("GET", "/v1/account/balances")]


def test_account_balance_without_credentials_refuses_as_auth(tmp_path):
    client = pe.PolymarketExecutor(AuditLog(tmp_path / "audit.jsonl"), confirm=lambda _p: "N", environ={})
    with pytest.raises(pe.ExecutionGateError) as refused:
        client.account_balance()
    assert pe.classify_gate_refusal(refused.value) == "auth"


def test_ioc_still_partially_filled_after_every_poll_takes_the_polled_quantity(tmp_path, exchange):
    """Seconds after the POST an IOC is no longer working, so a polled PARTIALLY_FILLED without
    leavesQuantity is final; refusing it left the row unknown and never settled."""
    exchange.sync_ioc = {"state": "ORDER_STATE_NEW", "cum": 0.0}

    def partial_on_poll(body):
        status, order = exchange.default_post(body)
        exchange.readback_overrides[order["id"]] = {
            "state": "ORDER_STATE_PARTIALLY_FILLED",
            "cumQuantity": 4.0,
        }
        return status, order

    exchange.post_script = [partial_on_poll]
    result = _execute(tmp_path, exchange)
    assert result["fill_known"] is True
    assert result["filled_size_shares"] == 4.0
    assert result["fallback_status"] == "resting"
    assert result["fallback_resting_shares"] == 6.0


def _polled_partial(exchange, **extra):
    """A NEW synchronous IOC whose polled order is PARTIALLY_FILLED with cum 4 plus ``extra`` fields."""
    exchange.sync_ioc = {"state": "ORDER_STATE_NEW", "cum": 0.0}

    def partial_on_poll(body):
        status, order = exchange.default_post(body)
        exchange.readback_overrides[order["id"]] = {
            "state": "ORDER_STATE_PARTIALLY_FILLED",
            "cumQuantity": 4.0,
            **extra,
        }
        return status, order

    exchange.post_script = [partial_on_poll]


@pytest.mark.parametrize("leaves", [0, 0.0, "0", "", None, -1.0])
def test_polled_partial_reporting_no_leaves_quantity_is_final(tmp_path, exchange, leaves):
    _polled_partial(exchange, leavesQuantity=leaves)
    result = _execute(tmp_path, exchange)
    assert result["fill_known"] is True
    assert result["filled_size_shares"] == 4.0
    assert result["fallback_status"] == "resting"
    assert result["fallback_resting_shares"] == 6.0


@pytest.mark.parametrize("leaves", [6, 6.0, "6", 0.5, "n/a"])
def test_polled_partial_still_working_is_not_final_and_rests_nothing(tmp_path, exchange, leaves):
    """An order that says leavesQuantity > 0 may still fill: a fallback for the remainder on top of
    it could double the position. The row stays an unknown-fill primary for reconcile instead."""
    _polled_partial(exchange, leavesQuantity=leaves)
    result = _execute(tmp_path, exchange)
    assert len(exchange.order_posts()) == 1  # no fallback leg
    assert result["fill_known"] is False
    assert result["fallback_status"] == "unknown_fill"
    assert result["fallback_order_id"] == result["order_id"]
    assert result["fallback_resting_shares"] == 10.0


def test_ioc_partial_from_the_synchronous_answer_alone_is_not_trusted(tmp_path, exchange):
    """Every poll fails: the synchronous PARTIALLY_FILLED count may predate later fills."""
    exchange.sync_ioc = {"state": "ORDER_STATE_PARTIALLY_FILLED", "cum": 4.0}

    def unreadable(body):
        status, order = exchange.default_post(body)
        exchange.not_found[order["id"]] = 99
        return status, order

    exchange.post_script = [unreadable]
    result = _execute(tmp_path, exchange)
    assert len(exchange.order_posts()) == 1
    assert result["fill_known"] is False
    assert result["fallback_status"] == "unknown_fill"


# ------------------------------------------------------------------ cancel: slug lookup vs deadline


def _cancel_calls(tmp_path, monkeypatch, *, slug, stop_before):
    client = _executor(tmp_path, _snapshot(_start()))
    calls: list[tuple[str, str, Any]] = []

    def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        return {"order": {"id": "o-1", "marketSlug": "looked-up"}} if method == "GET" else {}

    monkeypatch.setattr(client, "_request", fake_request)
    try:
        client.cancel("o-1", user_command=True, market_slug=slug, stop_before=stop_before)
    except pe.CancelDeferredError:
        return calls, True
    return calls, False


def test_cancel_without_a_slug_rechecks_the_deadline_between_the_lookup_and_the_post(tmp_path, monkeypatch):
    calls, deferred = _cancel_calls(tmp_path, monkeypatch, slug=None, stop_before=lambda: True)
    assert deferred and [c[0] for c in calls] == ["GET"]  # the POST was never sent


def test_cancel_without_a_slug_still_posts_while_the_deadline_has_not_passed(tmp_path, monkeypatch):
    calls, deferred = _cancel_calls(tmp_path, monkeypatch, slug=None, stop_before=lambda: False)
    assert not deferred and [c[0] for c in calls] == ["GET", "POST"]
    assert calls[-1][2] == {"marketSlug": "looked-up"}


def test_cancel_with_a_slug_makes_one_call_and_never_consults_the_deadline(tmp_path, monkeypatch):
    def boom() -> bool:
        raise AssertionError("no lookup ran, so there is nothing to recheck")

    calls, deferred = _cancel_calls(tmp_path, monkeypatch, slug="given", stop_before=boom)
    assert not deferred and [c[0] for c in calls] == ["POST"]
