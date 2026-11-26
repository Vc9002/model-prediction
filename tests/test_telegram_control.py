"""Telegram control of the live auto-buyer (operator-approved 2026-11-26: "Full + cancel orders").

Nothing here reaches the exchange: every executor is ``_Exchange`` below, httpx is made to fail loudly, and
the runtime root, state file and ledgers live under ``tmp_path``.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from model_prediction import auto_buyer_cancel_worker, telegram_control
from model_prediction.daily_lock import acquire_lock, release_lock
from model_prediction.data_sources.polymarket_execute import ExecutionGateError
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio import auto_buyer_ledger, auto_executor

SLUG_A = "asc-cbb-csu-pepp-2026-11-25-pos-9pt5"
SLUG_B = "tsc-cbb-aaa-bbb-2026-11-26-141pt5"
SLUG_C = "asc-cbb-ccc-ddd-2026-11-26-neg-3pt5"

LIVE_STATE: dict[str, Any] = {
    "enabled": True,
    "live_enabled": True,
    "mode": "paper",
    "unit_value_usd": 5.0,
    "max_daily_spend_usd": 500.0,
    "max_game_stake_usd": 25.0,
    "max_daily_loss_usd": 250.0,
    "cohorts": [
        {
            "model_id": "ncaab-spread-v7",
            "sport": "ncaab",
            "market": "spread",
            "min_saved_edge": 0.05,
            "max_saved_edge_exclusive": 0.12,
            "mode": "live",
            "enabled": True,
        }
    ],
    "_state_version": 7,
}


def _order(
    oid: str,
    slug: str = SLUG_A,
    *,
    intent: str = "ORDER_INTENT_BUY_LONG",
    price: str = "0.52",
    quantity: float = 10.0,
    age_minutes: float = 14.0,
) -> dict[str, Any]:
    return {
        "id": oid,
        "marketSlug": slug,
        "intent": intent,
        "price": {"value": price, "currency": "USD"},
        "quantity": quantity,
        "tif": "TIME_IN_FORCE_GOOD_TILL_DATE",
        "createTime": iso_utc(utc_now() - timedelta(minutes=age_minutes)),
        "state": "ORDER_STATE_NEW",
    }


class _Exchange:
    """Open-order list plus per-id read and cancel. Records every call; never trades."""

    def __init__(self, orders: list[dict[str, Any]] | None = None, *, no_slug_read: bool = True) -> None:
        self.orders = list(orders or [])
        self.no_slug_read = no_slug_read
        self.reads: list[list[str]] = []
        self.cancel_calls: list[tuple[str, str | None]] = []
        self.snapshot_calls: list[list[str]] = []
        self.cancel_error: Exception | None = None
        self.explode = False

    def open_orders(self, slugs: list[str]) -> list[dict[str, Any]]:
        assert not self.explode, "this command must not reach the exchange"
        self.reads.append(list(slugs))
        if not slugs and not self.no_slug_read:
            raise ExecutionGateError("REFUSED by Polymarket US (400): slugs required", status_code=400)
        if not slugs:
            return [dict(o) for o in self.orders]
        return [dict(o) for o in self.orders if o["marketSlug"] in slugs]

    def order_snapshots(self, order_ids: list[str]) -> dict[str, Any]:
        assert not self.explode, "this command must not reach the exchange"
        self.snapshot_calls.append(list(order_ids))
        open_ids = {o["id"] for o in self.orders}
        found = [
            {"order_id": oid, "order_state": "ORDER_STATE_CANCELED", "cum_quantity": 0.0}
            for oid in order_ids
            if oid not in open_ids
        ]
        found += [
            {"order_id": oid, "order_state": "ORDER_STATE_NEW", "cum_quantity": 0.0}
            for oid in order_ids
            if oid in open_ids
        ]
        return {"status": "live", "orders": found, "unavailable_order_ids": [], "not_found_order_ids": []}

    def cancel(
        self,
        order_id: str,
        user_command: bool,
        market_slug: str | None = None,
        stop_before: Any = None,
    ) -> dict[str, Any]:
        assert not self.explode, "this command must not reach the exchange"
        assert user_command is True
        self.cancel_calls.append((order_id, market_slug))
        if self.cancel_error is not None:
            raise self.cancel_error
        self.orders = [o for o in self.orders if o["id"] != order_id]
        return {"status": "cancelled", "order_id": order_id}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("tests must never reach the network")

    monkeypatch.setattr(httpx, "request", _boom)
    monkeypatch.setattr(httpx, "get", _boom)
    monkeypatch.setattr(httpx, "post", _boom)
    monkeypatch.setattr(httpx.Client, "request", _boom)


@pytest.fixture
def env(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(root))
    monkeypatch.setattr(auto_executor, "DATA", root.resolve())
    monkeypatch.setattr(auto_executor, "AUTO_BUYER_STATE_FILE", root.resolve() / "auto_buyer_state.json")
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", root.resolve() / "paper_auto_buyer")
    monkeypatch.setattr(auto_buyer_ledger, "log_auto_buyer_event", lambda *a, **k: None)
    exchange = _Exchange()
    # The toggles cancel the ledger's resting fallbacks through the ledger's own default executor.
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", lambda _root: exchange)
    monkeypatch.setattr(telegram_control, "_build_executor", lambda _paths: exchange)
    monkeypatch.setattr(telegram_control, "CANCEL_LOCK_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(telegram_control, "TOGGLE_RETRY_DELAY_SECONDS", 0.0)
    # main() ends the process when a kill-switch sweep thread is still running; a test must never inherit one.
    monkeypatch.setattr(telegram_control, "_ABANDONED", [])
    return type("Env", (), {"root": root.resolve(), "exchange": exchange})()


def _write_state(env, state: dict[str, Any]) -> Path:
    path = env.root / "auto_buyer_state.json"
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def _state(env) -> dict[str, Any]:
    return json.loads((env.root / "auto_buyer_state.json").read_text(encoding="utf-8"))


def _write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(order_id: str, *, fallback: str | None = None, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "pick_id": f"pick-{order_id}",
        "order_id": order_id,
        "market_slug": SLUG_A,
        "away_team": "Cal State Bakersfield Roadrunners",
        "home_team": "Pepperdine Waves",
        "selection": "away",
        "market_type": "spread",
        "line": 9.5,
        "token_side": "long",
        "event_start_utc": iso_utc(utc_now() + timedelta(hours=2)),
        "executed_at_utc": iso_utc(utc_now() - timedelta(minutes=20)),
        "cost_usd": 5.0,
        "status": "open",
        "result": "open",
        "fallback_order_id": fallback,
        "fallback_reconciled": False,
    }
    row.update(extra)
    return row


def _run(command: str, *args: str) -> dict[str, Any]:
    return telegram_control.run_command(command, list(args))


def _audit(env) -> list[dict[str, Any]]:
    path = env.root / telegram_control.AUDIT_FILE_NAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ status


def test_status_reports_switches_caps_spend_and_ledger_known_resting(env):
    _write_state(env, LIVE_STATE)
    live_row = _row("ioc-1", fallback="gtd-1", cost_usd=12.0, executed_at_utc=iso_utc(utc_now()))
    _write_ledger(env.root / "auto_buyer_ledger.jsonl", [live_row])
    paper_row = _row("p-1", cost_usd=7.0, executed_at_utc=iso_utc(utc_now()))
    _write_ledger(env.root / "paper_auto_buyer" / "auto_buyer_ledger.jsonl", [paper_row])
    result = _run("status")
    assert result["ok"] is True
    assert result["enabled"] is True and result["live_enabled"] is True
    assert result["live_trading_active"] is True
    assert result["caps"] == {"daily_spend_usd": 500.0, "per_game_usd": 25.0, "daily_loss_usd": 250.0}
    assert result["live_spend_today_usd"] >= 12.0
    assert result["paper_spend_today_usd"] == 7.0
    assert result["resting_live_orders_ledger"] == 1
    text = result["message"]
    for needle in ("$500", "$25", "$250", "ENABLED", "ledger"):
        assert needle in text


def test_status_with_live_off_says_not_active(env):
    _write_state(env, {**LIVE_STATE, "live_enabled": False})
    result = _run("status")
    assert result["ok"] is True and result["live_trading_active"] is False
    assert "OFF" in result["message"]


def test_status_is_read_only_and_network_free(env):
    state_path = _write_state(env, LIVE_STATE)
    ledger = env.root / "auto_buyer_ledger.jsonl"
    _write_ledger(ledger, [_row("ioc-1", fallback="gtd-1")])
    before = (state_path.read_bytes(), ledger.read_bytes(), sorted(p.name for p in env.root.iterdir()))
    env.exchange.explode = True
    result = _run("status")
    assert result["ok"] is True
    assert (
        state_path.read_bytes(),
        ledger.read_bytes(),
        sorted(p.name for p in env.root.iterdir()),
    ) == before
    assert _audit(env) == []


def test_status_with_an_unreadable_state_file_fails_closed(env):
    (env.root / "auto_buyer_state.json").write_text("{not json", encoding="utf-8")
    result = _run("status")
    assert result["ok"] is False


# ------------------------------------------------------------------ switches


def test_liveoff_turns_only_real_money_off_and_cancels_ledger_resting_by_id(env):
    _write_state(env, LIVE_STATE)
    _write_ledger(env.root / "auto_buyer_ledger.jsonl", [_row("ioc-1", fallback="gtd-1")])
    env.exchange.orders = [_order("gtd-1")]
    result = _run("live-off")
    assert result["ok"] is True
    assert _state(env)["live_enabled"] is False and _state(env)["enabled"] is True
    assert [c[0] for c in env.exchange.cancel_calls] == ["gtd-1"]
    assert "Paper" in result["message"]


def test_liveoff_succeeds_even_when_the_exchange_cancel_fails(env):
    _write_state(env, LIVE_STATE)
    _write_ledger(env.root / "auto_buyer_ledger.jsonl", [_row("ioc-1", fallback="gtd-1")])
    env.exchange.orders = [_order("gtd-1")]
    env.exchange.cancel_error = ExecutionGateError("REFUSED: exchange down", status_code=503)
    result = _run("live-off")
    assert result["ok"] is True
    assert _state(env)["live_enabled"] is False
    assert "1 NOT cancelled" in result["message"]
    assert result["cancel_failed"] is True


def test_liveoff_replies_while_a_slow_cancel_sweep_is_still_running(env, monkeypatch):
    """The switch is saved first; a sweep that outlives the wait is reported as pending, not waited for."""
    _write_state(env, LIVE_STATE)
    _write_ledger(env.root / "auto_buyer_ledger.jsonl", [_row("ioc-1", fallback="gtd-1")])
    env.exchange.orders = [_order("gtd-1")]
    release = threading.Event()
    real_cancel = env.exchange.cancel

    def _slow_cancel(*args, **kwargs):
        release.wait(5)
        return real_cancel(*args, **kwargs)

    env.exchange.cancel = _slow_cancel
    monkeypatch.setattr(telegram_control, "SWEEP_WAIT_SECONDS", 0.3)
    try:
        result = _run("live-off")
    finally:
        release.set()
    assert result["ok"] is True
    assert _state(env)["live_enabled"] is False
    assert "still running" in result["message"] and "/orders" in result["message"]


def test_a_kill_switch_that_cannot_be_written_is_not_reported_as_done(env, monkeypatch):
    _write_state(env, LIVE_STATE)

    def _hang(_enabled=None, **_kw):
        threading.Event().wait(3)
        raise OSError("never saved")

    monkeypatch.setattr(auto_executor, "toggle_live_trading", _hang)
    monkeypatch.setattr(telegram_control, "SWEEP_WAIT_SECONDS", 0.2)
    result = _run("live-off")
    assert result["ok"] is False and "ALARM" in result["message"]


def test_the_live_switch_audit_line_names_telegram_as_its_source(env):
    _write_state(env, LIVE_STATE)
    assert _run("live-off")["ok"] is True
    lines = [
        json.loads(x)
        for x in (env.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    switch = [x for x in lines if "auto_buyer_live_switch_updated" in json.dumps(x)]
    assert len(switch) == 1
    assert switch[0]["payload"]["source"] == "telegram" and switch[0]["payload"]["live_enabled"] is False


def test_pause_turns_real_money_off_and_then_the_master_switch_off(env, monkeypatch):
    path = _write_state(env, LIVE_STATE)
    seen: list[Any] = []
    real = auto_executor.toggle_auto_buyer

    def _spy(enabled=None):
        seen.append((enabled, json.loads(path.read_text(encoding="utf-8")).get("live_enabled")))
        return real(enabled)

    monkeypatch.setattr(auto_executor, "toggle_auto_buyer", _spy)
    result = _run("pause")
    assert result["ok"] is True
    state = _state(env)
    assert state["enabled"] is False and state["live_enabled"] is False
    assert seen == [(False, False)], "real money must already be off when the master write happens"
    assert "Real money OFF" in result["message"] and "paused" in result["message"].lower()
    after = _run("status")
    assert after["live_enabled"] is False and "Real money switch: OFF" in after["message"]
    # a later master-on (the dashboard, or /resume) can no longer bring real money back by itself
    auto_executor.toggle_auto_buyer(True)
    assert _run("status")["live_trading_active"] is False


def test_pause_from_an_already_live_off_state_skips_the_live_write(env, monkeypatch):
    _write_state(env, {**LIVE_STATE, "live_enabled": False})
    calls: list[Any] = []
    real = auto_executor.toggle_live_trading
    monkeypatch.setattr(
        auto_executor, "toggle_live_trading", lambda *a, **k: calls.append(a) or real(*a, **k)
    )
    assert _run("pause")["ok"] is True
    assert calls == []


def test_resume_turns_paper_on_and_leaves_live_off_even_if_live_was_on(env):
    _write_state(env, {**LIVE_STATE, "enabled": False, "live_enabled": True})
    result = _run("resume")
    assert result["ok"] is True
    state = _state(env)
    assert state["enabled"] is True and state["live_enabled"] is False
    assert result["live_trading_active"] is False
    assert "/liveon" in result["message"]


def test_resume_switches_live_off_before_the_master_comes_on(env, monkeypatch):
    """A crash or a concurrent buyer cycle between the two writes must never see live ON with the
    master ON: the file already reads live_enabled false when the master write happens."""
    path = _write_state(env, {**LIVE_STATE, "enabled": False, "live_enabled": True})
    seen: list[Any] = []
    real = auto_executor.toggle_auto_buyer

    def _spy(enabled=None):
        seen.append((enabled, json.loads(path.read_text(encoding="utf-8")).get("live_enabled")))
        return real(enabled)

    monkeypatch.setattr(auto_executor, "toggle_auto_buyer", _spy)
    assert _run("resume")["ok"] is True
    assert seen == [(True, False)]


def test_resume_does_not_enable_the_master_when_the_live_off_write_fails(env, monkeypatch):
    path = _write_state(env, {**LIVE_STATE, "enabled": False, "live_enabled": True})

    def _fail(_enabled=None, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(auto_executor, "toggle_live_trading", _fail)
    result = _run("resume")
    assert result["ok"] is False
    assert json.loads(path.read_text(encoding="utf-8"))["enabled"] is False


# ------------------------------------------------------------------ live-on


def test_liveon_without_confirmed_is_refused_and_changes_nothing(env):
    path = _write_state(env, {**LIVE_STATE, "live_enabled": False})
    before = path.read_bytes()
    result = _run("live-on")
    assert result["ok"] is False and "--confirmed" in result["message"]
    assert path.read_bytes() == before


def test_liveon_refuses_when_the_master_switch_is_off(env):
    path = _write_state(env, {**LIVE_STATE, "enabled": False, "live_enabled": False})
    before = path.read_bytes()
    result = _run("live-on", "--confirmed")
    assert result["ok"] is False and "/resume" in result["message"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("master", ["true", 1, None])
def test_liveon_master_check_is_strict_true(env, master):
    state = {**LIVE_STATE, "live_enabled": False}
    state["enabled"] = master
    path = _write_state(env, state)
    before = path.read_bytes()
    assert _run("live-on", "--confirmed")["ok"] is False
    assert path.read_bytes() == before


def test_liveon_refuses_derived_dollar_caps(env):
    state = {k: v for k, v in LIVE_STATE.items() if k != "max_daily_loss_usd"}
    path = _write_state(env, {**state, "live_enabled": False})
    before = path.read_bytes()
    result = _run("live-on", "--confirmed")
    assert result["ok"] is False and "max_daily_loss_usd" in result["message"]
    assert path.read_bytes() == before


def test_liveon_confirmed_turns_live_on_restates_caps_and_warns_unverified_fallback(env):
    _write_state(env, {**LIVE_STATE, "live_enabled": False})
    _write_ledger(env.root / "auto_buyer_ledger.jsonl", [_row("ioc-1")])
    result = _run("live-on", "--confirmed")
    assert result["ok"] is True
    assert _state(env)["live_enabled"] is True
    assert result["live_trading_active"] is True
    for needle in ("$500", "$25", "$250", "ncaab-spread-v7"):
        assert needle in result["message"]
    assert "not yet been verified" in result["message"]
    assert result["fallback_verified"] is False


def test_liveon_does_not_warn_once_a_row_carries_a_verified_fallback(env):
    _write_state(env, {**LIVE_STATE, "live_enabled": False})
    _write_ledger(
        env.root / "auto_buyer_ledger.jsonl",
        [_row("ioc-1", fallback="gtd-1", fallback_verified_via="order_readback")],
    )
    result = _run("live-on", "--confirmed")
    assert result["ok"] is True and result["fallback_verified"] is True
    assert "not yet been verified" not in result["message"]


def test_liveon_is_refused_when_the_audit_line_cannot_be_written(env, monkeypatch):
    path = _write_state(env, {**LIVE_STATE, "live_enabled": False})
    before = path.read_bytes()

    def _deny(*_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(telegram_control, "_append_audit", _deny)
    result = _run("live-on", "--confirmed")
    assert result["ok"] is False and "audit" in result["message"].lower()
    assert path.read_bytes() == before


@pytest.mark.parametrize("command", ["live-off", "pause", "resume", "cancel"])
def test_risk_reducing_commands_proceed_when_the_audit_line_cannot_be_written(env, monkeypatch, command):
    _write_state(env, {**LIVE_STATE, "enabled": command == "resume"})
    env.exchange.orders = [_order("ORD1ABCDEF")]
    monkeypatch.setattr(telegram_control, "_append_audit", lambda *_a, **_k: False)
    args = ["ORD1ABCDEF"] if command == "cancel" else []
    assert _run(command, *args)["ok"] is True


# ------------------------------------------------------------------ orders


def test_orders_lists_numbered_cents_and_marks_ledger_known_vs_manual(env):
    _write_state(env, LIVE_STATE)
    _write_ledger(env.root / "auto_buyer_ledger.jsonl", [_row("ioc-9", fallback="DYR1NJ7G15P7")])
    env.exchange.orders = [
        _order("DYR1NJ7G15P7", SLUG_A, age_minutes=14),
        _order(
            "DYR1NJ7G15QX", SLUG_B, intent="ORDER_INTENT_BUY_SHORT", price="0.40", quantity=5.0, age_minutes=3
        ),
    ]
    result = _run("orders")
    assert result["ok"] is True and result["scope"] == "account"
    by_id = {o["id"]: o for o in result["orders"]}
    known, manual = by_id["DYR1NJ7G15P7"], by_id["DYR1NJ7G15QX"]
    assert known["known"] is True and manual["known"] is False
    # a short intent is quoted in the long coordinate: 0.40 long is 60 cents for the short side
    assert known["price_cents"] == 52.0 and manual["price_cents"] == 60.0
    assert [o["n"] for o in result["orders"]] == [1, 2]
    assert all(o["text"].startswith(f"{o['n']}. {o['short_id']} ") for o in result["orders"])
    assert result["header"].startswith("Open live orders: 2") and result["footer"].startswith("/cancel")
    text = result["message"]
    assert "52¢" in text and "60¢" in text and "manual" in text
    assert re.search(r"(?<![\w.])[+-]\d{3}\b", text) is None
    assert env.exchange.cancel_calls == []
    assert _audit(env) == []


def test_orders_short_ids_are_unique_prefixes_of_at_least_four_characters(env):
    _write_state(env, LIVE_STATE)
    env.exchange.orders = [_order("DYR1NJ7G15P7"), _order("DYR1NJ7G15QX"), _order("ZZ9TOPSECRET")]
    result = _run("orders")
    shorts = {o["id"]: o["short_id"] for o in result["orders"]}
    assert all(len(s) >= telegram_control.MIN_PREFIX for s in shorts.values())
    for full, short in shorts.items():
        assert full.startswith(short)
        assert [i for i in shorts if i.startswith(short)] == [full]


def test_orders_unions_the_ledger_markets_when_the_account_read_misses_an_order(env):
    _write_state(env, LIVE_STATE)
    _write_ledger(env.root / "auto_buyer_ledger.jsonl", [_row("ioc-9", fallback="GTD9ABCDEFGH")])
    env.exchange.orders = [_order("GTD9ABCDEFGH", SLUG_A)]
    seen_all = env.exchange.open_orders

    def _empty_account_read(slugs):
        return [] if not slugs else seen_all(slugs)

    env.exchange.open_orders = _empty_account_read  # an endpoint that silently returns nothing without slugs
    result = _run("orders")
    assert [o["id"] for o in result["orders"]] == ["GTD9ABCDEFGH"]
    # an order only the ledger-market read found proves the account-wide read was incomplete
    assert result["scope"] == "partial" and "incomplete" in result["note"]
    assert "(partial read)" in result["message"]


def test_orders_reports_a_partial_scope_when_the_account_read_is_refused(env):
    _write_state(env, LIVE_STATE)
    _write_ledger(env.root / "auto_buyer_ledger.jsonl", [_row("ioc-9", fallback="GTD9ABCDEFGH")])
    env.exchange.no_slug_read = False
    env.exchange.orders = [_order("GTD9ABCDEFGH", SLUG_A), _order("MANUAL000001", SLUG_B)]
    result = _run("orders")
    assert result["ok"] is True and result["scope"] == "partial"
    assert [o["id"] for o in result["orders"]] == ["GTD9ABCDEFGH"]
    assert "partial" in result["message"].lower()


def test_orders_exchange_failure_is_reported_not_raised(env):
    _write_state(env, LIVE_STATE)

    def _down(_slugs):
        raise ExecutionGateError("REFUSED: Polymarket US order request failed (ConnectError).")

    env.exchange.open_orders = _down
    result = _run("orders")
    assert result["ok"] is False and "ConnectError" in result["message"]


# ------------------------------------------------------------------ cancel


def _three_orders(env) -> None:
    env.exchange.orders = [
        _order("DYR1NJ7G15P7", SLUG_A),
        _order("DYR1NJ7G15QX", SLUG_B),
        _order("EXY4MKLP9902", SLUG_C),
    ]


def test_cancel_exact_id_cancels_exactly_that_one_order(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    result = _run("cancel", "DYR1NJ7G15QX")
    assert result["ok"] is True
    assert env.exchange.cancel_calls == [("DYR1NJ7G15QX", SLUG_B)]
    assert {o["id"] for o in env.exchange.orders} == {"DYR1NJ7G15P7", "EXY4MKLP9902"}
    assert "Cancelled DYR1NJ7G15QX" in result["message"] and "ORDER_STATE_CANCELED" in result["message"]
    assert result["outcome"] == "cancelled"


def test_cancel_never_cancels_more_than_one_order_whatever_the_id(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    for ident in ("EXY4", "dyr1nj7g15qx", "DYR1NJ7G15P7"):
        before = len(env.exchange.cancel_calls)
        _run("cancel", ident)
        assert len(env.exchange.cancel_calls) - before == 1
    assert env.exchange.orders == []


def test_cancel_accepts_a_unique_prefix(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    result = _run("cancel", "EXY4")
    assert result["ok"] is True
    assert [c[0] for c in env.exchange.cancel_calls] == ["EXY4MKLP9902"]


def test_cancel_refuses_an_ambiguous_prefix_and_cancels_nothing(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    result = _run("cancel", "DYR1")
    assert result["ok"] is False and "ambiguous" in result["message"].lower()
    assert env.exchange.cancel_calls == [] and len(env.exchange.orders) == 3


def test_cancel_refuses_an_unknown_id(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    result = _run("cancel", "NOSUCHORDER")
    assert result["ok"] is False and "no open order" in result["message"].lower()
    assert env.exchange.cancel_calls == []


@pytest.mark.parametrize("ident", ["", "D", "DYR", "*", "all", "DYR1NJ7G15P7 EXY4MKLP9902", "DY;R1", "../x"])
def test_cancel_refuses_short_wildcard_and_malformed_ids(env, ident):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    result = _run("cancel", ident)
    assert result["ok"] is False
    assert env.exchange.cancel_calls == []


@pytest.mark.parametrize("ident", ["D", "DY", "DYR"])
def test_cancel_refuses_a_short_prefix_even_when_it_matches_the_only_open_order(env, ident):
    _write_state(env, LIVE_STATE)
    env.exchange.orders = [_order("DYR1NJ7G15P7")]
    result = _run("cancel", ident)
    assert result["ok"] is False and env.exchange.cancel_calls == []


def test_cancel_refetches_open_orders_at_cancel_time(env):
    """An id that listed a minute ago but is no longer open (filled, expired) must not be cancelled."""
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    env.exchange.orders = [o for o in env.exchange.orders if o["id"] != "EXY4MKLP9902"]
    result = _run("cancel", "EXY4MKLP9902")
    assert result["ok"] is False
    assert env.exchange.cancel_calls == []
    assert env.exchange.reads, "the open orders must be read at cancel time"


def test_cancel_with_a_partial_account_read_needs_the_exact_full_id(env):
    _write_state(env, LIVE_STATE)
    _write_ledger(env.root / "auto_buyer_ledger.jsonl", [_row("ioc-9", fallback="GTD9ABCDEFGH")])
    env.exchange.no_slug_read = False
    env.exchange.orders = [_order("GTD9ABCDEFGH", SLUG_A)]
    refused = _run("cancel", "GTD9")
    assert refused["ok"] is False and env.exchange.cancel_calls == []
    assert _run("cancel", "GTD9ABCDEFGH")["ok"] is True
    assert [c[0] for c in env.exchange.cancel_calls] == ["GTD9ABCDEFGH"]


def test_cancel_records_the_id_in_the_cancel_worker_memo_only_when_it_reads_terminal(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    assert _run("cancel", "EXY4MKLP9902")["ok"] is True
    memo = json.loads((env.root / auto_buyer_cancel_worker.MEMO_FILE_NAME).read_text(encoding="utf-8"))
    assert "EXY4MKLP9902" in memo["order_ids"]


def test_cancel_that_leaves_the_order_resting_is_not_memoised_and_is_reported(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    order = env.exchange.orders[2]

    def _noop_cancel(order_id, user_command, market_slug=None, stop_before=None):
        env.exchange.cancel_calls.append((order_id, market_slug))
        return {"status": "cancelled", "order_id": order_id}  # the exchange still lists it

    env.exchange.cancel = _noop_cancel
    result = _run("cancel", order["id"])
    assert result["ok"] is False and "still" in result["message"].lower()
    assert not (env.root / auto_buyer_cancel_worker.MEMO_FILE_NAME).exists()


def test_cancel_failure_is_reported_and_not_retried(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    env.exchange.cancel_error = ExecutionGateError("REFUSED by Polymarket US (500): boom", status_code=500)
    result = _run("cancel", "EXY4MKLP9902")
    assert result["ok"] is False
    assert len(env.exchange.cancel_calls) == 1


def test_cancel_takes_the_cancel_worker_lock_and_refuses_while_it_is_held(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    lock = acquire_lock(env.root / "locks" / auto_buyer_cancel_worker.LOCK_FILE_NAME)
    assert lock is not None
    try:
        result = _run("cancel", "EXY4MKLP9902")
    finally:
        release_lock(lock)
    assert result["ok"] is False and "retry" in result["message"].lower()
    assert env.exchange.cancel_calls == []
    # and the lock is released again after a cancel
    assert _run("cancel", "EXY4MKLP9902")["ok"] is True
    again = acquire_lock(env.root / "locks" / auto_buyer_cancel_worker.LOCK_FILE_NAME)
    assert again is not None
    release_lock(again)


# ------------------------------------------------------------------ audit and CLI


def test_every_mutating_command_appends_audit_lines_and_read_only_ones_do_not(env):
    _write_state(env, {**LIVE_STATE, "live_enabled": False})
    env.exchange.orders = [_order("DYR1NJ7G15P7")]
    _run("status")
    _run("orders")
    assert _audit(env) == []
    for command, args in (
        ("pause", []),
        ("resume", []),
        ("live-on", ["--confirmed"]),
        ("live-off", []),
        ("cancel", ["DYR1NJ7G15P7"]),
        ("live-on", []),
    ):
        before = len(_audit(env))
        _run(command, *args)
        lines = _audit(env)[before:]
        assert lines, command
        assert {line["command"] for line in lines} == {command}
        assert all(line["source"] == "telegram" and line["utc"] and "args" in line for line in lines)
        assert lines[-1]["phase"] == "finished" and "ok" in lines[-1]["result"]


def test_main_prints_one_json_object_and_maps_ok_to_the_exit_code(env, capsys):
    _write_state(env, LIVE_STATE)
    assert telegram_control.main(["status"]) == 0
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1 and json.loads(out)["ok"] is True
    assert telegram_control.main(["live-on"]) == 1
    refused = json.loads(capsys.readouterr().out)
    assert refused["ok"] is False and "message" in refused


@pytest.mark.parametrize("argv", [[], ["bogus"], ["cancel"], ["cancel", "A", "B"], ["status", "--confirmed"]])
def test_main_rejects_bad_usage_with_json_and_exit_1(env, capsys, argv):
    assert telegram_control.main(argv) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_main_fails_closed_without_the_runtime_root(env, monkeypatch, capsys):
    monkeypatch.delenv("MODEL_PREDICTION_RUNTIME_ROOT", raising=False)
    assert telegram_control.main(["status"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and "RUNTIME_ROOT" in result["message"]


def test_main_refuses_when_the_imported_buyer_is_bound_to_another_root(env, monkeypatch, tmp_path, capsys):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(other))
    assert telegram_control.main(["pause"]) == 1
    assert "bound" in json.loads(capsys.readouterr().out)["message"]


def test_stdout_stays_one_json_object_even_if_a_dependency_prints(env, monkeypatch, capsys):
    _write_state(env, LIVE_STATE)
    real = auto_executor.auto_buyer_status

    def _chatty():
        print("noise from a dependency")
        return real()

    monkeypatch.setattr(auto_executor, "auto_buyer_status", _chatty)
    assert telegram_control.main(["status"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["ok"] is True


def test_no_message_contains_american_odds_or_secrets(env, monkeypatch):
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "SUPERSECRETVALUE")
    _write_state(env, LIVE_STATE)
    env.exchange.orders = [_order("DYR1NJ7G15P7")]
    blob = json.dumps(
        [_run("status"), _run("orders"), _run("pause"), _run("resume"), _run("live-off"), _run("live-on")]
    )
    assert "SUPERSECRETVALUE" not in blob
    assert re.search(r"(?<![\w.:-])[+-]\d{3}(?!\d)", re.sub(r"\d{4}-\d{2}-\d{2}", "", blob)) is None


# ------------------------------------------------------------------ fix round 1 (critical review 2026-11-26)


def test_resume_skips_the_live_write_when_real_money_is_already_off(env, monkeypatch):
    _write_state(env, {**LIVE_STATE, "enabled": False, "live_enabled": False})
    calls: list[Any] = []
    real = auto_executor.toggle_live_trading
    monkeypatch.setattr(
        auto_executor, "toggle_live_trading", lambda *a, **k: calls.append(a) or real(*a, **k)
    )
    result = _run("resume")
    assert result["ok"] is True and calls == []
    assert _state(env)["enabled"] is True and _state(env)["live_enabled"] is False


def test_cancel_exact_refuses_a_prefix_even_when_it_is_unique(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    result = _run("cancel", "EXY4", "--exact")
    assert result["ok"] is False and "full order id" in result["message"]
    assert env.exchange.cancel_calls == [] and len(env.exchange.orders) == 3


def test_cancel_exact_cancels_the_full_id_only_if_it_is_open_right_now(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    assert _run("cancel", "EXY4MKLP9902", "--exact")["ok"] is True
    assert [c[0] for c in env.exchange.cancel_calls] == ["EXY4MKLP9902"]
    gone = _run("cancel", "EXY4MKLP9902", "--exact")
    assert gone["ok"] is False and len(env.exchange.cancel_calls) == 1


def test_cancel_exact_does_not_hit_a_newer_order_sharing_the_prefix(env):
    """The operator copied DYQRQ2SG15P9 from an older /orders; a newer DYQRQNF015P8 shares the prefix."""
    _write_state(env, LIVE_STATE)
    env.exchange.orders = [_order("DYQRQ2SG15P9"), _order("DYQRQNF015P8", SLUG_B)]
    result = _run("cancel", "DYQRQ", "--exact")
    assert result["ok"] is False and env.exchange.cancel_calls == []
    assert _run("cancel", "DYQRQ2SG15P9", "--exact")["ok"] is True
    assert [o["id"] for o in env.exchange.orders] == ["DYQRQNF015P8"]


@pytest.mark.parametrize("extra", [["--force"], ["--exact", "--exact"], ["x"]])
def test_cancel_rejects_unknown_extra_arguments(env, extra):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    result = _run("cancel", "EXY4MKLP9902", *extra)
    assert result["ok"] is False and env.exchange.cancel_calls == []


def test_cancel_reports_cancelled_for_expired_too(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    real_snapshots = env.exchange.order_snapshots

    def _expired(order_ids):
        out = real_snapshots(order_ids)
        for o in out["orders"]:
            o["order_state"] = "ORDER_STATE_EXPIRED"
        return out

    env.exchange.order_snapshots = _expired
    result = _run("cancel", "EXY4MKLP9902", "--exact")
    assert result["ok"] is True and result["outcome"] == "cancelled"


def test_cancel_with_an_unreadable_followup_is_unconfirmed_and_not_memoised(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)

    def _down(_ids):
        raise ExecutionGateError("REFUSED: Polymarket US order request failed (ConnectError).")

    env.exchange.order_snapshots = _down
    result = _run("cancel", "EXY4MKLP9902", "--exact")
    assert result["ok"] is False and result["outcome"] == "unconfirmed"
    assert "unconfirmed" in result["message"].lower() and "/orders" in result["message"]
    assert not (env.root / auto_buyer_cancel_worker.MEMO_FILE_NAME).exists()


def test_cancel_of_an_order_that_filled_is_not_reported_as_cancelled(env):
    _write_state(env, LIVE_STATE)
    _three_orders(env)
    real_snapshots = env.exchange.order_snapshots

    def _filled(order_ids):
        out = real_snapshots(order_ids)
        for o in out["orders"]:
            o["order_state"] = "ORDER_STATE_FILLED"
        return out

    env.exchange.order_snapshots = _filled
    result = _run("cancel", "EXY4MKLP9902", "--exact")
    assert result["ok"] is False and result["outcome"] == "filled"
    assert "FILLED" in result["message"] and "Cancelled" not in result["message"]


def test_liveon_with_no_live_cohort_says_no_real_order_will_be_placed(env):
    state = {**LIVE_STATE, "live_enabled": False, "cohorts": [{**LIVE_STATE["cohorts"][0], "mode": "paper"}]}
    _write_state(env, state)
    result = _run("live-on", "--confirmed")
    assert result["ok"] is True and result["live_trading_active"] is False
    assert "no real order will be placed" in result["message"]


def test_liveon_in_legacy_live_mode_is_called_out_as_ungoverned(env):
    state = {k: v for k, v in LIVE_STATE.items() if k != "cohorts"}
    _write_state(env, {**state, "mode": "live", "live_enabled": False})
    result = _run("live-on", "--confirmed")
    assert result["ok"] is True and result["live_trading_active"] is True
    assert "legacy live mode" in result["message"] and "ungoverned" in result["message"]
    assert "no real order will be placed" not in result["message"]


def test_the_cli_arms_an_overall_deadline_and_it_prints_one_json_object(env, monkeypatch, capsys):
    armed: list[tuple[float, Any]] = []

    class _Timer:
        def __init__(self, interval, function):
            armed.append((interval, function))
            self.daemon = False

        def start(self):
            pass

        def cancel(self):
            armed.append(("cancelled", None))

    monkeypatch.setattr(telegram_control.threading, "Timer", _Timer)
    _write_state(env, LIVE_STATE)
    assert telegram_control.main(["status"]) == 0
    assert armed[0][0] == telegram_control.CLI_DEADLINE_SECONDS == 150.0
    assert armed[-1][0] == "cancelled"
    capsys.readouterr()
    exits: list[int] = []
    monkeypatch.setattr(telegram_control.os, "_exit", exits.append)
    armed[0][1]()
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "UNKNOWN" in out["message"] and exits == [1]


def test_cancel_and_liveon_messages_have_no_american_odds_or_secrets_even_on_exchange_errors(
    env, monkeypatch
):
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", "SUPERSECRETVALUE")
    _write_state(env, {**LIVE_STATE, "live_enabled": False})
    _three_orders(env)
    ok_cancel = _run("cancel", "EXY4MKLP9902", "--exact")
    env.exchange.cancel_error = ExecutionGateError(
        "REFUSED by Polymarket US (500): upstream", status_code=500
    )
    failed_cancel = _run("cancel", "DYR1NJ7G15P7", "--exact")
    env.exchange.cancel_error = None
    live_on = _run("live-on", "--confirmed")
    env.exchange.open_orders = lambda _s: (_ for _ in ()).throw(
        ExecutionGateError("REFUSED: down (ConnectError).")
    )
    list_down = _run("cancel", "DYR1NJ7G15QX", "--exact")
    results = [ok_cancel, failed_cancel, live_on, list_down]
    assert [r["ok"] for r in results] == [True, False, True, False]
    blob = json.dumps(results)
    assert "SUPERSECRETVALUE" not in blob
    assert re.search(r"(?<![\w.:-])[+-]\d{3}(?!\d)", re.sub(r"\d{4}-\d{2}-\d{2}", "", blob)) is None
