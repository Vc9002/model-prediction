"""Live auto-buyer fixes of 2026-11-25 (operator-approved list, items 1-9).

Nothing here reaches the exchange or ESPN: every executor, quote source and scoreboard is a stub,
and the state file lives under ``tmp_path``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from model_prediction.audit import AuditLog
from model_prediction.calibration_pipeline import ServingCalibration
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio import auto_buyer_ledger, auto_executor
from model_prediction.portfolio.auto_buyer_ledger import (
    reconcile_pending_auto_buyer_fallbacks,
    settle_auto_buyer_ledger,
)
from model_prediction.portfolio.auto_executor import AutoExecutionConfig, AutoPolymarketBuyer, BuyCohort

# buyer_env stubs this to True; the pass-level recheck test puts the real one back.
_REAL_STILL_ENABLED = auto_executor._live_buying_still_enabled

SPREAD_V7 = "ncaab-spread-v7"
TOTAL_V6 = "ncaab-total-v6"
SPREAD_LIVE = BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.12, "live")
TOTAL_LIVE = BuyCohort(TOTAL_V6, "ncaab", "total", 0.05, 0.12, "live")


def _write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _ledger(path: Path) -> list[dict[str, Any]]:
    return auto_buyer_ledger.read_auto_buyer_ledger(jsonl_path=path)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("POLYMARKET_KEY_ID", raising=False)
    monkeypatch.setattr(auto_buyer_ledger, "log_auto_buyer_event", lambda *a, **k: None)
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", _no_default_executor)
    # A recorder call without explicit paths must never write the repo data/ tree.
    monkeypatch.setattr(
        auto_buyer_ledger, "AUTO_BUYER_XLSX_PATH", tmp_path / "default" / "auto_buyer_picks.xlsx"
    )
    monkeypatch.setattr(
        auto_buyer_ledger, "AUTO_BUYER_JSONL_PATH", tmp_path / "default" / "auto_buyer_ledger.jsonl"
    )
    # Never the repo or runtime state file: reconcile reads the global edge window from it.
    monkeypatch.setattr(auto_executor, "AUTO_BUYER_STATE_FILE", tmp_path / "state" / "auto_buyer_state.json")
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.PolymarketUSClient", _NoExchange)


class _NoExchange:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        raise RuntimeError("tests must never reach the exchange")


def _no_default_executor(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("a default (real) executor must not be built in this test")


# ---------------------------------------------------------------- buyer harness (no exchange)


class _BuyExecutor:
    """Stands in for PolymarketExecutor on the buy side. Never trades."""

    executed: ClassVar[list[Any]] = []
    fill_fraction: ClassVar[float] = 1.0

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def portfolio_snapshot(self) -> dict[str, Any]:
        return {"positions": {}}

    def execute(self, ticket: Any, pick_row: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        _BuyExecutor.executed.append((ticket, dict(pick_row)))
        filled = round(ticket.size_shares * _BuyExecutor.fill_fraction, 2)
        return {
            "status": "submitted",
            "order_id": f"sim-{ticket.pick_id}",
            "order_ids": [f"sim-{ticket.pick_id}"],
            "order_state": "ORDER_STATE_FILLED"
            if filled >= ticket.size_shares
            else "ORDER_STATE_PARTIALLY_FILLED",
            "filled_size_shares": filled,
            "estimated_filled_cost_usd": round(filled * ticket.price, 4),
        }


def _identity(_model: str, _market: str, probability: float, **_k: Any) -> ServingCalibration:
    return ServingCalibration(probability, probability, False, "identity", "test_isolation")


@pytest.fixture
def buyer_env(monkeypatch):
    _BuyExecutor.executed = []
    _BuyExecutor.fill_fraction = 1.0
    monkeypatch.setattr(auto_executor, "calibrate_for_serving", _identity)
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", _BuyExecutor)
    monkeypatch.setattr(auto_executor, "_load_pick_input_flags", lambda *_a, **_k: {})
    monkeypatch.setattr(auto_executor, "_read_daily_run_history", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(auto_executor, "_live_buying_still_enabled", lambda: True)
    monkeypatch.setattr(auto_executor.time, "sleep", lambda *_a: None)
    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", _quote_from_row)


def _slug(market: str, event: str, line: str = "3pt5") -> str:
    if market == "total":
        return f"tsc-cbb-{event}-2026-11-25-{line}"
    return f"asc-cbb-{event}-2026-11-25-neg-{line}"


def _pick(
    model_id: str,
    *,
    pick_id: str,
    event: str,
    market: str = "spread",
    model_probability: float = 0.58,
    ask: float = 0.50,
    units: float = 2.0,
    start_minutes: float = 20.0,
) -> dict[str, Any]:
    now = utc_now()
    created = now - timedelta(minutes=1)
    return {
        "pick_id": pick_id,
        "event_id": event,
        "model_version": model_id,
        "league": "ncaab",
        "market_type": market,
        "selection": "over" if market == "total" else "home",
        "status": "open",
        "decision": "CALL",
        "reason_code": "FLAT_CALL",
        "edge": "0.07",
        "line": -3.5 if market == "spread" else 140.5,
        "model_probability": model_probability,
        "model_probability_raw": model_probability,
        "calibration_method": "identity",
        "market_probability": ask,
        "units": units,
        "home_team": f"{event}-home",
        "away_team": f"{event}-away",
        "market_slug": _slug(market, event),
        "token_side": "long",
        "test_ask": ask,
        "created_at_utc": iso_utc(created),
        "event_start_utc": iso_utc(now + timedelta(minutes=start_minutes)),
        "market_quote_provenance": "archived_two_sided_bbo",
        "market_quote_timestamp_valid": "True",
        "market_quote_observed_at_utc": iso_utc(created - timedelta(minutes=1)),
        "market_snapshot_hash": "abc",
        "market_snapshot_archive_path": "archive.jsonl",
    }


def _quote_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "quote": {
            "market_slug": row["market_slug"],
            "side": "long",
            "ask": row.get("test_ask"),
            "fresh": True,
            "age_seconds": 5,
            "market_state": "MARKET_STATE_OPEN",
            # The live tip-time cross-check wants the exchange's own start; it agrees with the row.
            "event_start_utc": row.get("event_start_utc"),
        },
    }


def _live_buyer(tmp_path: Path, **overrides: Any) -> AutoPolymarketBuyer:
    kwargs: dict[str, Any] = {
        "mode": "live",
        "execute_live": True,
        "unit_value_usd": 5.0,
        "max_game_stake_usd": 25.0,
        "max_daily_spend_usd": 500.0,
        "whitelisted_models": (),
        "cohorts": (SPREAD_LIVE, TOTAL_LIVE),
        "edge_scaled_sizing_enabled": False,
        "require_model_timestamp": True,
        "capture_missing_slates": False,
        "dedup_own_ledger_only": True,
    }
    kwargs.update(overrides)
    return AutoPolymarketBuyer(
        config=AutoExecutionConfig(**kwargs), audit=AuditLog(tmp_path / "audit.jsonl"), data_root=tmp_path
    )


# ---------------------------------------------------------------- reconcile harness (no exchange)


class _Exchange:
    """Per-id reads and cancels only. ``states`` maps order id -> (state, cum); ids in
    ``unavailable`` are reported the way ``order_snapshots`` reports a genuine not-found, ids in
    ``flaky`` the way it reports an outage (a 429 or timeout: unavailable but NOT not-found)."""

    def __init__(
        self,
        states: dict[str, tuple[str, float]] | None = None,
        *,
        after_cancel: dict[str, tuple[str, float]] | None = None,
        unavailable: tuple[str, ...] = (),
        flaky: tuple[str, ...] = (),
        cancel_error: bool = False,
    ) -> None:
        self.states = dict(states or {})
        self.after_cancel = dict(after_cancel or {})
        self.unavailable = set(unavailable)
        self.flaky = set(flaky)
        self.cancel_error = cancel_error
        self.cancel_calls: list[str] = []
        self.cancelled: list[str] = []

    def order_snapshots(self, order_ids: list[str]) -> dict[str, Any]:
        orders, missing, not_found = [], [], []
        for oid in order_ids:
            if oid in self.unavailable or oid in self.flaky:
                missing.append(oid)
                if oid in self.unavailable:
                    not_found.append(oid)
                continue
            source = self.after_cancel if oid in self.cancelled and oid in self.after_cancel else self.states
            if oid in source:
                state, cum = source[oid]
                orders.append({"order_id": oid, "order_state": state, "cum_quantity": cum})
        return {
            "status": "live",
            "orders": orders,
            "unavailable_order_ids": missing,
            "not_found_order_ids": not_found,
        }

    def cancel(
        self, order_id: str, user_command: bool, market_slug: str | None = None, stop_before: Any = None
    ) -> dict[str, Any]:
        assert user_command is True
        self.cancel_calls.append(order_id)
        if self.cancel_error:
            raise RuntimeError("cancel refused: order not found")
        self.cancelled.append(order_id)
        return {"status": "cancelled", "order_id": order_id}

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"only per-id order_snapshots and cancel may be called, not {name}")


def _fallback_row(
    fid: str = "gtd-1",
    *,
    start_minutes: float = -5.0,
    primary_shares: float = 0.0,
    resting: float = 10.0,
    **extra: Any,
) -> dict[str, Any]:
    row = {
        "pick_id": f"pick-{fid}",
        "order_id": f"ioc-{fid}",
        "order_ids": [f"ioc-{fid}", fid],
        "market_slug": _slug("spread", f"g-{fid}"),
        "token_side": "long",
        "event_id": f"g-{fid}",
        "selection": "home",
        "market_type": "spread",
        "model_id": SPREAD_V7,
        "model_probability": 0.58,
        "event_start_utc": iso_utc(utc_now() + timedelta(minutes=start_minutes)),
        "executed_at_utc": iso_utc(utc_now() - timedelta(minutes=30)),
        "shares": primary_shares,
        "cost_usd": round(primary_shares * 0.5, 4),
        "primary_filled_shares": primary_shares,
        "primary_filled_cost_usd": round(primary_shares * 0.5, 4),
        "entry_price": 0.5,
        "unit_value_usd": 5.0,
        "status": "open",
        "result": "open",
        "order_state": "ORDER_STATE_PARTIALLY_FILLED" if primary_shares else "ORDER_STATE_CANCELED",
        "fill_known": True,
        "fallback_order_id": fid,
        "fallback_reconciled": False,
        "fallback_resting_shares": resting,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------- 7. same-orientation ESPN rematch


def _no_gateway(monkeypatch):
    from model_prediction.data_sources import polymarket_us

    class _Unresolved:
        def market(self, slug):
            return {"status": "MARKET_STATUS_OPEN"}

    monkeypatch.setattr(polymarket_us, "PolymarketUSClient", _Unresolved)


def _espn_game(event_id, date, away, away_score, home, home_score, completed=True):
    event = {
        "id": event_id,
        "competitions": [
            {
                "status": {"type": {"completed": completed}},
                "competitors": [
                    {"homeAway": "away", "team": {"displayName": away}, "score": away_score},
                    {"homeAway": "home", "team": {"displayName": home}, "score": home_score},
                ],
            }
        ],
    }
    if date is not None:
        event["date"] = date
    return event


def _espn_by_date(boards):
    espn = MagicMock()
    espn.scoreboard.side_effect = lambda league, day: {"events": boards.get(day, [])}
    return espn


def _series_row(**overrides):
    row = {
        "order_id": "LIVE-series-1",
        "sport": "NBA",
        "away_team": "Boston Celtics",
        "home_team": "New York Knicks",
        "event_start_utc": "2026-11-25T00:30:00Z",
        "executed_at_utc": "2026-11-25T00:00:00Z",
        "market_slug": "test-series-total",
        "market_type": "total",
        "line": 220.5,
        "selection": "over",
        "token_side": "long",
        "shares": 10.0,
        "entry_price": 0.50,
        "cost_usd": 5.0,
        "unit_value_usd": 5.0,
        "units": 1.0,
        "status": "open",
        "result": "open",
        "fill_known": True,
    }
    row.update(overrides)
    return row


def test_yesterdays_same_orientation_final_never_settles_todays_game(tmp_path, monkeypatch):
    # Same teams, same home/away, consecutive days: yesterday's final is in the prev-day board and
    # today's game is still in progress. The row must stay open.
    _no_gateway(monkeypatch)
    path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(path, [_series_row()])
    espn = _espn_by_date(
        {
            "20261124": [
                _espn_game("y1", "2026-11-24T00:30Z", "Boston Celtics", "120", "New York Knicks", "118")
            ],
            "20261125": [
                _espn_game(
                    "t1",
                    "2026-11-25T00:30Z",
                    "Boston Celtics",
                    "50",
                    "New York Knicks",
                    "48",
                    completed=False,
                )
            ],
        }
    )
    res = settle_auto_buyer_ledger(data_root=tmp_path, espn=espn)
    assert res["newly_settled"] == 0
    assert _ledger(path)[0]["status"] == "open"


def test_exact_match_without_an_espn_date_still_settles(tmp_path, monkeypatch):
    _no_gateway(monkeypatch)
    path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(path, [_series_row()])
    espn = _espn_by_date(
        {"20261125": [_espn_game("t1", None, "Boston Celtics", "120", "New York Knicks", "118")]}
    )
    res = settle_auto_buyer_ledger(data_root=tmp_path, espn=espn)
    assert res["newly_settled"] == 1
    assert res["changes"][0]["to_result"] == "win"


def test_dated_exact_match_is_preferred_over_an_undated_one(tmp_path, monkeypatch):
    _no_gateway(monkeypatch)
    path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(path, [_series_row()])
    espn = _espn_by_date(
        {
            "20261124": [_espn_game("u1", None, "Boston Celtics", "100", "New York Knicks", "100")],
            "20261125": [
                _espn_game("t1", "2026-11-25T00:30Z", "Boston Celtics", "120", "New York Knicks", "118")
            ],
        }
    )
    res = settle_auto_buyer_ledger(data_root=tmp_path, espn=espn)
    assert res["newly_settled"] == 1
    settled = _ledger(path)[0]
    assert (settled["away_score"], settled["home_score"]) == (120, 118)


# ---------------------------------------------------------------- 4. resting cancel uses the cohort window


def test_live_cohort_buy_records_its_edge_window_on_the_row(tmp_path, buyer_env):
    _BuyExecutor.fill_fraction = 0.5
    pick = _pick(SPREAD_V7, pick_id="w1", event="gw1", model_probability=0.57, ask=0.50)
    _live_buyer(tmp_path).evaluate_and_execute([pick])
    row = _ledger(tmp_path / "auto_buyer_ledger.jsonl")[0]
    assert row["cohort_min_edge"] == 0.05
    assert row["cohort_max_edge"] == 0.12
    assert row["cohort"] == SPREAD_LIVE.label


def _quote(ask: float):
    return lambda slug: {"market_state": "MARKET_STATE_OPEN", "long": {"ask": ask, "bid": 0.49}}


def test_resting_cohort_fallback_is_cancelled_below_the_cohort_floor(tmp_path):
    # Live ask 0.54 against a 58% model: 4% edge. Above the global 3.5% floor, below the cohort's 5%.
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row(start_minutes=60.0, cohort_min_edge=0.05, cohort_max_edge=0.12)])
    exchange = _Exchange(
        {"gtd-1": ("ORDER_STATE_NEW", 0.0)}, after_cancel={"gtd-1": ("ORDER_STATE_CANCELED", 0.0)}
    )
    reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange, quote_fn=_quote(0.54))
    assert exchange.cancel_calls == ["gtd-1"]
    assert _ledger(j_path)[0]["cancellation_reason"].startswith("edge_degraded")


def test_resting_cohort_fallback_is_cancelled_above_the_cohort_ceiling(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row(start_minutes=60.0, cohort_min_edge=0.05, cohort_max_edge=0.08)])
    exchange = _Exchange(
        {"gtd-1": ("ORDER_STATE_NEW", 0.0)}, after_cancel={"gtd-1": ("ORDER_STATE_CANCELED", 0.0)}
    )
    reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange, quote_fn=_quote(0.49))
    assert exchange.cancel_calls == ["gtd-1"]
    assert _ledger(j_path)[0]["cancellation_reason"].startswith("edge_anomalous")


def test_row_without_a_cohort_window_keeps_the_global_window(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row(start_minutes=60.0)])
    exchange = _Exchange({"gtd-1": ("ORDER_STATE_NEW", 0.0)})
    reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange, quote_fn=_quote(0.54))
    assert exchange.cancel_calls == []


# ---------------------------------------------------------------- 8. top-up merge


def _partial_row(order_id: str, *, shares: float = 6.0, **extra: Any) -> dict[str, Any]:
    row = _fallback_row("unused", start_minutes=60.0, primary_shares=shares)
    row.update(
        {
            "pick_id": "p1",
            "order_id": order_id,
            "order_ids": [order_id],
            "market_slug": _slug("spread", "g1"),
            "event_id": "g1",
            "home_team": "g1-home",
            "away_team": "g1-away",
            "fallback_order_id": None,
            "fallback_reconciled": True,
            "fallback_resting_shares": 0.0,
            "order_state": "ORDER_STATE_PARTIALLY_FILLED",
            "model_units": 2.0,
        }
    )
    row.update(extra)
    return row


def _topup_payload(**extra: Any) -> dict[str, Any]:
    # Defaults describe a top-up whose own IOC fill is unknown (the executor reuses the fallback
    # fields for it: fallback_order_id is the IOC's own id).
    payload = {
        "pick_id": "p1",
        "market_slug": _slug("spread", "g1"),
        "token_side": "long",
        "is_topup": True,
        "topup_of_order_id": "oid-partial",
        "shares": 0.0,
        "cost_usd": 0.0,
        "limit_price": 0.5,
        "unit_value_usd": 5.0,
        "order_ids": ["ioc-topup"],
        "fallback_order_id": "ioc-topup",
        "fallback_status": "unknown_fill",
        "fallback_resting_shares": 4.0,
        "fill_known": False,
        "target_spend": 10.0,
    }
    payload.update(extra)
    return payload


def test_unknown_fill_topup_is_reconciled_as_a_primary_not_cancelled_as_a_resting_leg(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    started = iso_utc(utc_now() - timedelta(minutes=5))
    _write_ledger(j_path, [_partial_row("oid-partial", event_start_utc=started)])
    auto_buyer_ledger.record_auto_buy_execution(
        _topup_payload(), order_id="ioc-topup", order_state="ORDER_STATE_UNKNOWN", jsonl_path=j_path
    )
    # The started game would cancel a resting leg; an IOC whose read is missing is not one.
    exchange = _Exchange(unavailable=("ioc-topup",))
    reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert exchange.cancel_calls == []
    backstop = auto_buyer_ledger.cancel_resting_live_fallbacks(
        data_root=tmp_path, executor=exchange, cancel_all=False, reason="backstop"
    )
    assert backstop["candidates"] == 0
    # Once the IOC reads terminal, its fill is added to the earlier legs, never in place of them.
    exchange = _Exchange({"ioc-topup": ("ORDER_STATE_CANCELED", 3.0)})
    reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    row = _ledger(j_path)[0]
    assert row["shares"] == 9.0
    assert row["fallback_reconciled"] is True and row["fill_known"] is True


def test_topup_merges_into_the_partial_row_the_index_chose(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    dead = _partial_row(
        "oid-dead", shares=0.0, order_state="ORDER_STATE_CANCELED", status="settled", result="void"
    )
    _write_ledger(j_path, [dead, _partial_row("oid-partial")])
    auto_buyer_ledger.record_auto_buy_execution(
        _topup_payload(
            shares=2.0, cost_usd=1.0, fill_known=True, fallback_order_id=None, fallback_status="no_remainder"
        ),
        order_id="ioc-topup",
        order_state="ORDER_STATE_FILLED",
        jsonl_path=j_path,
    )
    rows = _ledger(j_path)
    assert rows[0]["shares"] == 0.0 and rows[0]["order_ids"] == ["oid-dead"]
    assert rows[1]["shares"] == 8.0 and "ioc-topup" in rows[1]["order_ids"]


def test_topup_keeps_the_earliest_send_time_and_flags_the_anchor(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(
        j_path,
        [
            _partial_row(
                "oid-partial",
                submitted_at_utc="2026-11-25T21:40:05Z",
                quote_observed_at_utc="2026-11-25T21:39:50Z",
            )
        ],
    )
    auto_buyer_ledger.record_auto_buy_execution(
        _topup_payload(
            shares=2.0,
            cost_usd=1.0,
            fill_known=True,
            fallback_order_id=None,
            fallback_status="no_remainder",
            submitted_at_utc="2026-11-25T21:55:00Z",
            quote_observed_at_utc="2026-11-25T21:54:40Z",
        ),
        order_id="ioc-topup",
        order_state="ORDER_STATE_FILLED",
        jsonl_path=j_path,
    )
    (row,) = _ledger(j_path)
    # The report anchors CLV on the first leg's send; a later leg must not move it past the first fill.
    assert row["submitted_at_utc"] == "2026-11-25T21:40:05Z"
    assert row["quote_observed_at_utc"] == "2026-11-25T21:39:50Z"
    assert row["anchor_topped_up"] is True


def test_topup_into_a_row_without_a_send_time_takes_the_topups_but_is_still_flagged(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_partial_row("oid-partial")])
    auto_buyer_ledger.record_auto_buy_execution(
        _topup_payload(
            shares=2.0,
            cost_usd=1.0,
            fill_known=True,
            fallback_order_id=None,
            fallback_status="no_remainder",
            submitted_at_utc="2026-11-25T21:55:00Z",
        ),
        order_id="ioc-topup",
        order_state="ORDER_STATE_FILLED",
        jsonl_path=j_path,
    )
    (row,) = _ledger(j_path)
    assert row["submitted_at_utc"] == "2026-11-25T21:55:00Z"
    assert row["anchor_topped_up"] is True


def test_topup_whose_partial_row_is_gone_gets_its_own_row(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_partial_row("oid-other")])
    auto_buyer_ledger.record_auto_buy_execution(
        _topup_payload(shares=2.0, cost_usd=1.0, fill_known=True, fallback_order_id=None),
        order_id="ioc-topup",
        order_state="ORDER_STATE_FILLED",
        jsonl_path=j_path,
    )
    rows = _ledger(j_path)
    assert len(rows) == 2
    assert rows[0]["shares"] == 6.0
    assert rows[1]["order_id"] == "ioc-topup" and rows[1]["shares"] == 2.0


def test_buyer_topup_names_the_partial_row_it_sized_against(tmp_path, buyer_env):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    dead = _partial_row(
        "oid-dead", shares=0.0, order_state="ORDER_STATE_CANCELED", status="cancelled", result="void"
    )
    _write_ledger(j_path, [dead, _partial_row("oid-partial", shares=4.0, cost_usd=2.0)])
    pick = _pick(SPREAD_V7, pick_id="p1", event="g1", model_probability=0.57, ask=0.50, units=2.0)
    _live_buyer(tmp_path).evaluate_and_execute([pick])
    assert len(_BuyExecutor.executed) == 1
    rows = _ledger(j_path)
    assert rows[0]["order_ids"] == ["oid-dead"]
    assert rows[1]["shares"] > 4.0


# ---------------------------------------------------------------- 9. permanently missing fallback


def test_cancel_of_a_permanently_missing_order_is_flagged_once_and_not_resent(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row()])
    exchange = _Exchange(unavailable=("gtd-1",), cancel_error=True)
    first = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert first["cancel_failed"] == ["gtd-1"]
    assert "cancel_abandoned" not in first
    second = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert second["cancel_abandoned"] == ["gtd-1"]
    third = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert exchange.cancel_calls == ["gtd-1", "gtd-1"]
    assert "cancel_abandoned" not in third
    row = _ledger(j_path)[0]
    assert row["cancel_abandoned_at_utc"]
    assert row["fallback_reconciled"] is False and row["status"] == "open"


def test_a_rate_limited_or_timed_out_read_never_abandons_a_row(tmp_path):
    """An outage reads as unavailable too, but only a genuine not-found may stop the cancels."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row()])
    exchange = _Exchange(flaky=("gtd-1",), cancel_error=True)
    for _ in range(4):
        summary = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
        assert "cancel_abandoned" not in summary
    assert exchange.cancel_calls == ["gtd-1"] * 4
    assert "cancel_abandoned_at_utc" not in _ledger(j_path)[0]


def test_an_abandoned_cancel_resumes_when_the_order_reappears(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    stamp = iso_utc(utc_now())
    _write_ledger(
        j_path, [_fallback_row(cancel_abandoned_at_utc=stamp, cancel_abandoned_alarmed_at_utc=stamp)]
    )
    exchange = _Exchange(
        {"gtd-1": ("ORDER_STATE_NEW", 0.0)}, after_cancel={"gtd-1": ("ORDER_STATE_CANCELED", 0.0)}
    )
    reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert exchange.cancel_calls == ["gtd-1"]
    row = _ledger(j_path)[0]
    # The flag and the cycle's marker go together, so a later abandonment alarms afresh.
    assert "cancel_abandoned_at_utc" not in row and "cancel_abandoned_alarmed_at_utc" not in row


def test_a_cancel_that_fails_while_the_order_is_readable_is_never_abandoned(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row()])
    exchange = _Exchange({"gtd-1": ("ORDER_STATE_NEW", 0.0)}, cancel_error=True)
    for _ in range(3):
        summary = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
        assert "cancel_abandoned" not in summary
    assert exchange.cancel_calls == ["gtd-1"] * 3


# ---------------------------------------------------------------- cycle harness (state file in tmp)


LIVE_STATE: dict[str, Any] = {
    "enabled": True,
    "mode": "paper",
    "unit_value_usd": 5.0,
    "max_daily_spend_units": 100.0,
    "max_game_stake_units": 5.0,
    "max_daily_spend_usd": 500.0,
    "max_game_stake_usd": 25.0,
    "max_daily_loss_usd": 250.0,
    "whitelist_models": [],
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
    "_state_version": 7,
}


@pytest.fixture
def cycle(monkeypatch, tmp_path):
    """run_auto_buyer_cycle against a real state file under tmp_path; every pass is recorded."""
    state_file = tmp_path / "state" / "auto_buyer_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(auto_executor, "AUTO_BUYER_STATE_FILE", state_file)
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", tmp_path / "paper_auto_buyer")
    monkeypatch.setattr(auto_executor, "_append_auto_buyer_cycle_history", lambda *_a, **_k: None)
    monkeypatch.setattr(auto_executor, "_read_cohort_candidate_picks", list)
    calls: dict[str, Any] = {"configs": [], "reconcile": [], "state_file": state_file}

    def _evaluate(self, picks=None):
        calls["configs"].append(self.config)
        hook = calls.get("during_evaluate")
        if hook is not None:
            hook()
        return auto_executor.AutoExecutionResult()

    def _reconcile(**kwargs):
        calls["reconcile"].append(kwargs)
        return dict(calls.get("reconcile_result") or {})

    monkeypatch.setattr(auto_executor.AutoPolymarketBuyer, "evaluate_and_execute", _evaluate)
    monkeypatch.setattr(auto_buyer_ledger, "reconcile_pending_auto_buyer_fallbacks", _reconcile)
    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", lambda *_a, **_k: {})
    return calls


def _write_state(calls: dict[str, Any], state: dict[str, Any]) -> Path:
    path = calls["state_file"]
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 1. end-of-cycle save


def test_a_hand_edit_made_during_the_cycle_survives_its_save(cycle):
    path = _write_state(cycle, LIVE_STATE)

    def _operator_turns_it_off():
        # A hand edit: same _state_version, plus a key the loader never writes.
        edited = {**LIVE_STATE, "enabled": False, "operator_note": "off at tip"}
        path.write_text(json.dumps(edited, indent=2), encoding="utf-8")

    cycle["during_evaluate"] = _operator_turns_it_off
    auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["enabled"] is False
    assert on_disk["operator_note"] == "off at tip"
    assert on_disk["cohorts"] == LIVE_STATE["cohorts"]
    assert on_disk["last_daily_date"] == "2026-11-25"
    assert "last_run" in on_disk
    assert on_disk["_state_version"] == 8
    # Only the cycle's own keys were added: no loader defaults, no derived values.
    assert set(on_disk) == {*LIVE_STATE, "operator_note", "last_run", "last_daily_date"}


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_a_malformed_state_file_is_never_overwritten(cycle, content):
    path = cycle["state_file"]
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeError) as raised:
        auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    assert "state_file_unreadable" in str(raised.value)
    assert path.read_text(encoding="utf-8") == content
    assert all(c.execute_live is False for c in cycle["configs"])


def test_a_malformed_state_file_cancels_the_live_books_resting_orders(cycle, monkeypatch):
    monkeypatch.setattr(auto_executor, "_live_book_has_pending_orders", lambda _p: True)
    cycle["state_file"].write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        auto_executor.run_auto_buyer_cycle()
    assert cycle["reconcile"] == [{"cancel_all_resting": True}]


def test_setters_refuse_to_overwrite_a_malformed_state_file(cycle):
    path = cycle["state_file"]
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(auto_executor.AutoBuyerStateUnreadableError):
        auto_executor.set_auto_buyer_mode("paper")
    assert path.read_text(encoding="utf-8") == "{not json"


def test_a_whole_state_save_refuses_to_overwrite_a_malformed_file(cycle):
    path = cycle["state_file"]
    path.write_text("{not json", encoding="utf-8")
    defaults = auto_executor.load_auto_buyer_state()
    with pytest.raises(auto_executor.AutoBuyerStateUnreadableError):
        auto_executor.save_auto_buyer_state(defaults)
    assert path.read_text(encoding="utf-8") == "{not json"


# ---------------------------------------------------------------- 2. hard live caps


def test_changing_1u_is_refused_while_a_live_cohort_is_on(cycle):
    path = _write_state(cycle, LIVE_STATE)
    with pytest.raises(ValueError, match="live cohort"):
        auto_executor.set_auto_buyer_unit_value(50)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert (on_disk["unit_value_usd"], on_disk["max_daily_spend_usd"]) == (5.0, 500.0)


def _live_config(cycle) -> AutoExecutionConfig:
    live = [c for c in cycle["configs"] if c.mode == "live"]
    assert len(live) == 1
    return live[0]


def test_a_hand_edited_1u_never_scales_the_live_caps(cycle):
    _write_state(cycle, {**LIVE_STATE, "unit_value_usd": 50.0})
    auto_executor.run_auto_buyer_cycle()
    config = _live_config(cycle)
    assert config.execute_live is True
    caps = (config.max_daily_spend_usd, config.max_game_stake_usd, config.max_daily_loss_usd)
    assert caps == (500.0, 25.0, 250.0)


def test_a_hand_edited_dollar_cap_applies_and_survives_a_toggle(cycle):
    path = _write_state(cycle, {**LIVE_STATE, "max_daily_spend_usd": 300.0})
    auto_executor.toggle_auto_buyer(True)
    assert json.loads(path.read_text(encoding="utf-8"))["max_daily_spend_usd"] == 300.0
    auto_executor.run_auto_buyer_cycle()
    assert _live_config(cycle).max_daily_spend_usd == 300.0


@pytest.mark.parametrize("missing", ["max_daily_spend_usd", "max_game_stake_usd", "max_daily_loss_usd"])
def test_live_pass_refuses_to_run_without_a_written_dollar_cap(cycle, missing):
    _write_state(cycle, {k: v for k, v in LIVE_STATE.items() if k != missing})
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm, match="live_caps_invalid"):
        auto_executor.run_auto_buyer_cycle()
    assert [c for c in cycle["configs"] if c.mode == "live"] == []


def test_paper_unit_change_still_scales_the_unit_caps_without_a_live_cohort(cycle):
    path = _write_state(cycle, {**LIVE_STATE, "cohorts": []})
    auto_executor.set_auto_buyer_unit_value(10)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert (on_disk["max_daily_spend_usd"], on_disk["max_game_stake_usd"]) == (1000.0, 50.0)


# ---------------------------------------------------------------- 9. the cycle alarms once


class _UnreadableFillExchange:
    """Every order is terminal on the exchange, with a cumQuantity that cannot be read."""

    def order_snapshots(self, order_ids: list[str]) -> dict[str, Any]:
        orders = [
            {"order_id": oid, "order_state": "ORDER_STATE_CANCELED", "cum_quantity": "abc"}
            for oid in order_ids
        ]
        return {"status": "live", "orders": orders, "unavailable_order_ids": []}


def _unknown_fill_row() -> dict[str, Any]:
    return _fallback_row("uk", order_id="uk", order_ids=["uk"], fill_known=False)


def _alarm_kinds(raised: Any) -> list[str]:
    return [a["alarm"] for a in raised.value.alarms]


def test_a_newly_abandoned_cancel_raises_a_live_alarm_once(cycle, monkeypatch):
    """The ONCE is the cycle's own marker on the row (cancel_abandoned_alarmed_at_utc), set and persisted
    before the raise, so a reconcile run by another caller cannot consume it."""
    monkeypatch.setattr(auto_executor, "_live_book_has_pending_orders", lambda _p: True)
    _write_state(cycle, {**LIVE_STATE, "enabled": False})
    j_path = cycle["state_file"].parent.parent / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row("gtd-9", cancel_abandoned_at_utc=iso_utc(utc_now()))])
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert _alarm_kinds(raised) == ["fallback_cancel_abandoned"]
    assert raised.value.alarms[0]["order_ids"] == ["gtd-9"]
    assert _ledger(j_path)[0]["cancel_abandoned_alarmed_at_utc"]  # persisted before the raise
    auto_executor.run_auto_buyer_cycle()  # the second cycle is quiet


def test_a_summary_alone_never_alarms_the_ledger_marker_decides(cycle, monkeypatch):
    monkeypatch.setattr(auto_executor, "_live_book_has_pending_orders", lambda _p: True)
    _write_state(cycle, {**LIVE_STATE, "enabled": False})
    cycle["reconcile_result"] = {"cancel_abandoned": ["gtd-9"], "fill_unreadable": ["ioc-9"]}
    auto_executor.run_auto_buyer_cycle()


def test_a_newly_unreadable_fill_raises_a_live_alarm_once_naming_the_order(cycle, monkeypatch):
    """The alarm channel is the abandoned cancel's: raised after the cycle's buying, state and history,
    so a row that is merely unreadable never stops a cycle, only fails the worker run to tell the operator."""
    monkeypatch.setattr(auto_executor, "_live_book_has_pending_orders", lambda _p: True)
    _write_state(cycle, {**LIVE_STATE, "enabled": False})
    j_path = cycle["state_file"].parent.parent / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_unknown_fill_row() | {"fill_unreadable_at_utc": iso_utc(utc_now())}])
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert _alarm_kinds(raised) == ["fallback_fill_unreadable"]
    assert raised.value.alarms[0]["order_ids"] == ["uk"]
    assert "uk" in raised.value.alarms[0]["detail"]
    assert _ledger(j_path)[0]["fill_unreadable_alarmed_at_utc"]
    auto_executor.run_auto_buyer_cycle()  # quiet the second time


def test_a_row_stamped_by_a_non_cycle_reconcile_still_alarms_in_the_next_cycle_exactly_once(
    cycle, monkeypatch
):
    """The daily job (steps 11b/12b) and `cli settle` run the same reconcile and throw its summary away. The
    first one to read the stuck IOC stamps the row; the cycle must still alarm, once."""
    monkeypatch.setattr(auto_executor, "_live_book_has_pending_orders", lambda _p: True)
    _write_state(cycle, {**LIVE_STATE, "enabled": False})
    j_path = cycle["state_file"].parent.parent / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_unknown_fill_row()])
    stamped = reconcile_pending_auto_buyer_fallbacks(
        data_root=j_path.parent, executor=_UnreadableFillExchange()
    )
    assert stamped["fill_unreadable"] == ["uk"]  # the daily job's reconcile consumed the summary list
    assert _ledger(j_path)[0]["fill_unreadable_at_utc"]
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert _alarm_kinds(raised) == ["fallback_fill_unreadable"]
    assert raised.value.alarms[0]["order_ids"] == ["uk"]
    auto_executor.run_auto_buyer_cycle()
    # A later reconcile that keeps reading it unreadable neither re-stamps nor re-alarms.
    reconcile_pending_auto_buyer_fallbacks(data_root=j_path.parent, executor=_UnreadableFillExchange())
    auto_executor.run_auto_buyer_cycle()


def test_a_settlement_failure_does_not_lose_the_unreadable_fill_alarm(cycle, monkeypatch):
    """Reconcile succeeded and stamped the row, then settlement raised a caught type: the summary was
    overwritten with an error dict and the alarm never fired. Separate try blocks keep the summary, and the
    alarm reads the ledger regardless."""
    monkeypatch.setattr(auto_executor, "_live_book_has_pending_orders", lambda _p: True)
    _write_state(cycle, {**LIVE_STATE, "enabled": False})
    j_path = cycle["state_file"].parent.parent / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_unknown_fill_row() | {"fill_unreadable_at_utc": iso_utc(utc_now())}])
    cycle["reconcile_result"] = {"fill_unreadable": ["uk"], "errors": 1}

    def _settle_fails(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise OSError("ledger busy")

    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", _settle_fails)
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert _alarm_kinds(raised) == ["fallback_fill_unreadable"]
    reconciliation = raised.value.run_summary["reconciliation"]
    assert reconciliation["status"] == "error" and "ledger busy" in reconciliation["error"]
    assert reconciliation["fill_unreadable"] == ["uk"] and reconciliation["errors"] == 1


def test_an_alarm_whose_marker_cannot_be_persisted_still_fires_and_repeats(cycle, monkeypatch):
    monkeypatch.setattr(auto_executor, "_live_book_has_pending_orders", lambda _p: True)
    _write_state(cycle, {**LIVE_STATE, "enabled": False})
    j_path = cycle["state_file"].parent.parent / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_unknown_fill_row() | {"fill_unreadable_at_utc": iso_utc(utc_now())}])

    def _no_write(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(auto_buyer_ledger, "_write_jsonl_atomic", _no_write)
    for _ in range(2):  # never dropped for a write error; re-alarms next run
        with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
            auto_executor.run_auto_buyer_cycle()
        assert _alarm_kinds(raised) == ["fallback_fill_unreadable"]


# ---------------------------------------------------------------- 3. daily loss stop


def _settled_row(pnl: float, *, start: str, settled: str, **extra: Any) -> dict[str, Any]:
    row = {
        "pick_id": f"s-{len(start)}-{pnl}",
        "order_id": f"o-{start}-{pnl}",
        "model_id": SPREAD_V7,
        "executed_at_utc": start,
        "event_start_utc": start,
        "settled_at_utc": settled,
        "status": "settled",
        "result": "win" if pnl > 0 else "loss",
        "cost_usd": 10.0,
        "pnl_usd": pnl,
    }
    row.update(extra)
    return row


@pytest.fixture
def frozen_now(monkeypatch):
    # 10:00 ET on 2026-11-26.
    now = auto_executor.parse_utc("2026-11-26T15:00:00Z")
    monkeypatch.setattr(auto_executor, "utc_now", lambda: now)
    return now


def test_loss_stop_counts_gross_losses_so_morning_wins_never_offset_them(tmp_path, frozen_now):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _settled_row(200.0, start="2026-11-26T14:00:00Z", settled="2026-11-26T14:30:00Z"),
            _settled_row(-250.0, start="2026-11-26T14:10:00Z", settled="2026-11-26T14:40:00Z"),
        ],
    )
    assert auto_executor._today_auto_buyer_totals(tmp_path)[1] == -250.0
    # The paper book keeps the net figure.
    assert auto_executor._today_auto_buyer_totals(tmp_path, live_book=False)[1] == -50.0


def test_loss_stop_ignores_manual_rows(tmp_path, frozen_now):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _settled_row(
                -300.0,
                start="2026-11-26T14:00:00Z",
                settled="2026-11-26T14:30:00Z",
                pick_id="MANUAL-asc-cbb-x",
                order_id="MANUAL-asc-cbb-x",
            ),
            _settled_row(-40.0, start="2026-11-26T14:00:00Z", settled="2026-11-26T14:30:00Z", manual=True),
        ],
    )
    assert auto_executor._today_auto_buyer_totals(tmp_path) == (0.0, 0.0)


def test_a_late_game_settled_after_midnight_counts_against_its_own_day(tmp_path, frozen_now):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            # Tip 22:00 ET on 11-25, settled 00:30 ET on 11-26: yesterday's loss, not today's.
            _settled_row(-250.0, start="2026-11-26T03:00:00Z", settled="2026-11-26T05:30:00Z"),
            # No start recorded: the settlement date still counts it, so no loss is ever dropped.
            _settled_row(-20.0, start="", settled="2026-11-26T14:30:00Z", executed_at_utc=""),
        ],
    )
    assert auto_executor._today_auto_buyer_totals(tmp_path)[1] == -20.0


def test_live_book_stops_on_gross_loss_while_the_paper_book_nets(tmp_path, frozen_now, buyer_env):
    rows = [
        _settled_row(200.0, start="2026-11-26T14:00:00Z", settled="2026-11-26T14:30:00Z"),
        _settled_row(-250.0, start="2026-11-26T14:10:00Z", settled="2026-11-26T14:40:00Z"),
    ]
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", rows)
    _write_ledger(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl", rows)
    pick = _pick(SPREAD_V7, pick_id="after-stop", event="gs1")
    live = _live_buyer(tmp_path, max_daily_loss_usd=250.0).evaluate_and_execute([dict(pick)])
    assert live.rejected_daily_loss_breaker == 1
    paper = AutoPolymarketBuyer(
        config=AutoExecutionConfig(mode="paper", max_daily_loss_usd=250.0, whitelisted_models=()),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        data_root=tmp_path,
    ).evaluate_and_execute([dict(pick)])
    assert paper.rejected_daily_loss_breaker == 0


# ---------------------------------------------------------------- 6. settle takes daily.lock


def _post(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from io import BytesIO
    from unittest.mock import Mock

    from model_prediction.dashboard.common import _DASHBOARD_TOKEN
    from model_prediction.dashboard.routes import Handler

    body = json.dumps({"confirm": True, **payload}).encode("utf-8")
    handler = Handler.__new__(Handler)
    handler.path = path
    handler.headers = {"Content-Length": str(len(body)), "X-Dashboard-Token": _DASHBOARD_TOKEN}
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler.do_POST()
    return handler.send_response.call_args.args[0], json.loads(handler.wfile.getvalue().decode("utf-8"))


@pytest.mark.parametrize("mode", ["live", "paper"])
def test_settle_route_refuses_while_another_run_holds_the_writer_lock(tmp_path, monkeypatch, mode):
    from model_prediction import daily_lock
    from model_prediction.dashboard import routes

    lock_path = tmp_path / "locks" / "daily.lock"
    monkeypatch.setattr(routes, "_auto_buyer_run_lock_path", lambda: lock_path)
    settled: list[Any] = []
    monkeypatch.setattr(routes, "settle_auto_buyer_ledger", lambda **kw: settled.append(kw) or {"settled": 0})
    held = daily_lock.acquire_lock(lock_path)
    assert held is not None
    try:
        code, body = _post(f"/api/auto-buyer/settle?mode={mode}", {})
    finally:
        daily_lock.release_lock(held)
    assert code == 409 and body["status"] == "refused"
    assert settled == []
    code, body = _post(f"/api/auto-buyer/settle?mode={mode}", {})
    assert code == 200 and len(settled) == 1


# ---------------------------------------------------------------- 5. fees


FEE_AT_50C = 0.07 * 0.5 * 0.5  # TAKER_FEE_COEFFICIENT x p x (1 - p): 1.75c a share at 50c


def test_modeled_fee_matches_the_live_fills_of_2026_11_25():
    # Exchange position cost minus shares x limit on real rows: Rice 16.69 sh at 49c cost $0.29,
    # Long Beach St 20.02 sh at 49c $0.35, Liberty 11.62 sh at 56c $0.20.
    assert round(auto_buyer_ledger.modeled_taker_fee_usd(16.69, 0.49), 2) == 0.29
    assert round(auto_buyer_ledger.modeled_taker_fee_usd(20.02, 0.49), 2) == 0.35
    assert round(auto_buyer_ledger.modeled_taker_fee_usd(11.62, 0.56), 2) == 0.20


def test_live_fill_records_fee_avg_price_and_net_edge(tmp_path, buyer_env):
    _BuyExecutor.fill_fraction = 0.5
    pick = _pick(SPREAD_V7, pick_id="f1", event="gf1", model_probability=0.57, ask=0.50, units=2.0)
    _live_buyer(tmp_path).evaluate_and_execute([pick])
    row = _ledger(tmp_path / "auto_buyer_ledger.jsonl")[0]
    assert row["fee_usd"] == pytest.approx(row["shares"] * FEE_AT_50C, abs=1e-4)
    assert row["fee_source"] == "modeled_taker_fee"
    assert row["avg_fill_price"] == 0.5
    assert row["cost_incl_fee_usd"] == pytest.approx(row["cost_usd"] + row["fee_usd"], abs=1e-4)
    assert row["net_edge"] == pytest.approx(0.57 - (0.5 + FEE_AT_50C), abs=1e-4)
    # Gating stays on gross edge: the recorded edge is the live-ask edge.
    assert row["edge"] == pytest.approx(0.07, abs=1e-4)


def test_live_game_cap_is_fee_inclusive_and_the_ticket_stays_shares_times_limit(tmp_path, buyer_env):
    pick = _pick(SPREAD_V7, pick_id="cap", event="gcap", model_probability=0.57, ask=0.50, units=5.0)
    _live_buyer(tmp_path).evaluate_and_execute([pick])
    (ticket, _row) = _BuyExecutor.executed[0]
    assert ticket.estimated_cost_usd == pytest.approx(round(ticket.size_shares * ticket.price, 2))
    all_in = ticket.estimated_cost_usd + auto_buyer_ledger.modeled_taker_fee_usd(ticket.size_shares, 0.5)
    assert 24.5 < all_in <= 25.0
    # The next cycle reads the game as full: a total on the same game is refused.
    _BuyExecutor.executed = []
    total = _pick(TOTAL_V6, pick_id="cap-t", event="gcap", market="total", model_probability=0.57)
    later = _live_buyer(tmp_path).evaluate_and_execute([total])
    assert _BuyExecutor.executed == [] and later.rejected_budget == 1


def test_a_live_topup_keeps_the_game_within_its_cap_fee_included(tmp_path, buyer_env):
    # The game-room check leaves out the pick's own filled cost (the top-up math subtracts it), so
    # the top-up's own fee must still fit what is left of $25 after that filled cost and its fee.
    held = _partial_row("oid-partial", shares=25.0, cost_usd=12.5, fee_usd=0.4375, primary_fee_usd=0.4375)
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [held])
    pick = _pick(SPREAD_V7, pick_id="p1", event="g1", model_probability=0.57, ask=0.50, units=5.0)
    _live_buyer(tmp_path).evaluate_and_execute([pick])
    assert len(_BuyExecutor.executed) == 1
    merged = _ledger(tmp_path / "auto_buyer_ledger.jsonl")[0]
    assert merged["shares"] > 25.0
    assert merged["cost_incl_fee_usd"] <= 25.0


def test_live_daily_spend_includes_recorded_fees_but_paper_spend_does_not(tmp_path, frozen_now):
    row = {
        "pick_id": "d1",
        "order_id": "o-d1",
        "executed_at_utc": "2026-11-26T14:00:00Z",
        "event_start_utc": "2026-11-26T20:00:00Z",
        "status": "open",
        "cost_usd": 10.0,
        "fee_usd": 0.35,
        "shares": 20.0,
        "entry_price": 0.5,
    }
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [row])
    assert auto_executor._today_auto_buyer_totals(tmp_path)[0] == 10.35
    assert auto_executor._today_auto_buyer_totals(tmp_path, live_book=False)[0] == 10.0


def test_paper_fill_records_the_same_modeled_fee_and_settles_net_of_it(tmp_path, buyer_env, monkeypatch):
    paper_root = tmp_path / "paper_auto_buyer"
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", paper_root)
    pick = _pick(SPREAD_V7, pick_id="pp1", event="gp1", model_probability=0.57, ask=0.50, units=2.0)
    paper = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            mode="paper",
            unit_value_usd=5.0,
            max_game_stake_usd=25.0,
            max_daily_spend_usd=500.0,
            whitelisted_models=(),
            cohorts=(BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.12, "paper"),),
            edge_scaled_sizing_enabled=False,
            require_model_timestamp=True,
            capture_missing_slates=False,
            dedup_own_ledger_only=True,
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        data_root=tmp_path,
    )
    result = paper.evaluate_and_execute([pick])
    # Paper sizing is unchanged: 2U at $5 is $10 of shares, the fee comes on top.
    assert result.dry_run_orders[0]["cost_usd"] == 10.0
    auto_executor._record_paper_fills(result.dry_run_orders)
    row = _ledger(paper_root / "auto_buyer_ledger.jsonl")[0]
    assert row["fee_usd"] == pytest.approx(20.0 * FEE_AT_50C, abs=1e-4)
    row.update({"event_start_utc": "2026-11-25T00:30:00Z", "home_team": "Boston Celtics"})
    row.update({"away_team": "New York Knicks", "sport": "NBA", "selection": "home", "line": -3.5})
    _write_ledger(paper_root / "auto_buyer_ledger.jsonl", [row])
    _no_gateway(monkeypatch)
    espn = _espn_by_date(
        {
            "20261125": [
                _espn_game("t1", "2026-11-25T00:30Z", "New York Knicks", "100", "Boston Celtics", "110")
            ]
        }
    )
    settle_auto_buyer_ledger(data_root=paper_root, espn=espn)
    settled = _ledger(paper_root / "auto_buyer_ledger.jsonl")[0]
    assert settled["result"] == "win"
    assert settled["pnl_usd"] == pytest.approx(20.0 - 10.0 - 20.0 * FEE_AT_50C, abs=1e-4)


def _settle_row(**extra: Any) -> dict[str, Any]:
    return _series_row(**{"market_slug": "test-fee-total", **extra})


def test_settlement_is_fee_inclusive_for_rows_that_record_a_fee(tmp_path, monkeypatch):
    _no_gateway(monkeypatch)
    path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(
        path,
        [
            _settle_row(order_id="with-fee", fee_usd=0.18, primary_fee_usd=0.18),
            _settle_row(order_id="legacy", market_slug="test-fee-total-legacy"),
        ],
    )
    espn = _espn_by_date(
        {
            "20261125": [
                _espn_game("t1", "2026-11-25T00:30Z", "Boston Celtics", "120", "New York Knicks", "118")
            ]
        }
    )
    settle_auto_buyer_ledger(data_root=tmp_path, espn=espn)
    by_order = {r["order_id"]: r for r in _ledger(path)}
    assert by_order["with-fee"]["pnl_usd"] == pytest.approx(10.0 - 5.0 - 0.18)
    # A row written before fees were recorded is graded exactly as before.
    assert by_order["legacy"]["pnl_usd"] == pytest.approx(5.0)
    assert "fee_usd" not in by_order["legacy"]


class _PortfolioStub:
    def __init__(self, positions: dict[str, Any]) -> None:
        self.positions = positions

    def portfolio_snapshot(self) -> dict[str, Any]:
        return {"status": "live", "positions": self.positions, "activities": []}


def test_open_row_fee_is_trued_up_to_the_exchange_position_cost(tmp_path, monkeypatch):
    _no_gateway(monkeypatch)
    path = tmp_path / "auto_buyer_ledger.jsonl"
    future = iso_utc(utc_now() + timedelta(hours=3))
    row = _settle_row(
        event_start_utc=future,
        shares=16.69,
        entry_price=0.49,
        cost_usd=8.1781,
        model_probability=0.56,
        fee_usd=0.2919,
        primary_fee_usd=0.2919,
        fee_source="modeled_taker_fee",
    )
    _write_ledger(path, [row])
    stub = _PortfolioStub({"test-fee-total": {"netPosition": "-16.69", "cost": {"value": "8.4681"}}})
    settle_auto_buyer_ledger(data_root=tmp_path, espn=_espn_by_date({}), polymarket_executor=stub)
    trued = _ledger(path)[0]
    assert trued["status"] == "open"
    assert trued["fee_usd"] == pytest.approx(0.29)
    assert trued["fee_source"] == "exchange_position_cost"
    assert trued["cost_incl_fee_usd"] == pytest.approx(8.4681)
    assert trued["net_edge"] == pytest.approx(0.56 - 8.4681 / 16.69, abs=1e-4)


def test_a_position_that_aggregates_more_than_the_row_keeps_the_modeled_fee(tmp_path, monkeypatch):
    _no_gateway(monkeypatch)
    path = tmp_path / "auto_buyer_ledger.jsonl"
    future = iso_utc(utc_now() + timedelta(hours=3))
    _write_ledger(path, [_settle_row(event_start_utc=future, fee_usd=0.18, fee_source="modeled_taker_fee")])
    stub = _PortfolioStub({"test-fee-total": {"netPosition": "25", "cost": {"value": "12.9"}}})
    settle_auto_buyer_ledger(data_root=tmp_path, espn=_espn_by_date({}), polymarket_executor=stub)
    assert _ledger(path)[0]["fee_source"] == "modeled_taker_fee"


# ---------------------------------------------------------------- 10. independent live and paper switches
# `enabled` stays the master (false stops live AND paper); `live_enabled` false stops only the live
# book and cancels its resting orders like the kill switch. Absent: live follows `enabled`.

PAPER_TOTAL_COHORT = {
    "model_id": TOTAL_V6,
    "sport": "ncaab",
    "market": "total",
    "min_saved_edge": 0.05,
    "max_saved_edge_exclusive": 0.12,
    "mode": "paper",
    "enabled": True,
}


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"enabled": True}, True),  # no key: live follows the master
        ({"enabled": True, "live_enabled": True}, True),
        ({"enabled": True, "live_enabled": False}, False),
        ({"enabled": False, "live_enabled": True}, False),  # the master wins
        ({"enabled": False}, False),
        ({"enabled": True, "live_enabled": "true"}, False),  # strict is True: a string is off
        ({"enabled": True, "live_enabled": 1}, False),
        ({"enabled": True, "live_enabled": None}, False),
        ({"enabled": "true"}, False),
        ({}, False),
    ],
)
def test_live_switch_is_strict_and_follows_enabled_when_the_key_is_absent(state, expected):
    assert auto_buyer_ledger.live_switch_on(state) is expected


def _paper_configs(cycle) -> list[AutoExecutionConfig]:
    return [c for c in cycle["configs"] if c.mode == "paper" and c.cohorts]


def test_live_off_with_paper_on_runs_no_live_pass_and_keeps_the_paper_pass(cycle, monkeypatch):
    monkeypatch.setattr(auto_executor, "_live_book_has_pending_orders", lambda _p: True)
    _write_state(
        cycle,
        {**LIVE_STATE, "live_enabled": False, "cohorts": [*LIVE_STATE["cohorts"], PAPER_TOTAL_COHORT]},
    )
    summary = auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    assert [c for c in cycle["configs"] if c.mode == "live"] == []
    assert all(c.execute_live is False for c in cycle["configs"])
    assert len(_paper_configs(cycle)) == 1
    assert summary["cohort_passes"]["live"]["mode"] == "LIVE_DISABLED"
    assert "live_enabled" in summary["cohort_passes"]["live"]["skipped"]
    assert summary["cohort_passes"]["paper"]["mode"] == "PAPER_TRADING"
    # The resting live book is cancelled by id exactly like the kill switch.
    assert cycle["reconcile"] == [{"cancel_all_resting": True}]


@pytest.mark.parametrize("extra", [{}, {"live_enabled": True}])
def test_live_on_or_key_absent_keeps_todays_live_behaviour(cycle, extra):
    _write_state(cycle, {**LIVE_STATE, **extra})
    auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    config = _live_config(cycle)
    assert config.execute_live is True


def test_master_off_still_stops_everything_even_with_live_on(cycle):
    _write_state(
        cycle,
        {
            **LIVE_STATE,
            "enabled": False,
            "live_enabled": True,
            "cohorts": [*LIVE_STATE["cohorts"], PAPER_TOTAL_COHORT],
        },
    )
    summary = auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    assert cycle["configs"] and all(c.execute_live is False for c in cycle["configs"])
    assert [c for c in cycle["configs"] if c.mode == "live"] == []
    assert summary["cohort_passes"]["live"]["skipped"] == "enabled is not true"
    assert summary["cohort_passes"]["paper"]["mode"] == "PAPER_DISABLED"


def test_a_live_off_cycle_never_writes_the_live_switch_back(cycle):
    path = _write_state(cycle, {**LIVE_STATE, "live_enabled": False})
    auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    assert json.loads(path.read_text(encoding="utf-8"))["live_enabled"] is False


def _real_cycle_env(tmp_path, monkeypatch, state: dict[str, Any], picks: list[dict[str, Any]]) -> Path:
    """run_auto_buyer_cycle with the real buyer (no stubbed evaluate) on a slate of ``picks``."""
    state_file = tmp_path / "state" / "auto_buyer_state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(auto_executor, "AUTO_BUYER_STATE_FILE", state_file)
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", tmp_path / "paper_auto_buyer")
    monkeypatch.setattr(auto_executor, "_append_auto_buyer_cycle_history", lambda *_a, **_k: None)
    monkeypatch.setattr(auto_executor, "_capture_missing_active_snapshot_slates", lambda *a, **k: [])
    monkeypatch.setattr(auto_buyer_ledger, "reconcile_pending_auto_buyer_fallbacks", lambda **_k: {})
    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", lambda *_a, **_k: {})
    monkeypatch.setattr(auto_executor, "_read_cohort_candidate_picks", lambda: [dict(p) for p in picks])
    return state_file


def test_live_off_buys_paper_rows_and_sends_no_live_order(tmp_path, monkeypatch, buyer_env):
    # The real buyer end to end (no stubbed evaluate): a live-cohort pick and a paper-cohort pick on
    # the slate, live switch off. The paper pick is recorded, nothing reaches the executor.
    picks = [
        _pick(SPREAD_V7, pick_id="live-pick", event="gl1", model_probability=0.57, ask=0.50),
        _pick(TOTAL_V6, pick_id="paper-pick", event="gp1", market="total", model_probability=0.57, ask=0.50),
    ]
    both = {**LIVE_STATE, "cohorts": [*LIVE_STATE["cohorts"], PAPER_TOTAL_COHORT]}
    state_file = _real_cycle_env(tmp_path, monkeypatch, {**both, "live_enabled": False}, picks)
    auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    assert _BuyExecutor.executed == []
    assert not (tmp_path / "auto_buyer_ledger.jsonl").exists()
    paper_rows = _ledger(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")
    assert [r["pick_id"] for r in paper_rows] == ["paper-pick"]

    # The same slate with live on is what the harness is for: the live pick reaches the executor.
    state_file.write_text(json.dumps(both), encoding="utf-8")
    auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    assert len(_BuyExecutor.executed) == 1 and "gl1" in _BuyExecutor.executed[0][0].market_slug


def test_paper_twin_of_a_live_cohort_keeps_buying_paper_while_live_is_off(tmp_path, monkeypatch, buyer_env):
    # A twin copies the live cohort's window so the two books compare pick for pick. Live off must
    # skip only the live pass: the twin still buys the very pick the live book would have bought.
    twin = {**LIVE_STATE["cohorts"][0], "mode": "paper", "paper_twin": True}
    twinned = {**LIVE_STATE, "cohorts": [*LIVE_STATE["cohorts"], twin]}
    picks = [_pick(SPREAD_V7, pick_id="twin-pick", event="gt1", model_probability=0.57, ask=0.50)]
    state_file = _real_cycle_env(tmp_path, monkeypatch, {**twinned, "live_enabled": False}, picks)
    summary = auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    assert summary["cohort_passes"]["live"]["mode"] == "LIVE_DISABLED"
    assert summary["cohort_passes"]["paper"]["mode"] == "PAPER_TRADING"
    assert _BuyExecutor.executed == []
    assert not (tmp_path / "auto_buyer_ledger.jsonl").exists()
    assert [r["pick_id"] for r in _ledger(tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl")] == [
        "twin-pick"
    ]
    # With live on the same pick is bought by BOTH books, which is what a twin is for.
    state_file.write_text(json.dumps(twinned), encoding="utf-8")
    auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    assert len(_BuyExecutor.executed) == 1
    assert (tmp_path / "auto_buyer_ledger.jsonl").exists()


def test_master_off_stops_the_paper_twin_too(tmp_path, monkeypatch, buyer_env):
    twin = {**LIVE_STATE["cohorts"][0], "mode": "paper", "paper_twin": True}
    state = {**LIVE_STATE, "enabled": False, "live_enabled": True, "cohorts": [*LIVE_STATE["cohorts"], twin]}
    picks = [_pick(SPREAD_V7, pick_id="twin-pick", event="gt2", model_probability=0.57, ask=0.50)]
    _real_cycle_env(tmp_path, monkeypatch, state, picks)
    summary = auto_executor.run_auto_buyer_cycle(forecast_date="2026-11-25")
    assert summary["cohort_passes"]["paper"]["mode"] == "PAPER_DISABLED"
    assert _BuyExecutor.executed == []
    assert not (tmp_path / "paper_auto_buyer" / "auto_buyer_ledger.jsonl").exists()


def test_toggle_live_off_saves_then_cancels_resting_orders_by_id(cycle, monkeypatch, tmp_path):
    path = _write_state(cycle, LIVE_STATE)
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_fallback_row("gtd-1", start_minutes=120.0)])
    seen: list[Any] = []

    class _SavedFirst(_Exchange):
        def cancel(self, order_id, user_command, market_slug=None, stop_before=None):
            seen.append(json.loads(path.read_text(encoding="utf-8")).get("live_enabled"))
            return super().cancel(order_id, user_command, market_slug)

    exchange = _SavedFirst({"gtd-1": ("ORDER_STATE_NEW", 0.0)})
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", lambda _root: exchange)
    state = auto_executor.toggle_live_trading(False)
    assert state["live_enabled"] is False and state["enabled"] is True
    assert state["kill_switch_cancel"]["cancelled"] == ["gtd-1"]
    assert exchange.cancelled == ["gtd-1"] and seen == [False]
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["live_enabled"] is False and on_disk["enabled"] is True
    assert "kill_switch_cancel" not in on_disk


def test_toggle_live_on_cancels_nothing_and_leaves_the_master_alone(cycle, tmp_path):
    path = _write_state(cycle, {**LIVE_STATE, "live_enabled": False})
    state = auto_executor.toggle_live_trading(True)
    assert state["live_enabled"] is True and "kill_switch_cancel" not in state
    assert json.loads(path.read_text(encoding="utf-8"))["enabled"] is True


@pytest.mark.parametrize("bad", ["false", 0, 1, "true"])
def test_toggle_live_rejects_a_non_boolean(cycle, bad):
    path = _write_state(cycle, LIVE_STATE)
    with pytest.raises(ValueError, match="true or false"):
        auto_executor.toggle_live_trading(bad)
    assert "live_enabled" not in json.loads(path.read_text(encoding="utf-8"))


def test_toggle_live_refuses_to_overwrite_a_malformed_state_file(cycle):
    path = cycle["state_file"]
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(auto_executor.AutoBuyerStateUnreadableError):
        auto_executor.toggle_live_trading(True)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_master_toggle_off_still_cancels_and_master_on_with_live_off_cancels_too(
    cycle, monkeypatch, tmp_path
):
    # Live off is the kill switch for live orders: re-enabling the master does not revive them.
    _write_state(cycle, {**LIVE_STATE, "enabled": False, "live_enabled": False})
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_fallback_row("gtd-1", start_minutes=120.0)])
    exchange = _Exchange({"gtd-1": ("ORDER_STATE_NEW", 0.0)})
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", lambda _root: exchange)
    state = auto_executor.toggle_auto_buyer(True)
    assert state["enabled"] is True and state["live_enabled"] is False
    assert exchange.cancelled == ["gtd-1"]


def test_status_exposes_the_live_switch_and_an_effective_live_trading_active(cycle):
    _write_state(cycle, LIVE_STATE)
    status = auto_executor.auto_buyer_status()
    assert status["live_enabled"] is True and status["live_enabled_set"] is False
    assert status["live_trading_active"] is True
    _write_state(cycle, {**LIVE_STATE, "live_enabled": False})
    status = auto_executor.auto_buyer_status()
    assert status["live_enabled"] is False and status["live_enabled_set"] is True
    assert status["enabled"] is True and status["live_trading_active"] is False
    _write_state(cycle, {**LIVE_STATE, "enabled": False, "live_enabled": True})
    assert auto_executor.auto_buyer_status()["live_trading_active"] is False


def test_mid_pass_recheck_stops_live_submissions_when_the_live_switch_goes_off(cycle):
    _write_state(cycle, {**LIVE_STATE, "live_enabled": False})
    assert auto_executor._live_buying_still_enabled() is False
    _write_state(cycle, LIVE_STATE)
    assert auto_executor._live_buying_still_enabled() is True


def test_a_pass_stops_at_the_real_recheck_when_the_live_switch_flips_off_mid_pass(
    tmp_path, buyer_env, monkeypatch
):
    """The real ``_live_buying_still_enabled`` (buyer_env stubs it True) reads the state file each
    submission: the first order goes out, the switch flips, the second never reaches the exchange."""
    state: dict[str, Any] = {"enabled": True, "live_enabled": True}
    monkeypatch.setattr(auto_executor, "_live_buying_still_enabled", _REAL_STILL_ENABLED)
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: dict(state))

    class _FlipsOffAfterFirst(_BuyExecutor):
        def execute(self, ticket: Any, pick_row: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            output = super().execute(ticket, pick_row, **kwargs)
            state["live_enabled"] = False
            return output

    monkeypatch.setattr(auto_executor, "PolymarketExecutor", _FlipsOffAfterFirst)
    picks = [
        _pick(SPREAD_V7, pick_id="p1", event="g1", model_probability=0.57, ask=0.50),
        _pick(SPREAD_V7, pick_id="p2", event="g2", model_probability=0.57, ask=0.50),
    ]
    result = _live_buyer(tmp_path, recheck_enabled_before_submit=True).evaluate_and_execute(picks)
    assert [t.pick_id for t, _ in _BuyExecutor.executed] == ["p1"]
    assert result.live_halt_reason == "kill_switch"


@pytest.mark.parametrize(
    ("state", "cancel_all"),
    [
        ({"enabled": True}, False),
        ({"enabled": True, "live_enabled": True}, False),
        ({"enabled": True, "live_enabled": False}, True),
        ({"enabled": False, "live_enabled": True}, True),
    ],
)
def test_daily_reconcile_cancels_all_when_the_live_switch_is_off(monkeypatch, state, cancel_all):
    from model_prediction.cli import daily

    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: dict(state))
    assert daily._daily_cancels_all_resting() is cancel_all


def test_the_manual_buy_script_refuses_while_the_live_switch_is_off(monkeypatch):
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "auto_polymarket_buyer.py"
    spec = importlib.util.spec_from_file_location("auto_polymarket_buyer_script", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "load_auto_buyer_state", lambda: {"enabled": True, "live_enabled": False})
    assert "live switch is off" in module._execute_refusal()
    monkeypatch.setattr(module, "load_auto_buyer_state", lambda: {"enabled": True})
    assert module._execute_refusal() is None


def test_live_toggle_route_flips_only_the_live_switch(cycle):
    path = _write_state(cycle, LIVE_STATE)
    code, body = _post("/api/auto-buyer/live-toggle", {"enabled": False})
    assert code == 200 and body["live_enabled"] is False and body["enabled"] is True
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["live_enabled"] is False and on_disk["enabled"] is True
    code, body = _post("/api/auto-buyer/live-toggle", {"enabled": True})
    assert code == 200 and body["live_enabled"] is True


@pytest.mark.parametrize(
    "payload", [{}, {"confirm": True}, {"enabled": None}, {"confirm": True, "enabled": None}]
)
def test_live_toggle_route_refuses_a_bare_call_and_never_turns_live_on(cycle, payload):
    """A flip with no explicit value could turn real money ON with no confirm."""
    path = _write_state(cycle, {**LIVE_STATE, "live_enabled": False})
    before = path.read_bytes()
    code, body = _post("/api/auto-buyer/live-toggle", payload)
    assert code == 400 and body["status"] == "refused"
    assert "explicit enabled" in body["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "bare_state", [{}, {"live_enabled": False}, {"live_enabled": True}, {"live_enabled": "x"}]
)
def test_toggle_live_trading_requires_an_explicit_boolean(cycle, bare_state):
    path = _write_state(cycle, {**LIVE_STATE, **bare_state})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="live_enabled"):
        auto_executor.toggle_live_trading()
    with pytest.raises(ValueError, match="live_enabled"):
        auto_executor.toggle_live_trading(None)
    assert path.read_bytes() == before


@pytest.mark.parametrize("bare_state", [{}, {"enabled": False}, {"enabled": True}, {"enabled": "x"}])
def test_toggle_auto_buyer_requires_an_explicit_boolean(cycle, bare_state):
    """A bare master toggle used to flip the saved state, so it could turn the buyer ON unconfirmed."""
    path = _write_state(cycle, {**LIVE_STATE, **bare_state})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="enabled"):
        auto_executor.toggle_auto_buyer()
    with pytest.raises(ValueError, match="enabled"):
        auto_executor.toggle_auto_buyer(None)
    assert path.read_bytes() == before


@pytest.mark.parametrize("payload", [{}, {"confirm": True}, {"enabled": None}])
def test_master_toggle_route_refuses_a_bare_call_and_never_turns_the_buyer_on(cycle, payload):
    path = _write_state(cycle, {**LIVE_STATE, "enabled": False})
    before = path.read_bytes()
    code, body = _post("/api/auto-buyer/toggle", payload)
    assert code == 400 and body["status"] == "refused"
    assert path.read_bytes() == before


@pytest.mark.parametrize("payload", [{"enabled": "false"}, {"enabled": 0}])
def test_live_toggle_route_refuses_a_non_boolean(cycle, payload):
    path = _write_state(cycle, LIVE_STATE)
    code, _body = _post("/api/auto-buyer/live-toggle", payload)
    assert code == 400
    assert "live_enabled" not in json.loads(path.read_text(encoding="utf-8"))
