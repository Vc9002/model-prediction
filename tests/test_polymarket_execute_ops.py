"""Executor-level ops fixes (2026-11-25): IOC fills trusted only when final, the GTD fallback
read-back's eventual-consistency retry and open-orders verification, refusal classification and the
read-only balance call. Every exchange call is a FakeExchange (no network, no sleeps)."""

from __future__ import annotations

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


class SequencedExchange(FakeExchange):
    """FakeExchange whose ``GET /v1/order/{id}`` answers 404 the first ``not_found[id]`` times."""

    def __init__(self) -> None:
        super().__init__()
        self.not_found: dict[str, int] = {}
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
                return httpx.Response(
                    404,
                    text='{"code":5, "message":"Order not found", "details":[]}',
                    request=httpx.Request(method, url),
                )
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
