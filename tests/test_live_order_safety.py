"""Live auto-buyer order safety (quick-261125-hih, Task 1).

One live cohort order, end to end, over a faked exchange: every ``POST /v1/orders`` carries an
``Idempotency-Key`` (one per leg), an ambiguous outcome is never retried by default and becomes an
``ORDER_STATE_SUBMIT_UNKNOWN`` ledger row plus an alarm, the intent is audited before the POST, the
IOC remainder rests only as a GOOD_TILL_DATE order expiring 90 s before the start and is read back
by id, the live ask is re-checked against the ticket price, and every placed order is recorded or
alarmed (never "refused").

No test here can reach the exchange: ``httpx.request`` inside ``polymarket_execute`` is a
``FakeExchange``, ``PolymarketUSClient`` raises, and ``time.sleep`` is a no-op. New symbols are
reached through their modules so a missing one fails its own test rather than the whole file.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from model_prediction.audit import AuditLockTimeout, AuditLog
from model_prediction.calibration_pipeline import ServingCalibration
from model_prediction.data_sources import polymarket_execute as pe
from model_prediction.domain import iso_utc, parse_utc, utc_now
from model_prediction.portfolio import auto_buyer_ledger, auto_executor

CREDS = {
    "POLYMARKET_KEY_ID": "test-key-id",
    "POLYMARKET_SECRET_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
}
SLUG = "asc-cbb-hom-awy-2026-11-25-neg-3pt5"
IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
GTD = "TIME_IN_FORCE_GOOD_TILL_DATE"
GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
SPREAD_V7 = "ncaab-spread-v7"


# ------------------------------------------------------------------ fake exchange


class FakeExchange:
    """Stands in for api.polymarket.us at the ``httpx.request`` boundary.

    ``post_script`` holds one step per order POST (a ``(status, body)`` tuple, an exception to
    raise, or a callable taking the JSON body); an empty script uses ``default_post``: an IOC fills
    ``ioc_fill`` shares and a GTD order rests. ``GET /v1/order/{id}`` echoes the stored order, with
    ``readback_overrides`` applied; ids in ``get_failures`` answer 404.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.post_script: list[Any] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.open_orders: list[dict[str, Any]] = []
        self.get_failures: set[str] = set()
        self.readback_overrides: dict[str, dict[str, Any]] = {}
        self.ioc_fill = 4.0
        self.preview_script: list[Any] = []
        self._next_id = 0

    def order_posts(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == "POST" and c["path"] == "/v1/orders"]

    def cancels(self) -> list[str]:
        return [c["path"].split("/")[3] for c in self.calls if c["path"].endswith("/cancel")]

    def store(self, body: dict[str, Any], *, prefix: str, state: str, cum: float) -> dict[str, Any]:
        self._next_id += 1
        order_id = f"{prefix}-{self._next_id}"
        order = {
            "id": order_id,
            "state": state,
            "cumQuantity": cum,
            "tif": body.get("tif"),
            "goodTillTime": body.get("goodTillTime"),
            "marketSlug": body.get("marketSlug"),
            "intent": body.get("intent"),
            "price": body.get("price"),
            "quantity": body.get("quantity"),
        }
        self.orders[order_id] = order
        return order

    def default_post(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if body.get("tif") == IOC:
            filled = min(self.ioc_fill, float(body["quantity"]))
            state = (
                "ORDER_STATE_FILLED" if filled >= float(body["quantity"]) else "ORDER_STATE_PARTIALLY_FILLED"
            )
            return 200, dict(self.store(body, prefix="ioc", state=state, cum=filled))
        return 200, dict(self.store(body, prefix="gtd", state="ORDER_STATE_NEW", cum=0.0))

    def __call__(self, method: str, url: str, headers=None, json=None, timeout=None, **_kw):
        assert url.startswith(pe.API_HOST)
        path = url[len(pe.API_HOST) :]
        self.calls.append({"method": method, "path": path, "headers": dict(headers or {}), "json": json})
        request = httpx.Request(method, url)
        if method == "POST" and path == "/v1/orders":
            step = self.post_script.pop(0) if self.post_script else self.default_post
            outcome = step(json) if callable(step) else step
            if isinstance(outcome, BaseException):
                raise outcome
            status, body = outcome
            if isinstance(body, dict | list):
                return httpx.Response(status, json=body, request=request)
            return httpx.Response(status, text=str(body), request=request)
        if method == "POST" and path == "/v1/order/preview":
            step = self.preview_script.pop(0) if self.preview_script else (200, {"preview": "ok"})
            if isinstance(step, BaseException):
                raise step
            return httpx.Response(step[0], json=step[1], request=request)
        if method == "GET" and path.startswith("/v1/orders/open"):
            return httpx.Response(200, json={"orders": self.open_orders}, request=request)
        if method == "GET" and path.startswith("/v1/order/"):
            order_id = path.split("/")[3]
            if order_id in self.get_failures:
                return httpx.Response(404, json={"error": "not found"}, request=request)
            order = {
                **self.orders.get(order_id, {"id": order_id}),
                **self.readback_overrides.get(order_id, {}),
            }
            return httpx.Response(200, json={"order": order}, request=request)
        if method == "POST" and path.endswith("/cancel"):
            order_id = path.split("/")[3]
            self.orders.setdefault(order_id, {"id": order_id})["state"] = "ORDER_STATE_CANCELED"
            return httpx.Response(200, json={}, request=request)
        raise AssertionError(f"unexpected exchange call {method} {path}")


@pytest.fixture
def exchange(monkeypatch):
    fake = FakeExchange()
    monkeypatch.setattr(pe.httpx, "request", fake)
    monkeypatch.setattr(pe.time, "sleep", lambda *_a: None)
    yield fake
    # D-01: no POST anywhere in this file may ask the exchange for an un-expiring rest.
    assert not [c for c in fake.calls if isinstance(c["json"], dict) and c["json"].get("tif") == GTC]


def _same_key_posts(fake: FakeExchange) -> set[str]:
    return {c["headers"].get("Idempotency-Key", "") for c in fake.order_posts()}


# ------------------------------------------------------------------ executor-level fixtures


def _start(minutes: float = 30.0):
    return utc_now() + timedelta(minutes=minutes)


def _good_till(start) -> str:
    return (start - timedelta(seconds=90)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick_row(start, **overrides: Any) -> dict[str, Any]:
    row = {
        "pick_id": "pick-1",
        "status": "open",
        "rationale": f"auto_buy market_slug:{SLUG} (market_slug={SLUG})",
        "market_type": "spread",
        "home_team": "Home U",
        "away_team": "Away U",
        "selection": "HOME",
        "line": "-3.5",
        "event_id": "ev-1",
        "event_start_utc": iso_utc(start),
        "created_at_utc": iso_utc(utc_now() - timedelta(minutes=5)),
    }
    row.update(overrides)
    return row


def _snapshot(start, ask: Any = 0.52, **overrides: Any) -> dict[str, Any]:
    snap = {
        "market_slug": SLUG,
        "observed_at_utc": iso_utc(utc_now()),
        "market_state": "MARKET_STATE_OPEN",
        "team": "Home U",
        "line": -3.5,
        "event_start_utc": iso_utc(start),
        "long": {"description": "Home U -3.5", "ask": ask},
        "short": {"description": "Away U +3.5", "ask": 0.50},
    }
    snap.update(overrides)
    return snap


def _ticket(start, **overrides: Any):
    fields: dict[str, Any] = {
        "market_slug": SLUG,
        "token_side": "long",
        "action": "buy",
        "order_type": "limit_ioc",
        "price": 0.52,
        "size_shares": 10.0,
        "pick_id": "pick-1",
        "estimated_cost_usd": 5.2,
        "maximum_cost_usd": 5.3,
        "authorization_type": "auto_whitelisted_model",
        "ioc_fallback_resting": True,
        "resting_expires_at_utc": iso_utc(start - timedelta(seconds=90)),
        "max_live_price_deviation": 0.02,
    }
    fields.update(overrides)
    return pe.OrderTicket(**fields)


def _executor(tmp_path: Path, snapshot: dict[str, Any]) -> pe.PolymarketExecutor:
    return pe.PolymarketExecutor(
        AuditLog(tmp_path / "audit.jsonl"),
        confirm=lambda _prompt: "Y",
        environ=dict(CREDS),
        live_quote=lambda _slug: snapshot,
    )


def _audit_types(tmp_path: Path) -> list[str]:
    return [e["event_type"] for e in AuditLog(tmp_path / "audit.jsonl").events()]


def _audit_events(tmp_path: Path, event_type: str) -> list[dict[str, Any]]:
    return [e for e in AuditLog(tmp_path / "audit.jsonl").events() if e["event_type"] == event_type]


# ------------------------------------------------------------------ B1: keys and retries


def test_request_refuses_an_order_post_without_an_idempotency_key(tmp_path, exchange):
    start = _start()
    client = _executor(tmp_path, _snapshot(start))
    with pytest.raises(pe.ExecutionGateError, match="Idempotency-Key"):
        client._request("POST", "/v1/orders", {"marketSlug": SLUG})
    assert exchange.calls == []


def test_connect_error_then_success_retries_once_with_the_same_key(tmp_path, exchange):
    start = _start()
    exchange.ioc_fill = 10.0
    exchange.post_script = [httpx.ConnectError("connection refused")]
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    posts = exchange.order_posts()
    assert len(posts) == 2
    assert len(_same_key_posts(exchange)) == 1
    assert posts[0]["headers"]["Idempotency-Key"].endswith("-p")
    assert result["status"] == "submitted"
    assert result["filled_size_shares"] == 10.0


def test_429_backs_off_and_reuses_the_same_key(tmp_path, exchange):
    start = _start()
    exchange.ioc_fill = 10.0
    exchange.post_script = [(429, {"error": "rate limited"})]
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    assert len(exchange.order_posts()) == 2
    assert len(_same_key_posts(exchange)) == 1
    assert result["status"] == "submitted"


def test_connect_failure_on_every_attempt_is_refused_never_unknown(tmp_path, exchange):
    start = _start()
    exchange.post_script = [
        httpx.ConnectError("down"),
        httpx.ConnectTimeout("down"),
        httpx.ConnectError("down"),
    ]
    with pytest.raises(pe.ExecutionGateError) as caught:
        _executor(tmp_path, _snapshot(start)).execute(
            _ticket(start), _pick_row(start), execute_flag=True, user_command=True
        )
    assert not isinstance(caught.value, pe.OrderSubmitUnknownError)
    assert len(exchange.order_posts()) == 3
    assert len(_same_key_posts(exchange)) == 1
    types = _audit_types(tmp_path)
    assert "order_rejected" in types
    assert "order_submit_unknown" not in types


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(httpx.ReadTimeout("read timed out"), id="read-timeout"),
        pytest.param(httpx.RemoteProtocolError("peer closed"), id="remote-protocol"),
        pytest.param((502, {"error": "bad gateway"}), id="http-502"),
        pytest.param((200, "<html>not json</html>"), id="2xx-not-json"),
        pytest.param((200, {"state": "ORDER_STATE_NEW"}), id="2xx-without-id"),
    ],
)
def test_ambiguous_outcome_is_unknown_with_exactly_one_post(tmp_path, exchange, step):
    assert pe.ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY is False
    start = _start()
    exchange.post_script = [step]
    with pytest.raises(pe.OrderSubmitUnknownError) as caught:
        _executor(tmp_path, _snapshot(start)).execute(
            _ticket(start), _pick_row(start), execute_flag=True, user_command=True
        )
    assert not isinstance(caught.value, pe.ExecutionGateError)
    posts = exchange.order_posts()
    assert len(posts) == 1, "an ambiguous order POST must never be resent"
    assert caught.value.idempotency_key == posts[0]["headers"]["Idempotency-Key"]
    assert caught.value.market_slug == SLUG
    assert caught.value.leg == "primary"
    unknown = _audit_events(tmp_path, "order_submit_unknown")
    assert len(unknown) == 1
    assert unknown[0]["payload"]["idempotency_key"] == caught.value.idempotency_key


def test_same_key_retry_switch_on_reuses_the_key_and_records_one_order(tmp_path, exchange, monkeypatch):
    monkeypatch.setattr(pe, "ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY", True)
    start = _start()
    exchange.ioc_fill = 10.0
    retained: dict[str, Any] = {}

    def accepted_then_lost(body):
        retained.update(exchange.default_post(body)[1])
        return httpx.ReadTimeout("response lost")

    exchange.post_script = [accepted_then_lost, lambda _body: (200, dict(retained))]
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    assert len(exchange.order_posts()) == 2
    assert len(_same_key_posts(exchange)) == 1
    assert len(exchange.orders) == 1
    assert result["order_id"] == retained["id"]


def test_same_key_retry_switch_on_waits_out_aborted(tmp_path, exchange, monkeypatch):
    monkeypatch.setattr(pe, "ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY", True)
    start = _start()
    exchange.ioc_fill = 10.0
    exchange.post_script = [(409, {"code": "ABORTED", "message": "concurrent request"})]
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    assert len(exchange.order_posts()) == 2
    assert len(_same_key_posts(exchange)) == 1
    assert result["status"] == "submitted"


def test_definite_400_is_refused_and_does_not_block_a_later_buy(tmp_path, exchange):
    start = _start()
    exchange.ioc_fill = 10.0
    exchange.post_script = [(400, {"error": "invalid price"})]
    client = _executor(tmp_path, _snapshot(start))
    with pytest.raises(pe.ExecutionGateError, match="400"):
        client.execute(_ticket(start), _pick_row(start), execute_flag=True, user_command=True)
    assert "order_rejected" in _audit_types(tmp_path)
    result = client.execute(_ticket(start), _pick_row(start), execute_flag=True, user_command=True)
    assert result["status"] == "submitted"
    assert len(_same_key_posts(exchange)) == 2, "a new placement intent gets a new key"


def test_single_order_policy_blocks_after_an_unknown_or_dangling_intent(tmp_path, exchange):
    start = _start()
    exchange.post_script = [httpx.ReadTimeout("lost")]
    client = _executor(tmp_path, _snapshot(start))
    with pytest.raises(pe.OrderSubmitUnknownError):
        client.execute(_ticket(start), _pick_row(start), execute_flag=True, user_command=True)
    with pytest.raises(pe.ExecutionGateError, match="single_order"):
        client.execute(_ticket(start), _pick_row(start), execute_flag=True, user_command=True)
    assert len(exchange.order_posts()) == 1
    # An intent with no recorded outcome (a crash mid-POST) blocks the pick too.
    other = tmp_path / "other"
    other.mkdir()
    AuditLog(other / "audit.jsonl").append("order_intent", "pick-1", {"intent_id": "abc", "action": "buy"})
    with pytest.raises(pe.ExecutionGateError, match="single_order"):
        _executor(other, _snapshot(start)).execute(
            _ticket(start), _pick_row(start), execute_flag=True, user_command=True
        )
    assert len(exchange.order_posts()) == 1


# ------------------------------------------------------------------ B2: intent and audit


def _failing_append(event_type_to_fail: str):
    original = AuditLog.append

    def append(self, event_type, subject_id, payload, *args, **kwargs):
        if event_type == event_type_to_fail:
            raise AuditLockTimeout(f"lock busy for {event_type}")
        return original(self, event_type, subject_id, payload, *args, **kwargs)

    return append


def test_intent_audit_failure_refuses_before_any_post(tmp_path, exchange, monkeypatch):
    monkeypatch.setattr(AuditLog, "append", _failing_append("order_intent"))
    start = _start()
    with pytest.raises(pe.ExecutionGateError, match="intent"):
        _executor(tmp_path, _snapshot(start)).execute(
            _ticket(start), _pick_row(start), execute_flag=True, user_command=True
        )
    assert exchange.order_posts() == []


def test_intent_is_audited_before_the_post_with_event_fields(tmp_path, exchange):
    start = _start()
    exchange.ioc_fill = 10.0
    seen_at_post: list[list[str]] = []
    exchange.post_script = [
        lambda body: seen_at_post.append(_audit_types(tmp_path)) or exchange.default_post(body)
    ]
    _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    assert seen_at_post == [["order_intent"]]
    intent = _audit_events(tmp_path, "order_intent")[0]["payload"]
    assert intent["event_id"] == "ev-1"
    assert intent["selection"] == "home"
    assert intent["market_type"] == "spread"
    assert intent["idempotency_key"] == exchange.order_posts()[0]["headers"]["Idempotency-Key"]
    assert intent["fallback_idempotency_key"].endswith("-f")


def test_executed_audit_failure_after_a_placed_order_returns_audit_error(tmp_path, exchange, monkeypatch):
    monkeypatch.setattr(AuditLog, "append", _failing_append("order_executed"))
    start = _start()
    exchange.ioc_fill = 10.0
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    assert result["status"] == "submitted"
    assert "lock busy" in result["audit_error"]
    assert result["order_id"]


def test_event_start_during_the_intent_append_sends_nothing(tmp_path, exchange, monkeypatch):
    start = _start()
    original = AuditLog.append

    def slow_append(self, event_type, subject_id, payload, *args, **kwargs):
        written = original(self, event_type, subject_id, payload, *args, **kwargs)
        if event_type == "order_intent":
            # The audit lock wait outlasted the pregame window.
            monkeypatch.setattr(pe, "utc_now", lambda: start + timedelta(seconds=1))
        return written

    monkeypatch.setattr(AuditLog, "append", slow_append)
    with pytest.raises(pe.ExecutionGateError, match="event started"):
        _executor(tmp_path, _snapshot(start)).execute(
            _ticket(start), _pick_row(start), execute_flag=True, user_command=True
        )
    assert exchange.order_posts() == []
    assert _audit_types(tmp_path) == ["order_intent", "order_rejected"]


def test_cancel_audit_failure_still_reports_cancelled(tmp_path, exchange, monkeypatch):
    monkeypatch.setattr(AuditLog, "append", _failing_append("order_cancelled"))
    result = _executor(tmp_path, _snapshot(_start())).cancel("ord-9", user_command=True, market_slug=SLUG)
    assert result["status"] == "cancelled"
    assert result["order_id"] == "ord-9"
    assert "lock busy" in result["audit_error"]


# ------------------------------------------------------------------ D-01: GTD fallback


def test_fallback_400_keeps_the_ioc_fill_and_reports_rejected(tmp_path, exchange):
    start = _start()
    exchange.post_script = [exchange.default_post, (400, {"error": "goodTillTime invalid"})]
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    assert result["status"] == "submitted"
    assert result["filled_size_shares"] == 4.0
    assert result["fallback_status"] == "rejected"
    assert "goodTillTime invalid" in result["fallback_error"]
    assert result["fallback_order_id"] is None
    assert len(exchange.order_posts()) == 2


def _open_order(body_from: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    order = {
        "id": "adopt-1",
        "intent": body_from["intent"],
        "price": body_from["price"],
        "quantity": body_from["quantity"],
        "tif": GTD,
        "goodTillTime": body_from["goodTillTime"],
        "marketSlug": SLUG,
        "state": "ORDER_STATE_NEW",
        "createTime": iso_utc(utc_now()),
    }
    order.update(overrides)
    return order


@pytest.mark.parametrize("matches", [1, 0, 2])
def test_ambiguous_fallback_is_adopted_only_on_one_exact_open_match(tmp_path, exchange, matches):
    start = _start()

    def lost_fallback(body):
        if matches:
            exchange.open_orders = [_open_order(body, id=f"adopt-{i}") for i in range(matches)]
            for order in exchange.open_orders:
                exchange.orders[order["id"]] = order
        return httpx.ReadTimeout("fallback response lost")

    exchange.post_script = [exchange.default_post, lost_fallback]
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    assert len(exchange.order_posts()) == 2
    assert any(c["path"].startswith("/v1/orders/open?slugs=") for c in exchange.calls)
    if matches == 1:
        assert result["fallback_status"] == "resting"
        assert result["fallback_order_id"] == "adopt-0"
    else:
        assert result["fallback_status"] == "submit_unknown"
        assert result["fallback_order_id"] is None
    assert result["filled_size_shares"] == 4.0


@pytest.mark.parametrize(
    ("override", "fail_get"),
    [
        pytest.param({"tif": GTC}, False, id="tif-gtc"),
        pytest.param({"goodTillTime": "SHIFT"}, False, id="expiry-off-by-5s"),
        pytest.param({}, True, id="readback-get-fails"),
    ],
)
def test_readback_mismatch_cancels_the_fallback_by_id(tmp_path, exchange, override, fail_get):
    start = _start()

    def fallback(body):
        status, order = exchange.default_post(body)
        if override.get("goodTillTime") == "SHIFT":
            shifted = parse_utc(body["goodTillTime"]) + timedelta(seconds=5)
            exchange.readback_overrides[order["id"]] = {"goodTillTime": iso_utc(shifted)}
        elif override:
            exchange.readback_overrides[order["id"]] = dict(override)
        if fail_get:
            exchange.get_failures.add(order["id"])
        return status, order

    exchange.post_script = [exchange.default_post, fallback]
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    fallback_id = result["fallback_order_id"]
    assert fallback_id and fallback_id.startswith("gtd-")
    assert exchange.cancels() == [fallback_id]
    assert result["fallback_status"] == "cancelled_unverified_gtd"
    assert result["filled_size_shares"] == 4.0


def test_fallback_too_close_to_start_is_not_placed(tmp_path, exchange):
    start = _start(minutes=2.4)  # start - 90 s is ~54 s away: inside the 60 s floor
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    assert len(exchange.order_posts()) == 1
    assert result["fallback_status"] == "not_placed_near_start"


def test_snapshot_start_earlier_than_ticket_clamps_the_expiry(tmp_path, exchange):
    start = _start(minutes=30)
    exchange_start = _start(minutes=20)
    _executor(tmp_path, _snapshot(start, event_start_utc=iso_utc(exchange_start))).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    posts = exchange.order_posts()
    assert posts[1]["json"]["goodTillTime"] == _good_till(exchange_start)


def test_ticket_without_expiry_places_no_fallback(tmp_path, exchange):
    start = _start()
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start, resting_expires_at_utc=None), _pick_row(start), execute_flag=True, user_command=True
    )
    assert len(exchange.order_posts()) == 1
    assert result["fallback_status"] == "not_placed_no_expiry"
    assert result["filled_size_shares"] == 4.0


def test_gtd_fallback_payload_and_describe(tmp_path, exchange):
    start = _start()
    ticket = _ticket(start)
    assert "GOOD_TILL_CANCEL" not in ticket.describe() and "GTC" not in ticket.describe()
    result = _executor(tmp_path, _snapshot(start)).execute(
        ticket, _pick_row(start), execute_flag=True, user_command=True
    )
    primary, fallback = exchange.order_posts()
    assert fallback["json"]["tif"] == GTD
    assert fallback["json"]["goodTillTime"] == _good_till(start)
    assert fallback["json"]["participateDontInitiate"] is True
    assert fallback["json"]["price"] == primary["json"]["price"]
    assert fallback["json"]["quantity"] == 6.0
    assert fallback["headers"]["Idempotency-Key"] != primary["headers"]["Idempotency-Key"]
    assert result["good_till_time"] == _good_till(start)
    assert result["fallback_status"] == "resting"
    assert ("GET", f"/v1/order/{result['fallback_order_id']}") in [
        (c["method"], c["path"]) for c in exchange.calls
    ]


# ------------------------------------------------------------------ D-07: preview probe and CLI


def _preview_body() -> dict[str, Any]:
    return {
        "marketSlug": SLUG,
        "intent": "ORDER_INTENT_BUY_LONG",
        "tif": GTD,
        "goodTillTime": "2026-11-25T23:58:30Z",
    }


def test_preview_requires_an_explicit_user_command(tmp_path, exchange):
    with pytest.raises(pe.ExecutionGateError, match="explicit user command"):
        _executor(tmp_path, _snapshot(_start())).preview_order(_preview_body(), user_command=False)
    assert exchange.calls == []


def test_preview_posts_once_to_the_preview_endpoint(tmp_path, exchange):
    result = _executor(tmp_path, _snapshot(_start())).preview_order(_preview_body(), user_command=True)
    assert result == {"preview": "ok"}
    (call,) = exchange.calls
    assert (call["method"], call["path"]) == ("POST", "/v1/order/preview")
    assert call["json"] == {"request": _preview_body()}
    assert exchange.order_posts() == []


def test_preview_ambiguous_outcome_is_never_resent(tmp_path, exchange):
    exchange.preview_script = [httpx.ReadTimeout("lost")]
    with pytest.raises(pe.OrderSubmitUnknownError):
        _executor(tmp_path, _snapshot(_start())).preview_order(_preview_body(), user_command=True)
    assert len(exchange.calls) == 1


def test_cli_reports_an_unknown_outcome_instead_of_refused():
    from model_prediction.cli import commands

    class Unknown:
        def execute(self, *_a, **_k):
            raise pe.OrderSubmitUnknownError(
                "read timed out", idempotency_key="mp-x-p", market_slug=SLUG, leg="primary", intent_id="x"
            )

    class Placed:
        def execute(self, *_a, **_k):
            return {"status": "submitted", "order_id": "o-1"}

    reported = commands._execute_or_report_unknown(Unknown(), None, {}, execute_flag=True, user_command=True)
    assert reported["status"] == "submit_unknown"
    assert reported["idempotency_key"] == "mp-x-p"
    assert "Do not resubmit" in reported["note"]
    placed = commands._execute_or_report_unknown(Placed(), None, {}, execute_flag=True, user_command=True)
    assert placed == {"status": "submitted", "order_id": "o-1"}


# ------------------------------------------------------------------ B5: live-ask re-check


@pytest.mark.parametrize(
    ("ask", "allowed"),
    [(0.55, False), (0.54, True), (0.50, True), (0.49, False), (None, False), (float("nan"), False)],
)
def test_live_ask_deviation_two_cents_either_way(tmp_path, exchange, ask, allowed):
    start = _start()
    exchange.ioc_fill = 10.0
    client = _executor(tmp_path, _snapshot(start, ask=ask))
    if allowed:
        assert (
            client.execute(_ticket(start), _pick_row(start), execute_flag=True, user_command=True)["status"]
            == "submitted"
        )
    else:
        with pytest.raises(pe.ExecutionGateError, match="live ask"):
            client.execute(_ticket(start), _pick_row(start), execute_flag=True, user_command=True)
        assert exchange.order_posts() == []


def test_unset_deviation_ignores_a_missing_ask(tmp_path, exchange):
    start = _start()
    exchange.ioc_fill = 10.0
    snap = _snapshot(start)
    snap["long"] = {"description": "Home U -3.5"}
    result = _executor(tmp_path, snap).execute(
        _ticket(start, max_live_price_deviation=None), _pick_row(start), execute_flag=True, user_command=True
    )
    assert result["status"] == "submitted"


# ------------------------------------------------------------------ B3 / ledger truth


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "pick_id": "pick-1",
        "model_id": SPREAD_V7,
        "market_slug": SLUG,
        "token_side": "long",
        "sport": "ncaab",
        "selection": "home",
        "limit_price": 0.52,
        "shares": 4.0,
        "cost_usd": 2.08,
        "unit_value_usd": 5.0,
        "edge": 0.06,
        "event_start_utc": iso_utc(_start()),
        "timestamp_utc": iso_utc(utc_now()),
        "event_id": "ev-1",
        "market_type": "spread",
    }
    payload.update(overrides)
    return payload


def test_live_ledger_append_failure_raises(tmp_path, monkeypatch):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    j_path.mkdir()  # appending to a directory fails with an OSError on every platform
    monkeypatch.setattr(
        auto_buyer_ledger,
        "resolve_auto_buyer_paths",
        lambda **_k: (j_path, tmp_path / "auto_buyer_picks.xlsx"),
    )
    monkeypatch.setattr(auto_buyer_ledger, "log_auto_buyer_event", lambda *a, **k: None)
    with pytest.raises(auto_buyer_ledger.AutoBuyerLedgerWriteError):
        auto_buyer_ledger.record_auto_buy_execution(
            _payload(), order_id="ord-1", order_state="ORDER_STATE_FILLED", jsonl_path=j_path
        )


def test_paper_ledger_append_failure_only_warns(tmp_path, caplog):
    j_path = tmp_path / "paper" / "auto_buyer_ledger.jsonl"
    j_path.mkdir(parents=True)
    with caplog.at_level(logging.WARNING):
        auto_buyer_ledger.record_auto_buy_execution(
            _payload(), order_id="PAPER-1", order_state="ORDER_STATE_FILLED", jsonl_path=j_path
        )
    assert "Failed to append" in caplog.text


def test_live_topup_rewrite_failure_raises(tmp_path, monkeypatch):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    j_path.write_text(json.dumps({"pick_id": "pick-1", "market_slug": SLUG, "token_side": "long"}) + "\n")
    monkeypatch.setattr(
        auto_buyer_ledger,
        "resolve_auto_buyer_paths",
        lambda **_k: (j_path, tmp_path / "auto_buyer_picks.xlsx"),
    )
    monkeypatch.setattr(auto_buyer_ledger, "log_auto_buyer_event", lambda *a, **k: None)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(auto_buyer_ledger, "_write_jsonl_atomic", boom)
    with pytest.raises(auto_buyer_ledger.AutoBuyerLedgerWriteError):
        auto_buyer_ledger.record_auto_buy_execution(
            _payload(is_topup=True), order_id="ord-2", order_state="ORDER_STATE_FILLED", jsonl_path=j_path
        )


def test_explicit_strict_write_raises_off_the_live_path(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    j_path.mkdir()
    with pytest.raises(auto_buyer_ledger.AutoBuyerLedgerWriteError):
        auto_buyer_ledger.record_auto_buy_execution(
            _payload(), order_id="ord-1", jsonl_path=j_path, raise_on_write_failure=True
        )


def test_submit_unknown_row_fields_and_settle_leaves_it_open(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    row = auto_buyer_ledger.record_auto_buy_execution(
        _payload(
            shares=0.0,
            cost_usd=0.0,
            submit_unknown=True,
            requested_shares=10.0,
            requested_cost_usd=5.2,
            fill_known=False,
            intent_id="i-1",
            idempotency_key="mp-i-1-p",
            fallback_idempotency_key="mp-i-1-f",
            event_start_utc=iso_utc(utc_now() - timedelta(hours=6)),
        ),
        order_id="",
        order_state="ORDER_STATE_SUBMIT_UNKNOWN",
        jsonl_path=j_path,
    )
    assert row["submit_unknown"] is True
    assert row["order_id"] == ""
    assert row["order_ids"] == []
    assert row["shares"] == 0.0 and row["cost_usd"] == 0.0
    assert row["requested_cost_usd"] == 5.2
    assert row["fill_known"] is False
    assert row["order_state"] == "ORDER_STATE_SUBMIT_UNKNOWN"
    assert row["idempotency_key"] == "mp-i-1-p"

    class _Espn:
        def scoreboard(self, *_a, **_k):
            return {}

    summary = auto_buyer_ledger.settle_auto_buyer_ledger(data_root=tmp_path, espn=_Espn())
    (after,) = auto_buyer_ledger.read_auto_buyer_ledger(jsonl_path=j_path)
    assert after["status"] == "open"
    assert after["result"] == "open"
    assert summary["pending"] >= 1


# ------------------------------------------------------------------ buyer level


COHORT = auto_executor.BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.12, "live")


def _brow(pick_id: str, event: str, *, ask: float = 0.51, start_minutes: float = 20.0) -> dict[str, Any]:
    now = utc_now()
    return {
        "pick_id": pick_id,
        "event_id": event,
        "model_version": SPREAD_V7,
        "league": "ncaab",
        "market_type": "spread",
        "selection": "home",
        "status": "open",
        "decision": "CALL",
        "reason_code": "FLAT_CALL",
        "edge": "0.07",
        "line": -3.5,
        "model_probability": 0.58,
        "market_probability": ask,
        "units": 2.0,
        "home_team": f"{event} home",
        "away_team": f"{event} away",
        "market_slug": f"asc-cbb-{event}-2026-11-25-neg-3pt5",
        "token_side": "long",
        "test_ask": ask,
        "created_at_utc": iso_utc(now - timedelta(minutes=1)),
        "event_start_utc": iso_utc(now + timedelta(minutes=start_minutes)),
        "market_quote_provenance": "archived_two_sided_bbo",
        "market_quote_timestamp_valid": "True",
        "market_quote_observed_at_utc": iso_utc(now - timedelta(minutes=2)),
        "market_snapshot_hash": "abc",
        "market_snapshot_archive_path": "archive.jsonl",
    }


def _quote_fn(rows: list[dict[str, Any]]):
    by_slug = {r["market_slug"]: r for r in rows}

    def quote(slug: str) -> dict[str, Any]:
        row = by_slug[slug]
        return {
            "market_slug": slug,
            "side": "long",
            "ask": row["test_ask"],
            "observed_at_utc": iso_utc(utc_now()),
            "market_state": "MARKET_STATE_OPEN",
            "team": row["home_team"],
            "line": -3.5,
            "event_start_utc": row["event_start_utc"],
            "long": {"description": f"{row['home_team']} -3.5", "ask": row["test_ask"]},
            "short": {"description": f"{row['away_team']} +3.5", "ask": 0.50},
        }

    return quote


@pytest.fixture
def buyer_env(monkeypatch):
    for name, value in CREDS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        auto_executor,
        "calibrate_for_serving",
        lambda _model, _market, probability, **_k: ServingCalibration(
            probability, probability, False, "identity", "test_isolation"
        ),
    )
    monkeypatch.setattr(auto_executor, "_load_pick_input_flags", lambda *_a, **_k: {})
    monkeypatch.setattr(auto_buyer_ledger, "log_auto_buyer_event", lambda *a, **k: None)

    class _NoExchange:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            raise RuntimeError("tests must never reach the exchange")

    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.PolymarketUSClient", _NoExchange)


def _buyer(tmp_path: Path, rows: list[dict[str, Any]], **overrides: Any) -> auto_executor.AutoPolymarketBuyer:
    kwargs: dict[str, Any] = {
        "mode": "live",
        "execute_live": True,
        "unit_value_usd": 5.0,
        "max_game_stake_usd": 25.0,
        "max_daily_spend_usd": 500.0,
        "whitelisted_models": (),
        "disabled_sport_markets": (("ncaab", "spread"),),
        "cohorts": (COHORT,),
        "edge_scaled_sizing_enabled": False,
        "require_model_timestamp": True,
        "capture_missing_slates": False,
        "dedup_own_ledger_only": True,
    }
    kwargs.update(overrides)
    return auto_executor.AutoPolymarketBuyer(
        config=auto_executor.AutoExecutionConfig(**kwargs),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        live_quote_fn=_quote_fn(rows),
        data_root=tmp_path,
    )


def _ledger(tmp_path: Path) -> list[dict[str, Any]]:
    return auto_buyer_ledger.read_auto_buyer_ledger(jsonl_path=tmp_path / "auto_buyer_ledger.jsonl")


def test_tracer_one_live_cohort_order_end_to_end(tmp_path, exchange, buyer_env):
    """Tracer: idempotent IOC, GTD remainder at start - 90 s read back by id, one ledger row, and the
    intent audited before the executed event with the event fields dedup keys on."""
    row = _brow("t1", "g1")
    result = _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])

    posts = exchange.order_posts()
    assert len(posts) == 2
    ioc, gtd = posts
    assert ioc["json"]["tif"] == IOC
    assert ioc["json"]["marketSlug"] == row["market_slug"]
    ioc_key = ioc["headers"]["Idempotency-Key"]
    gtd_key = gtd["headers"]["Idempotency-Key"]
    assert ioc_key.startswith("mp-") and ioc_key.endswith("-p") and len(ioc_key) <= 255
    assert gtd_key.endswith("-f") and gtd_key != ioc_key
    start = parse_utc(row["event_start_utc"])
    assert gtd["json"]["tif"] == GTD
    assert gtd["json"]["goodTillTime"] == _good_till(start)
    assert gtd["json"]["participateDontInitiate"] is True
    assert gtd["json"]["price"] == ioc["json"]["price"]
    assert sum(1 for p in posts if p["json"]["tif"] == IOC) == 1
    gtd_id = next(oid for oid in exchange.orders if oid.startswith("gtd-"))
    assert ("GET", f"/v1/order/{gtd_id}") in [(c["method"], c["path"]) for c in exchange.calls]

    (ledger_row,) = _ledger(tmp_path)
    assert ledger_row["shares"] == 4.0
    assert ledger_row["fallback_order_id"] == gtd_id
    assert ledger_row["fallback_status"] == "resting"
    assert ledger_row["idempotency_key"] == ioc_key
    assert ledger_row["fallback_idempotency_key"] == gtd_key
    assert ledger_row["good_till_time"] == _good_till(start)

    types = _audit_types(tmp_path)
    assert types.index("order_intent") < types.index("order_executed")
    executed = _audit_events(tmp_path, "order_executed")[0]["payload"]
    assert executed["event_id"] == "g1"
    assert executed["selection"] == "home"
    assert executed["market_type"] == "spread"
    assert executed["idempotency_key"] == ioc_key
    assert result.alarms == []
    assert result.live_halt_reason is None


def test_unknown_ioc_records_an_unknown_row_alarms_halts_and_blocks(tmp_path, exchange, buyer_env):
    rows = [_brow("u1", "g1"), _brow("u2", "g2")]
    exchange.post_script = [httpx.ReadTimeout("read timed out")]
    result = _buyer(tmp_path, rows).evaluate_and_execute([dict(r) for r in rows])

    assert len(exchange.order_posts()) == 1, "the second candidate must not be submitted"
    assert result.live_halt_reason == "submit_unknown"
    assert [a["alarm"] for a in result.alarms] == ["submit_unknown"]
    assert result.submitted_orders[0]["status"] == "submit_unknown"
    (unknown,) = _ledger(tmp_path)
    assert unknown["pick_id"] == "u1"
    assert unknown["submit_unknown"] is True
    assert unknown["order_state"] == "ORDER_STATE_SUBMIT_UNKNOWN"
    assert unknown["shares"] == 0.0 and unknown["cost_usd"] == 0.0
    assert unknown["fill_known"] is False
    requested = unknown["requested_cost_usd"]
    assert requested == pytest.approx(10.0, abs=0.06)
    assert unknown["idempotency_key"] == exchange.order_posts()[0]["headers"]["Idempotency-Key"]

    # Caps: the requested cost counts toward the daily and per-game caps.
    assert auto_executor._today_auto_buyer_totals(tmp_path)[0] == pytest.approx(requested)
    index = _buyer(tmp_path, rows)._build_bought_index()
    assert sum(index["game_exposure"]["event:g1"].values()) == pytest.approx(requested)

    # Next cycle: the unknown pick is blocked, nothing is resent.
    again = _buyer(tmp_path, rows[:1]).evaluate_and_execute([dict(rows[0])])
    assert again.rejected_dedup == 1
    assert len(exchange.order_posts()) == 1


def test_unknown_on_a_topup_is_not_merged_and_blocks_the_next_cycle(tmp_path, exchange, buyer_env):
    row = _brow("p1", "g1")
    partial = {
        "order_id": "ioc-old",
        "order_ids": ["ioc-old"],
        "pick_id": "p1",
        "event_id": "g1",
        "executed_at_utc": iso_utc(utc_now() - timedelta(minutes=30)),
        "event_start_utc": row["event_start_utc"],
        "sport": "NCAAB",
        "away_team": row["away_team"],
        "home_team": row["home_team"],
        "market_type": "spread",
        "selection": "home",
        "market_slug": row["market_slug"],
        "token_side": "long",
        "shares": 2.0,
        "entry_price": 0.51,
        "cost_usd": 1.02,
        "unit_value_usd": 5.0,
        "model_id": SPREAD_V7,
        "order_state": "ORDER_STATE_PARTIALLY_FILLED",
        "status": "open",
        "result": "open",
        "fallback_order_id": None,
        "fallback_reconciled": True,
        "fill_known": True,
    }
    (tmp_path / "auto_buyer_ledger.jsonl").write_text(json.dumps(partial) + "\n")
    exchange.post_script = [httpx.ReadTimeout("read timed out")]
    result = _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    assert result.live_halt_reason == "submit_unknown"
    rows = _ledger(tmp_path)
    assert len(rows) == 2, "the unknown top-up must be its own row, never merged into the filled one"
    assert rows[0]["shares"] == 2.0
    assert rows[1]["submit_unknown"] is True

    again = _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    assert again.rejected_dedup == 1
    assert len(exchange.order_posts()) == 1


class ScriptedExecutor:
    """Stands in for PolymarketExecutor at the buyer boundary. Never trades."""

    outcomes: ClassVar[list[Any]] = []
    executed: ClassVar[list[str]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def portfolio_snapshot(self) -> dict[str, Any]:
        return {"positions": {}}

    def execute(self, ticket: Any, pick_row: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        ScriptedExecutor.executed.append(ticket.pick_id)
        outcome = ScriptedExecutor.outcomes.pop(0) if ScriptedExecutor.outcomes else {}
        if isinstance(outcome, BaseException):
            raise outcome
        return {
            "status": "submitted",
            "order_id": f"sim-{ticket.pick_id}",
            "order_ids": [f"sim-{ticket.pick_id}"],
            "order_state": "ORDER_STATE_FILLED",
            "filled_size_shares": ticket.size_shares,
            "estimated_filled_cost_usd": ticket.estimated_cost_usd,
            "fill_known": True,
            **outcome,
        }


@pytest.fixture
def scripted(monkeypatch, buyer_env):
    ScriptedExecutor.outcomes = []
    ScriptedExecutor.executed = []
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", ScriptedExecutor)
    monkeypatch.setattr(auto_executor.time, "sleep", lambda *_a: None)
    return ScriptedExecutor


def test_audit_failure_after_submit_records_alarms_and_halts(tmp_path, scripted):
    rows = [_brow("a1", "g1"), _brow("a2", "g2")]
    scripted.outcomes = [{"audit_error": "AuditLockTimeout: lock busy"}]
    result = _buyer(tmp_path, rows).evaluate_and_execute([dict(r) for r in rows])
    assert scripted.executed == ["a1"]
    assert [a["alarm"] for a in result.alarms] == ["audit_failed_after_submit"]
    assert result.live_halt_reason == "audit_failed_after_submit"
    assert [r["pick_id"] for r in _ledger(tmp_path)] == ["a1"]
    assert result.submitted_orders[0]["status"] == "submitted"


def test_ledger_write_failure_after_submit_alarms_and_halts(tmp_path, scripted, monkeypatch, caplog):
    rows = [_brow("l1", "g1"), _brow("l2", "g2")]

    def broken(*_a, **_k):
        raise auto_buyer_ledger.AutoBuyerLedgerWriteError("live ledger append failed: disk full")

    monkeypatch.setattr(auto_buyer_ledger, "record_auto_buy_execution", broken)
    with caplog.at_level(logging.ERROR):
        result = _buyer(tmp_path, rows).evaluate_and_execute([dict(r) for r in rows])
    assert scripted.executed == ["l1"]
    assert [a["alarm"] for a in result.alarms] == ["ledger_write_failed"]
    assert result.live_halt_reason == "ledger_write_failed"
    assert result.submitted_orders[0]["status"] == "submitted", "a placed order is never 'refused'"
    assert "sim-l1" in caplog.text


def test_fallback_problem_alarms_without_halting(tmp_path, scripted):
    rows = [_brow("f1", "g1"), _brow("f2", "g2")]
    scripted.outcomes = [
        {
            "filled_size_shares": 2.0,
            "estimated_filled_cost_usd": 1.02,
            "order_state": "ORDER_STATE_PARTIALLY_FILLED",
            "fallback_status": "rejected",
            "fallback_error": "REFUSED by Polymarket US (400): bad goodTillTime",
        }
    ]
    result = _buyer(tmp_path, rows).evaluate_and_execute([dict(r) for r in rows])
    assert scripted.executed == ["f1", "f2"]
    assert [a["alarm"] for a in result.alarms] == ["fallback_rejected"]
    assert result.live_halt_reason is None
    assert _ledger(tmp_path)[0]["fallback_error"].endswith("bad goodTillTime")


def test_gate_refusal_before_submission_stays_refused(tmp_path, scripted):
    rows = [_brow("r1", "g1"), _brow("r2", "g2")]
    scripted.outcomes = [pe.ExecutionGateError("REFUSED: live ask moved")]
    result = _buyer(tmp_path, rows).evaluate_and_execute([dict(r) for r in rows])
    assert scripted.executed == ["r1", "r2"]
    assert result.submitted_orders[0]["status"] == "refused"
    assert result.alarms == []


def test_live_tickets_carry_expiry_and_price_deviation(tmp_path, monkeypatch, buyer_env):
    seen: list[Any] = []

    class Capture(ScriptedExecutor):
        def execute(self, ticket, pick_row, **kwargs):
            seen.append(ticket)
            return super().execute(ticket, pick_row, **kwargs)

    monkeypatch.setattr(auto_executor, "PolymarketExecutor", Capture)
    monkeypatch.setattr(auto_executor.time, "sleep", lambda *_a: None)
    row = _brow("e1", "g1")
    _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    (ticket,) = seen
    expected = parse_utc(row["event_start_utc"]) - timedelta(
        seconds=auto_executor.LIVE_FALLBACK_EXPIRY_LEAD_SECONDS
    )
    assert auto_executor.LIVE_FALLBACK_EXPIRY_LEAD_SECONDS == 90
    assert parse_utc(ticket.resting_expires_at_utc) == expected
    assert ticket.max_live_price_deviation == auto_executor.LIVE_MAX_PRICE_DEVIATION == 0.02


def test_kill_switch_mid_pass_stops_the_next_live_submission(tmp_path, scripted, monkeypatch):
    rows = [_brow("k1", "g1"), _brow("k2", "g2")]
    states = iter([{"enabled": True}, {"enabled": False}])
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: dict(next(states)))
    result = _buyer(tmp_path, rows, recheck_enabled_before_submit=True).evaluate_and_execute(
        [dict(r) for r in rows]
    )
    assert scripted.executed == ["k1"]
    assert result.live_halt_reason == "kill_switch"


@pytest.mark.parametrize("raw", ["true", 1, None])
def test_kill_switch_recheck_is_strict(tmp_path, scripted, monkeypatch, raw):
    rows = [_brow("s1", "g1")]
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: {"enabled": raw})
    result = _buyer(tmp_path, rows, recheck_enabled_before_submit=True).evaluate_and_execute(
        [dict(r) for r in rows]
    )
    assert scripted.executed == []
    assert result.live_halt_reason == "kill_switch"


# ------------------------------------------------------------------ cycle and dashboard


def _cycle_state(enabled: bool = True) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "mode": "paper",
        "unit_value_usd": 5.0,
        "max_game_stake_usd": 25.0,
        "max_daily_spend_usd": 500.0,
        "max_daily_loss_usd": 250.0,
        "max_event_lookahead_hours": 0.5,
        "whitelist_models": [],
        "disabled_sport_markets": ["ncaab:spread", "ncaab:total"],
        "cohorts": [
            {
                "model_id": SPREAD_V7,
                "sport": "ncaab",
                "market": "spread",
                "min_saved_edge": 0.05,
                "max_saved_edge_exclusive": 0.12,
                "mode": "live",
                "enabled": True,
            }
        ],
    }


def _cycle_harness(monkeypatch, tmp_path: Path, rows: list[dict[str, Any]]) -> dict[str, list[Any]]:
    state = _cycle_state()
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", tmp_path / "paper_auto_buyer")
    monkeypatch.setattr(
        auto_executor, "AUTO_BUYER_CYCLE_HISTORY_PATH", tmp_path / "logs" / "auto_buyer_cycle_history.jsonl"
    )
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: dict(state))
    calls: dict[str, list[Any]] = {"saved": [], "history": []}
    monkeypatch.setattr(auto_executor, "save_auto_buyer_state", lambda s: calls["saved"].append(s))
    monkeypatch.setattr(
        auto_executor,
        "_append_auto_buyer_cycle_history",
        lambda summary, history_path=None: calls["history"].append(summary),
    )
    monkeypatch.setattr(auto_executor, "_read_cohort_candidate_picks", lambda: [dict(r) for r in rows])
    monkeypatch.setattr("model_prediction.dashboard.picks.read_picks", lambda *a, **k: [])
    monkeypatch.setattr("model_prediction.dashboard.picks.read_flat_picks", lambda *a, **k: [])
    monkeypatch.setattr("model_prediction.dashboard.picks._parse_research_picks", lambda *a, **k: [])

    def decorate(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **row,
            "quote": {
                "market_slug": row["market_slug"],
                "side": "long",
                "ask": row.get("test_ask"),
                "fresh": True,
                "age_seconds": 5,
                "market_state": "MARKET_STATE_OPEN",
            },
        }

    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", decorate)
    monkeypatch.setattr(auto_executor, "_capture_missing_active_snapshot_slates", lambda *a, **k: [])
    monkeypatch.setattr(auto_buyer_ledger, "reconcile_pending_auto_buyer_fallbacks", lambda **_k: {})
    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", lambda *a, **k: {})
    monkeypatch.delenv("POLYMARKET_KEY_ID", raising=False)
    return calls


def test_cycle_saves_state_and_history_then_raises_the_alarm(tmp_path, scripted, monkeypatch):
    rows = [_brow("c1", "g1"), _brow("c2", "g2")]
    calls = _cycle_harness(monkeypatch, tmp_path, rows)
    scripted.outcomes = [
        pe.OrderSubmitUnknownError("read timed out", idempotency_key="mp-x-p", market_slug="s", leg="primary")
    ]
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as caught:
        auto_executor.run_auto_buyer_cycle()
    assert isinstance(caught.value, RuntimeError)
    assert scripted.executed == ["c1"]
    summary = caught.value.run_summary
    live = summary["cohort_passes"]["live"]
    assert [a["alarm"] for a in live["alarms"]] == ["submit_unknown"]
    assert live["live_halt_reason"] == "submit_unknown"
    assert calls["saved"] and calls["saved"][-1]["last_run"] is summary
    assert any(h.get("pass") == "cohort_live" and h.get("alarms") for h in calls["history"])


def test_cycle_without_alarms_returns_normally(tmp_path, scripted, monkeypatch):
    rows = [_brow("n1", "g1")]
    _cycle_harness(monkeypatch, tmp_path, rows)
    summary = auto_executor.run_auto_buyer_cycle()
    assert scripted.executed == ["n1"]
    assert summary["cohort_passes"]["live"]["alarms"] == []


def test_live_cohort_pass_config_rechecks_enabled(monkeypatch, tmp_path, scripted):
    _cycle_harness(monkeypatch, tmp_path, [])
    seen: list[Any] = []
    original = auto_executor.AutoPolymarketBuyer.evaluate_and_execute

    def spy(self, picks=None):
        seen.append(self.config)
        return original(self, picks)

    monkeypatch.setattr(auto_executor.AutoPolymarketBuyer, "evaluate_and_execute", spy)
    auto_executor.run_auto_buyer_cycle()
    live = [c for c in seen if c.mode == "live"]
    assert live and all(c.recheck_enabled_before_submit for c in live)


def test_dashboard_run_route_answers_alarm_and_releases_the_lock(monkeypatch, tmp_path):
    from io import BytesIO
    from unittest.mock import Mock

    from model_prediction.daily_lock import acquire_lock
    from model_prediction.dashboard import routes
    from model_prediction.dashboard.common import _DASHBOARD_TOKEN

    lock_path = tmp_path / "locks" / "daily.lock"
    monkeypatch.setattr(routes, "_auto_buyer_run_lock_path", lambda: lock_path)

    def alarmed(**_k):
        raise auto_executor.LiveAutoBuyerAlarm(
            "live auto-buyer alarm: submit_unknown",
            run_summary={"mode": "LIVE", "cohort_passes": {}},
            alarms=[{"alarm": "submit_unknown"}],
        )

    monkeypatch.setattr(routes, "run_auto_buyer_cycle", alarmed)
    payload = json.dumps({"confirm": True}).encode("utf-8")
    handler = routes.Handler.__new__(routes.Handler)
    handler.path = "/api/auto-buyer/run"
    handler.headers = {"Content-Length": str(len(payload)), "X-Dashboard-Token": _DASHBOARD_TOKEN}
    handler.rfile = BytesIO(payload)
    handler.wfile = BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler.do_POST()
    body = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert body["status"] == "alarm"
    assert body["alarms"] == [{"alarm": "submit_unknown"}]
    assert body["run"] == {"mode": "LIVE", "cohort_passes": {}}
    relock = acquire_lock(lock_path)
    assert relock is not None
    relock.close()


# ------------------------------------------------------------------ dedup from the audit


def test_audit_unknown_and_dangling_intent_block_live_dedup_but_rejected_does_not(tmp_path, buyer_env):
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append(
        "order_intent",
        "rej",
        {"intent_id": "i-rej", "action": "buy", "market_slug": "s-rej", "token_side": "long"},
    )
    audit.append("order_rejected", "rej", {"intent_id": "i-rej", "error": "400"})
    audit.append(
        "order_intent",
        "dangling",
        {
            "intent_id": "i-dang",
            "action": "buy",
            "market_slug": "s-dang",
            "token_side": "long",
            "event_id": "ev-d",
            "selection": "home",
            "market_type": "spread",
        },
    )
    audit.append(
        "order_submit_unknown",
        "unk",
        {"intent_id": "i-unk", "action": "buy", "market_slug": "s-unk", "token_side": "long"},
    )
    live_index = _buyer(tmp_path, [])._build_bought_index()
    assert "rej" not in live_index["pick_ids"]
    assert ("s-rej", "long") not in live_index["market_sides"]
    assert {"dangling", "unk"} <= live_index["pick_ids"]
    assert {("s-dang", "long"), ("s-unk", "long")} <= live_index["market_sides"]
    assert ("ev-d", "home") in live_index["event_selections"]
    assert ("ev-d", "spread") in live_index["event_markets"]

    paper_cohort = auto_executor.BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.12, "paper")
    paper = auto_executor.AutoPolymarketBuyer(
        config=auto_executor.AutoExecutionConfig(
            mode="paper", cohorts=(paper_cohort,), dedup_own_ledger_only=True, capture_missing_slates=False
        ),
        audit=audit,
        data_root=tmp_path,
    )
    paper_index = paper._build_bought_index()
    assert "dangling" in paper_index["pick_ids"]
    assert ("ev-d", "home") not in paper_index["event_selections"], "a live fill never blocks a paper game"
    assert ("ev-d", "spread") not in paper_index["event_markets"]


# ------------------------------------------------------------------ review cycle 1


def _switch_on(monkeypatch) -> None:
    monkeypatch.setattr(pe, "ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY", True)


@pytest.mark.parametrize(
    "script",
    [
        pytest.param(
            [httpx.ReadTimeout("lost"), httpx.ConnectError("down"), httpx.ConnectError("down")],
            id="read-timeout-then-connect-errors",
        ),
        pytest.param([(502, {"error": "bad gateway"}), (429, {"e": 1}), (429, {"e": 1})], id="5xx-then-429s"),
        pytest.param([httpx.ReadTimeout("lost"), (400, {"error": "duplicate"})], id="read-timeout-then-400"),
    ],
)
def test_switch_on_a_refusal_after_an_ambiguous_attempt_stays_unknown(
    tmp_path, exchange, monkeypatch, script
):
    """Review fix 2: once one attempt may have reached the exchange, no later attempt can turn the
    outcome into "refused" (which would audit order_rejected and make the pick buyable again)."""
    _switch_on(monkeypatch)
    start = _start()
    exchange.post_script = list(script)
    with pytest.raises(pe.OrderSubmitUnknownError):
        _executor(tmp_path, _snapshot(start)).execute(
            _ticket(start), _pick_row(start), execute_flag=True, user_command=True
        )
    assert len(exchange.order_posts()) == len(script)
    assert len(_same_key_posts(exchange)) == 1
    types = _audit_types(tmp_path)
    assert "order_submit_unknown" in types
    assert "order_rejected" not in types


def test_switch_on_fallback_leg_refusal_after_ambiguity_is_unknown_not_rejected(
    tmp_path, exchange, monkeypatch
):
    _switch_on(monkeypatch)
    start = _start()
    exchange.post_script = [exchange.default_post, httpx.ReadTimeout("lost"), (400, {"error": "duplicate"})]
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    posts = exchange.order_posts()
    assert len(posts) == 3
    assert posts[1]["headers"]["Idempotency-Key"] == posts[2]["headers"]["Idempotency-Key"]
    assert result["fallback_status"] == "submit_unknown"
    assert result["fallback_order_id"] is None
    assert result["filled_size_shares"] == 4.0


def _advancing_clock(monkeypatch, seconds_per_sleep: float) -> None:
    """utc_now keeps running, and every retry back-off jumps it forward by ``seconds_per_sleep``."""
    offset = [0.0]
    monkeypatch.setattr(pe, "utc_now", lambda: utc_now() + timedelta(seconds=offset[0]))
    monkeypatch.setattr(pe.time, "sleep", lambda *_a: offset.__setitem__(0, offset[0] + seconds_per_sleep))


def test_no_resend_after_the_event_starts(tmp_path, exchange, monkeypatch):
    """Review fix 4: a connect-phase failure sent nothing, but the resend after its back-off must not
    go out once the event has started."""
    start = _start(minutes=5)
    _advancing_clock(monkeypatch, seconds_per_sleep=600)
    exchange.ioc_fill = 10.0
    exchange.post_script = [httpx.ConnectError("down")]
    with pytest.raises(pe.ExecutionGateError) as caught:
        _executor(tmp_path, _snapshot(start)).execute(
            _ticket(start), _pick_row(start), execute_flag=True, user_command=True
        )
    assert not isinstance(caught.value, pe.OrderSubmitUnknownError)
    assert "never sent" in str(caught.value)
    assert len(exchange.order_posts()) == 1
    assert _audit_types(tmp_path)[-1] == "order_rejected"


def test_no_resend_after_the_event_starts_keeps_an_earlier_unknown(tmp_path, exchange, monkeypatch):
    _switch_on(monkeypatch)
    start = _start(minutes=5)
    _advancing_clock(monkeypatch, seconds_per_sleep=600)
    exchange.post_script = [httpx.ReadTimeout("lost")]
    with pytest.raises(pe.OrderSubmitUnknownError):
        _executor(tmp_path, _snapshot(start)).execute(
            _ticket(start), _pick_row(start), execute_flag=True, user_command=True
        )
    assert len(exchange.order_posts()) == 1
    assert "order_rejected" not in _audit_types(tmp_path)


def test_resend_before_the_event_start_still_goes_out(tmp_path, exchange, monkeypatch):
    start = _start(minutes=30)
    _advancing_clock(monkeypatch, seconds_per_sleep=1)
    exchange.ioc_fill = 10.0
    exchange.post_script = [httpx.ConnectError("down")]
    result = _executor(tmp_path, _snapshot(start)).execute(
        _ticket(start), _pick_row(start), execute_flag=True, user_command=True
    )
    assert result["status"] == "submitted"
    assert len(exchange.order_posts()) == 2


def test_single_order_refusal_names_the_prior_event_and_intent(tmp_path, exchange):
    """Review fix 5: the refusal read "order None at None" for an unknown or dangling intent."""
    start = _start()
    exchange.post_script = [httpx.ReadTimeout("lost")]
    client = _executor(tmp_path, _snapshot(start))
    with pytest.raises(pe.OrderSubmitUnknownError) as unknown:
        client.execute(_ticket(start), _pick_row(start), execute_flag=True, user_command=True)
    with pytest.raises(pe.ExecutionGateError, match="single_order policy refuses a second buy") as caught:
        client.execute(_ticket(start), _pick_row(start), execute_flag=True, user_command=True)
    message = str(caught.value)
    assert "None" not in message
    assert "order_submit_unknown" in message
    assert unknown.value.intent_id in message

    other = tmp_path / "other"
    other.mkdir()
    AuditLog(other / "audit.jsonl").append(
        "order_intent",
        "pick-1",
        {"intent_id": "abc", "action": "buy", "created_at_utc": "2026-11-25T20:00:00Z"},
    )
    with pytest.raises(pe.ExecutionGateError, match="single_order") as dangling:
        _executor(other, _snapshot(start)).execute(
            _ticket(start), _pick_row(start), execute_flag=True, user_command=True
        )
    assert "None" not in str(dangling.value)
    assert "order_intent" in str(dangling.value)
    assert "abc" in str(dangling.value)


def _espn_final(away: str, home: str, away_score: int, home_score: int):
    class _Espn:
        def scoreboard(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            return {
                "events": [
                    {
                        "competitions": [
                            {
                                "status": {"type": {"completed": True}},
                                "competitors": [
                                    {
                                        "homeAway": "away",
                                        "team": {"displayName": away},
                                        "score": str(away_score),
                                    },
                                    {
                                        "homeAway": "home",
                                        "team": {"displayName": home},
                                        "score": str(home_score),
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }

    return _Espn()


def _settle_with_a_control_row(tmp_path: Path, unknown_pick: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move every row's start into the past, add an ordinary filled control row for the same game, and
    settle against a final score. The control row settling proves the fixture can settle a row."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    rows = _ledger(tmp_path)
    past = iso_utc(utc_now() - timedelta(hours=6))
    for r in rows:
        r["event_start_utc"] = past
    (target,) = [r for r in rows if r["pick_id"] == unknown_pick]
    control = {
        **target,
        "pick_id": "control",
        "order_id": "ioc-control",
        "order_ids": ["ioc-control"],
        "order_state": "ORDER_STATE_FILLED",
        "fallback_order_id": None,
        "fallback_status": "no_remainder",
        "fallback_reconciled": True,
        "fill_known": True,
        "shares": 4.0,
        "cost_usd": 2.04,
        "primary_filled_shares": 4.0,
        "primary_filled_cost_usd": 2.04,
    }
    control.pop("fallback_submit_unknown", None)
    j_path.write_text("".join(json.dumps(r) + "\n" for r in [*rows, control]), encoding="utf-8")
    auto_buyer_ledger.settle_auto_buyer_ledger(
        data_root=tmp_path, espn=_espn_final(target["away_team"], target["home_team"], 60, 80)
    )
    after = {r["pick_id"]: r for r in _ledger(tmp_path)}
    return after[unknown_pick], after["control"]


def test_unknown_fallback_leg_blocks_the_next_cycle_and_is_never_settled(tmp_path, exchange, buyer_env):
    """Review fix 1 (major): IOC fills 4, the GTD POST times out and no open order matches, so the
    remainder may rest or fill on the exchange. The row must not read as a top-up-eligible partial,
    and settlement must not settle it on the IOC shares alone."""
    row = _brow("fu1", "g1")
    exchange.post_script = [exchange.default_post, httpx.ReadTimeout("fallback response lost")]
    first = _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    assert [a["alarm"] for a in first.alarms] == ["fallback_submit_unknown"]
    assert len(exchange.order_posts()) == 2

    again = _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    assert len(exchange.order_posts()) == 2, "no order may be sent for a pick whose fallback leg is unknown"
    assert again.rejected_dedup == 1

    unknown, control = _settle_with_a_control_row(tmp_path, "fu1")
    assert control["status"] == "settled" and control["result"] == "win"
    assert unknown["status"] == "open"
    assert unknown["result"] == "open"


def test_unknown_fallback_leg_row_is_unreconciled_pending_and_counts_toward_the_caps(
    tmp_path, exchange, buyer_env
):
    row = _brow("fu2", "g1")
    exchange.post_script = [exchange.default_post, httpx.ReadTimeout("fallback response lost")]
    _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    (recorded,) = _ledger(tmp_path)
    assert recorded["shares"] == 4.0
    assert recorded["fallback_status"] == "submit_unknown"
    assert recorded["fallback_order_id"] is None
    assert recorded["fallback_reconciled"] is False
    assert recorded["fallback_submit_unknown"] is True
    assert recorded["fill_known"] is True, "the IOC fill is known; only the fallback leg is not"
    assert auto_buyer_ledger.is_fallback_submit_unknown(recorded)
    assert auto_executor._live_book_has_pending_orders(tmp_path / "auto_buyer_ledger.jsonl")

    remainder_cost = recorded["fallback_resting_shares"] * recorded["entry_price"]
    assert remainder_cost > 0
    expected = recorded["cost_usd"] + remainder_cost
    assert auto_executor._today_auto_buyer_totals(tmp_path)[0] == pytest.approx(expected, abs=0.01)
    index = _buyer(tmp_path, [row])._build_bought_index()
    assert "fu2" not in index["partially_filled"]
    assert "fu2" in index["pick_ids"]
    assert (row["market_slug"], "long") in index["market_sides"]
    assert ("g1", "home") in index["event_selections"]
    assert sum(index["game_exposure"]["event:g1"].values()) == pytest.approx(expected)


def test_zero_fill_ioc_with_an_unknown_fallback_is_held(tmp_path, exchange, buyer_env):
    row = _brow("fz1", "g1")
    exchange.ioc_fill = 0.0
    exchange.post_script = [exchange.default_post, httpx.ReadTimeout("fallback response lost")]
    _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    (recorded,) = _ledger(tmp_path)
    assert recorded["shares"] == 0.0
    assert auto_buyer_ledger.is_fallback_submit_unknown(recorded)
    assert auto_executor._live_book_has_pending_orders(tmp_path / "auto_buyer_ledger.jsonl")
    again = _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    assert len(exchange.order_posts()) == 2
    assert again.rejected_dedup == 1
    unknown, control = _settle_with_a_control_row(tmp_path, "fz1")
    assert control["status"] == "settled"
    assert unknown["status"] == "open"


def test_a_row_written_before_the_fix_is_still_held(tmp_path, buyer_env):
    """Rows recorded by 14bc409f carry fallback_reconciled True for an unknown leg; the fallback_status
    and the missing id alone must hold them."""
    start = iso_utc(utc_now() + timedelta(minutes=20))
    legacy = {
        "order_id": "ioc-1",
        "order_ids": ["ioc-1"],
        "pick_id": "old1",
        "event_id": "g7",
        "executed_at_utc": iso_utc(utc_now() - timedelta(minutes=10)),
        "event_start_utc": start,
        "sport": "NCAAB",
        "away_team": "g7 away",
        "home_team": "g7 home",
        "market_type": "spread",
        "selection": "home",
        "line": -3.5,
        "market_slug": "asc-cbb-g7-2026-11-25-neg-3pt5",
        "token_side": "long",
        "shares": 4.0,
        "entry_price": 0.51,
        "cost_usd": 2.04,
        "unit_value_usd": 5.0,
        "order_state": "ORDER_STATE_PARTIALLY_FILLED",
        "status": "open",
        "result": "open",
        "fallback_order_id": None,
        "fallback_status": "submit_unknown",
        "fallback_resting_shares": 15.6,
        "fallback_reconciled": True,
        "fill_known": True,
        "primary_filled_shares": 4.0,
        "primary_filled_cost_usd": 2.04,
    }
    (tmp_path / "auto_buyer_ledger.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    assert auto_buyer_ledger.is_fallback_submit_unknown(legacy)
    assert auto_executor._live_book_has_pending_orders(tmp_path / "auto_buyer_ledger.jsonl")
    index = _buyer(tmp_path, [])._build_bought_index()
    assert "old1" not in index["partially_filled"]
    assert "old1" in index["pick_ids"]
    assert auto_buyer_ledger.event_key_from_slug(legacy["market_slug"]) in index["held_events"]
    unknown, control = _settle_with_a_control_row(tmp_path, "old1")
    assert control["status"] == "settled"
    assert unknown["status"] == "open"


def _partial_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": "ioc-old",
        "order_ids": ["ioc-old"],
        "pick_id": row["pick_id"],
        "event_id": row["event_id"],
        "executed_at_utc": iso_utc(utc_now() - timedelta(minutes=30)),
        "event_start_utc": row["event_start_utc"],
        "sport": "NCAAB",
        "away_team": row["away_team"],
        "home_team": row["home_team"],
        "market_type": "spread",
        "selection": "home",
        "line": -3.5,
        "market_slug": row["market_slug"],
        "token_side": "long",
        "shares": 2.0,
        "entry_price": 0.51,
        "cost_usd": 1.02,
        "unit_value_usd": 5.0,
        "model_id": SPREAD_V7,
        "order_state": "ORDER_STATE_PARTIALLY_FILLED",
        "status": "open",
        "result": "open",
        "fallback_order_id": None,
        "fallback_status": "rejected",
        "fallback_reconciled": True,
        "fill_known": True,
        "primary_filled_shares": 2.0,
        "primary_filled_cost_usd": 1.02,
    }


def test_topup_with_an_unknown_fallback_merges_as_unknown_and_blocks_the_next_cycle(
    tmp_path, exchange, buyer_env
):
    """Review fix 3: the top-up merge returned before the execution passthrough fields and marked an
    unknown fallback leg reconciled, so the merged row read as a partial and was topped up again."""
    row = _brow("tp1", "g1")
    (tmp_path / "auto_buyer_ledger.jsonl").write_text(json.dumps(_partial_row(row)) + "\n")
    exchange.post_script = [exchange.default_post, httpx.ReadTimeout("fallback response lost")]
    first = _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    assert [a["alarm"] for a in first.alarms] == ["fallback_submit_unknown"]
    (merged,) = _ledger(tmp_path)
    assert merged["shares"] == 6.0
    assert merged["fallback_status"] == "submit_unknown"
    assert merged["fallback_reconciled"] is False
    assert merged["fallback_submit_unknown"] is True
    assert "ReadTimeout after sending" in merged["fallback_error"]
    ioc_key = exchange.order_posts()[0]["headers"]["Idempotency-Key"]
    assert merged["idempotency_key"] == ioc_key
    assert merged["fallback_idempotency_key"] == exchange.order_posts()[1]["headers"]["Idempotency-Key"]
    assert merged["order_state"] == "ORDER_STATE_PARTIALLY_FILLED"

    again = _buyer(tmp_path, [row]).evaluate_and_execute([dict(row)])
    assert len(exchange.order_posts()) == 2
    assert again.rejected_dedup == 1


def test_topup_merge_carries_the_execution_fields_of_the_topup(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    row = _brow("tp2", "g1")
    j_path.write_text(json.dumps(_partial_row(row)) + "\n")
    merged = auto_buyer_ledger.record_auto_buy_execution(
        _payload(
            pick_id="tp2",
            market_slug=row["market_slug"],
            is_topup=True,
            shares=4.0,
            cost_usd=2.04,
            limit_price=0.51,
            order_ids=["ioc-new", "gtd-new"],
            fallback_order_id="gtd-new",
            fallback_status="resting",
            fallback_resting_shares=3.0,
            good_till_time="2026-11-25T23:58:30Z",
            intent_id="i-2",
            idempotency_key="mp-i-2-p",
            fallback_idempotency_key="mp-i-2-f",
            requested_shares=7.0,
            requested_cost_usd=3.57,
        ),
        order_id="ioc-new",
        order_state="ORDER_STATE_PARTIALLY_FILLED",
        jsonl_path=j_path,
    )
    assert merged["fallback_order_id"] == "gtd-new"
    assert merged["fallback_status"] == "resting"
    assert merged["fallback_reconciled"] is False
    assert merged["good_till_time"] == "2026-11-25T23:58:30Z"
    assert merged["intent_id"] == "i-2"
    assert merged["idempotency_key"] == "mp-i-2-p"
    assert merged["fallback_idempotency_key"] == "mp-i-2-f"
    assert "fallback_submit_unknown" not in merged
