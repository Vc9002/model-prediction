"""Live auto-buyer safety, Task 2 of quick-261125-hih: dedup keys, top-up contract, pending
exposure, reconcile robustness, the kill switch's cancels, live pick age, identity calibration and
the live book's quarantine.

Nothing here can reach the exchange. Buyer tests swap ``PolymarketExecutor`` for ``_BuyExecutor``
and ``_decorate_pick`` for a fixed fresh quote; ``PolymarketUSClient`` raises. Reconcile and cancel
tests hand in ``_Exchange``, which records every call and raises on any method other than a per-id
``order_snapshots`` read and a per-id ``cancel``. Default executors are built only through
``auto_buyer_ledger._default_live_executor``, which the ``_isolate`` fixture makes raise.
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from model_prediction.audit import AuditLog
from model_prediction.calibration_pipeline import ServingCalibration
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio import auto_buyer_ledger, auto_executor
from model_prediction.portfolio.auto_buyer_ledger import (
    event_key_from_slug,
    reconcile_pending_auto_buyer_fallbacks,
)
from model_prediction.portfolio.auto_executor import AutoExecutionConfig, AutoPolymarketBuyer, BuyCohort
from model_prediction.run_supervisor import RunSupervisor
from model_prediction.runtime_paths import RuntimePaths

# The real reader, kept before the autouse fixture swaps it for a fail-closed stub.
_REAL_READ_DAILY_RUN_HISTORY = getattr(auto_executor, "_read_daily_run_history", None)

SPREAD_V7 = "ncaab-spread-v7"
TOTAL_V6 = "ncaab-total-v6"
SPREAD_LIVE = BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.12, "live")
TOTAL_LIVE = BuyCohort(TOTAL_V6, "ncaab", "total", 0.05, 0.12, "live")
SPREAD_PAPER = BuyCohort(SPREAD_V7, "ncaab", "spread", 0.05, 0.12, "paper")


class _BuyExecutor:
    """Stands in for PolymarketExecutor on the buy side. Never trades."""

    executed: ClassVar[list[Any]] = []
    positions: ClassVar[dict[str, Any]] = {}

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def portfolio_snapshot(self) -> dict[str, Any]:
        return {"positions": dict(_BuyExecutor.positions)}

    def execute(self, ticket: Any, pick_row: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        _BuyExecutor.executed.append((ticket, dict(pick_row)))
        return {
            "status": "submitted",
            "order_id": f"sim-{ticket.pick_id}",
            "order_ids": [f"sim-{ticket.pick_id}"],
            "order_state": "ORDER_STATE_FILLED",
            "filled_size_shares": ticket.size_shares,
            "estimated_filled_cost_usd": ticket.estimated_cost_usd,
        }


class _NoExchange:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        raise RuntimeError("tests must never reach the exchange")


def _no_default_executor(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("a default (real) executor must not be built in this test")


def _identity(_model: str, _market: str, probability: float, **_k: Any) -> ServingCalibration:
    return ServingCalibration(probability, probability, False, "identity", "test_isolation")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    _BuyExecutor.executed = []
    _BuyExecutor.positions = {}
    monkeypatch.delenv("POLYMARKET_KEY_ID", raising=False)
    monkeypatch.setattr(auto_executor, "calibrate_for_serving", _identity)
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", _BuyExecutor)
    monkeypatch.setattr(auto_executor, "_load_pick_input_flags", lambda *_a, **_k: {})
    # Never the real runs.db: an unreadable history keeps every live row at the 180-minute limit.
    monkeypatch.setattr(auto_executor, "_read_daily_run_history", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(auto_executor.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", _no_default_executor)
    monkeypatch.setattr(auto_buyer_ledger, "log_auto_buyer_event", lambda *a, **k: None)
    monkeypatch.setattr("model_prediction.data_sources.polymarket_us.PolymarketUSClient", _NoExchange)
    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", _quote_from_row)


def _slug(market: str, event: str, line: str = "3pt5", side: str = "neg") -> str:
    if market == "total":
        return f"tsc-cbb-{event}-2026-11-25-{line}"
    if market == "moneyline":
        return f"aec-cbb-{event}-2026-11-25"
    return f"asc-cbb-{event}-2026-11-25-{side}-{line}"


def _row(
    model_id: str,
    *,
    pick_id: str,
    event: str,
    market: str = "spread",
    market_slug: str | None = None,
    selection: str | None = None,
    edge: str = "0.07",
    model_probability: float = 0.58,
    ask: float = 0.51,
    units: float = 2.0,
    model_age_minutes: float = 1.0,
    start_minutes: float = 20.0,
) -> dict[str, Any]:
    now = utc_now()
    created = now - timedelta(minutes=model_age_minutes)
    return {
        "pick_id": pick_id,
        "event_id": event,
        "model_version": model_id,
        "league": "ncaab",
        "market_type": market,
        "selection": selection or ("over" if market == "total" else "home"),
        "status": "open",
        "decision": "CALL",
        "reason_code": "FLAT_CALL",
        "edge": edge,
        "line": -3.5 if market == "spread" else 140.5,
        "model_probability": model_probability,
        # Self-certified raw probability, so the B7 guard need not read a canonical ledger.
        "model_probability_raw": model_probability,
        "calibration_method": "identity",
        "market_probability": ask,
        "units": units,
        "home_team": f"{event}-home",
        "away_team": f"{event}-away",
        "market_slug": market_slug or _slug(market, event),
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
            # The exchange snapshot's own start; a live buy needs it to agree with the row's tip time.
            "event_start_utc": row.get("event_start_utc"),
        },
    }


def _buyer(tmp_path: Path, **overrides: Any) -> AutoPolymarketBuyer:
    kwargs: dict[str, Any] = {
        "mode": "live",
        "execute_live": True,
        "unit_value_usd": 5.0,
        "max_game_stake_usd": 25.0,
        "max_daily_spend_usd": 500.0,
        "whitelisted_models": (),
        "disabled_sport_markets": (("ncaab", "spread"), ("ncaab", "total")),
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


def _write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _ledger(path: Path) -> list[dict[str, Any]]:
    return auto_buyer_ledger.read_auto_buyer_ledger(jsonl_path=path)


def _held_row(
    *,
    pick_id: str,
    event: str,
    market: str,
    market_slug: str,
    cost: float = 20.0,
    shares: float = 40.0,
    selection: str | None = None,
    order_state: str = "ORDER_STATE_FILLED",
    **extra: Any,
) -> dict[str, Any]:
    row = {
        "pick_id": pick_id,
        "order_id": f"oid-{pick_id}",
        "order_ids": [f"oid-{pick_id}"],
        "model_id": SPREAD_V7 if market == "spread" else TOTAL_V6,
        "sport": "NCAAB",
        "event_id": event,
        "market_type": market,
        "selection": selection or ("over" if market == "total" else "home"),
        "market_slug": market_slug,
        "token_side": "long",
        "home_team": f"{event}-home",
        "away_team": f"{event}-away",
        "event_start_utc": iso_utc(utc_now() + timedelta(minutes=20)),
        "executed_at_utc": iso_utc(utc_now() - timedelta(minutes=30)),
        "shares": shares,
        "cost_usd": cost,
        "entry_price": 0.5,
        "unit_value_usd": 5.0,
        "model_units": 5.0,
        "status": "open",
        "result": "open",
        "order_state": order_state,
        "fill_known": True,
        "fallback_order_id": None,
        "fallback_reconciled": True,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------- B4a total-line key


def test_total_lines_share_one_key_that_never_equals_the_spread_or_moneyline_key():
    a = event_key_from_slug("tsc-cbb-abchr-sdkst-2026-11-25-117pt5")
    b = event_key_from_slug("tsc-cbb-abchr-sdkst-2026-11-25-120pt5")
    assert a == b == "tsc-cbb-abchr-sdkst-2026-11-25"
    spread = event_key_from_slug("asc-cbb-abchr-sdkst-2026-11-25-pos-11pt5")
    assert spread == event_key_from_slug("asc-cbb-abchr-sdkst-2026-11-25-neg-11pt5")
    assert spread == event_key_from_slug("asc-cbb-abchr-sdkst-2026-11-25-neg-12pt5")
    assert spread == "cbb-abchr-sdkst-2026-11-25"
    moneyline = event_key_from_slug("aec-cbb-abchr-sdkst-2026-11-25")
    assert len({a, spread, moneyline}) == 3
    # cfb/nfl totals carry "-total-NptM": still one key per game, still not the spread key.
    cfb = event_key_from_slug("tsc-cfb-kentst-bowlgr-2026-11-10-total-44pt5")
    assert cfb == event_key_from_slug("tsc-cfb-kentst-bowlgr-2026-11-10-total-47pt5")
    assert cfb != event_key_from_slug("asc-cfb-kentst-bowlgr-2026-11-10-pos-3pt5")


def test_exchange_position_on_a_total_blocks_a_moved_line(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-key")
    _BuyExecutor.positions = {_slug("total", "g1", "140pt5"): {"netPosition": "10"}}
    moved = _row(
        TOTAL_V6, pick_id="t-moved", event="g1", market="total", market_slug=_slug("total", "g1", "143pt5")
    )
    result = _buyer(tmp_path).evaluate_and_execute([moved])
    assert _BuyExecutor.executed == []
    assert result.rejected_dedup == 1


def test_manual_total_row_blocks_a_moved_line(tmp_path):
    # A hand-placed position imported from the exchange has no market_type and usually no event id,
    # so only the slug's game key can link it to a model pick on another line.
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _held_row(
                pick_id=f"MANUAL-{_slug('total', 'g1', '140pt5')}",
                event="",
                market="",
                market_slug=_slug("total", "g1", "140pt5"),
                selection="manual",
                manual=True,
                model_id="manual",
                away_team="",
                home_team="",
            )
        ],
    )
    moved = _row(
        TOTAL_V6, pick_id="t-moved", event="g1", market="total", market_slug=_slug("total", "g1", "143pt5")
    )
    result = _buyer(tmp_path).evaluate_and_execute([moved])
    assert _BuyExecutor.executed == []
    assert result.rejected_dedup == 1


@pytest.mark.parametrize("held_market", ["spread", "total"])
def test_spread_and_total_on_one_game_stay_independent_under_the_shared_game_cap(tmp_path, held_market):
    other = "total" if held_market == "spread" else "spread"
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _held_row(
                pick_id=f"held-{held_market}",
                event="g1",
                selection="manual",
                market="",
                market_slug=_slug(held_market, "g1", "140pt5" if held_market == "total" else "3pt5"),
                manual=True,
                model_id="manual",
                cost=20.0,
                away_team="g1-away",
                home_team="g1-home",
            )
        ],
    )
    pick = _row(
        TOTAL_V6 if other == "total" else SPREAD_V7,
        pick_id=f"new-{other}",
        event="g1",
        market=other,
        units=5.0,
    )
    result = _buyer(tmp_path).evaluate_and_execute([pick])
    assert result.rejected_dedup == 0
    assert len(_BuyExecutor.executed) == 1
    ticket = _BuyExecutor.executed[0][0]
    # The game already carries $20 of the $25 cap (keyed by matchup, the held row has no event id).
    assert ticket.estimated_cost_usd <= 5.0 + 0.10


# ---------------------------------------------------------------- B4c top-up contract


def _partial(pick_id: str, slug: str, *, selection: str = "home", market: str = "spread") -> dict[str, Any]:
    return _held_row(
        pick_id=pick_id,
        event="g1",
        market=market,
        market_slug=slug,
        cost=2.0,
        shares=4.0,
        selection=selection,
        order_state="ORDER_STATE_PARTIALLY_FILLED",
    )


def test_partial_row_is_never_topped_up_on_another_line(tmp_path):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_partial("p1", _slug("spread", "g1", "3pt5"))])
    moved = _row(SPREAD_V7, pick_id="p1", event="g1", market_slug=_slug("spread", "g1", "4pt5"))
    result = _buyer(tmp_path).evaluate_and_execute([moved])
    assert _BuyExecutor.executed == []
    assert result.rejected_dedup == 1


def test_partial_row_without_a_recorded_slug_is_never_topped_up(tmp_path):
    # A legacy row with no market_slug, matched by pick id: the slug check alone must refuse it.
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_partial("p1", "")])
    pick = _row(SPREAD_V7, pick_id="p1", event="g1")
    result = _buyer(tmp_path).evaluate_and_execute([pick])
    assert _BuyExecutor.executed == []
    assert result.rejected_dedup == 1


def test_partial_row_still_tops_up_on_its_own_contract(tmp_path):
    slug = _slug("spread", "g1", "3pt5")
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_partial("p1", slug)])
    pick = _row(SPREAD_V7, pick_id="p1", event="g1", market_slug=slug, units=2.0)
    result = _buyer(tmp_path).evaluate_and_execute([pick])
    assert result.rejected_dedup == 0
    assert len(_BuyExecutor.executed) == 1
    ticket = _BuyExecutor.executed[0][0]
    assert ticket.market_slug == slug
    assert ticket.pick_id == "p1_topup"


def test_partial_total_blocks_the_other_selection_at_a_moved_line(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_partial("over-140", _slug("total", "g1", "140pt5"), selection="over", market="total")],
    )
    under = _row(
        TOTAL_V6,
        pick_id="under-143",
        event="g1",
        market="total",
        selection="under",
        market_slug=_slug("total", "g1", "143pt5"),
    )
    result = _buyer(tmp_path).evaluate_and_execute([under])
    assert _BuyExecutor.executed == []
    assert result.rejected_dedup == 1


# ---------------------------------------------------------------- C6 / C7 pending exposure


def test_daily_spend_counts_only_the_unfilled_remainder(tmp_path):
    now = iso_utc(utc_now())
    rows = [
        # A true fallback: 10 resting, 4 of them since filled (already in cost_usd).
        {
            "pick_id": "fb",
            "order_id": "ioc-fb",
            "fallback_order_id": "gtd-fb",
            "fallback_reconciled": False,
            "fallback_resting_shares": 10.0,
            "fallback_filled_shares": 4.0,
            "shares": 6.0,
            "cost_usd": 3.0,
            "entry_price": 0.5,
            "status": "open",
            "executed_at_utc": now,
        },
        # An unknown-fill primary: 10 requested, reconcile has restated 4 filled into shares.
        {
            "pick_id": "uk",
            "order_id": "ioc-uk",
            "fallback_order_id": "ioc-uk",
            "fill_known": False,
            "fallback_reconciled": False,
            "fallback_resting_shares": 10.0,
            "shares": 4.0,
            "cost_usd": 2.0,
            "entry_price": 0.5,
            "status": "open",
            "executed_at_utc": now,
        },
        # An unknown submission: its requested cost.
        {
            "pick_id": "su",
            "order_id": "",
            "submit_unknown": True,
            "requested_cost_usd": 7.5,
            "shares": 0.0,
            "cost_usd": 0.0,
            "status": "open",
            "executed_at_utc": now,
        },
    ]
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", rows)
    spend, _ = auto_executor._today_auto_buyer_totals(tmp_path)
    # fb: 3.00 filled + 6 * 0.5 pending; uk: 2.00 + 6 * 0.5; su: 7.50. The live book's caps are
    # fee-inclusive, so each pending share also carries its modeled fee (su has no price to model).
    fee = auto_buyer_ledger.taker_fee_per_share(0.5)
    assert spend == pytest.approx(3.0 + 3.0 + 2.0 + 3.0 + 7.5 + 12 * fee, abs=0.01)


def test_a_dead_looking_row_with_a_resting_fallback_still_blocks_its_pick(tmp_path):
    # The IOC filled nothing (CANCELED, 0 shares) but its GTD remainder still rests on the book.
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _held_row(
                pick_id="p1",
                event="g1",
                market="spread",
                market_slug=_slug("spread", "g1"),
                cost=0.0,
                shares=0.0,
                order_state="ORDER_STATE_CANCELED",
                fallback_order_id="gtd-1",
                fallback_reconciled=False,
                fallback_resting_shares=20.0,
            )
        ],
    )
    result = _buyer(tmp_path).evaluate_and_execute([_row(SPREAD_V7, pick_id="p1", event="g1")])
    assert _BuyExecutor.executed == []
    assert result.rejected_dedup == 1


def test_game_exposure_keeps_a_picks_pending_exposure_apart_from_its_filled_cost(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [
            _held_row(
                pick_id="p1",
                event="g1",
                market="spread",
                market_slug=_slug("spread", "g1"),
                cost=3.0,
                shares=6.0,
                order_state="ORDER_STATE_PARTIALLY_FILLED",
                fallback_order_id="gtd-1",
                fallback_reconciled=False,
                fallback_resting_shares=10.0,
                fallback_filled_shares=0.0,
            )
        ],
    )
    exposure = _buyer(tmp_path)._build_bought_index()["game_exposure"]["event:g1"]
    # The game-room check skips only key "p1" (the pick's own filled cost, which the top-up math
    # subtracts); its resting $5 stays on the game.
    pending = 5.0 + 10 * auto_buyer_ledger.taker_fee_per_share(0.5)  # fee-inclusive live caps
    assert exposure == {"p1": pytest.approx(3.0), "p1#pending": pytest.approx(pending)}


# ---------------------------------------------------------------- B6 / C5 / D-02 reconcile


class _Exchange:
    """Records calls; per-id reads and cancels only. ``states`` maps order id -> (state, cum)."""

    def __init__(
        self,
        states: dict[str, tuple[str, float]] | None = None,
        *,
        after_cancel: dict[str, tuple[str, float]] | None = None,
        snapshot_error: bool = False,
        reread_error: bool = False,
        cancel_error: bool = False,
    ) -> None:
        self.states = dict(states or {})
        self.after_cancel = dict(after_cancel or {})
        self.snapshot_error = snapshot_error
        self.reread_error = reread_error
        self.cancel_error = cancel_error
        self.calls: list[tuple[str, Any]] = []
        self.cancelled: list[str] = []

    def order_snapshots(self, order_ids: list[str]) -> dict[str, Any]:
        self.calls.append(("order_snapshots", list(order_ids)))
        if self.snapshot_error and not self.cancelled:
            raise RuntimeError("gateway 503")
        if self.reread_error and self.cancelled:
            raise RuntimeError("gateway 503 on re-read")
        orders = []
        for oid in order_ids:
            source = self.after_cancel if oid in self.cancelled and oid in self.after_cancel else self.states
            if oid in source:
                state, cum = source[oid]
                orders.append({"order_id": oid, "order_state": state, "cum_quantity": cum})
        return {"status": "live", "orders": orders, "unavailable_order_ids": []}

    def cancel(
        self, order_id: str, user_command: bool, market_slug: str | None = None, stop_before: Any = None
    ) -> dict[str, Any]:
        assert user_command is True
        self.calls.append(("cancel", order_id))
        if self.cancel_error:
            raise RuntimeError("cancel refused")
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


def test_snapshot_failure_still_cancels_a_started_fallback_by_id_and_voids_nothing(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row()])
    exchange = _Exchange(snapshot_error=True, reread_error=True)
    summary = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert exchange.cancelled == ["gtd-1"]
    assert "snapshot_error" in summary and "gateway 503" in summary["snapshot_error"]
    assert "cancel_failed" not in summary
    row = _ledger(j_path)[0]
    assert row["result"] == "open" and row["status"] == "open"
    assert row["fallback_reconciled"] is False
    assert row["cancel_requested_at_utc"]
    assert row["cancellation_reason"] == "game_started"


@pytest.mark.parametrize("bad_start", ["", "not-a-time", "2026-11-26T19:00:00", "0001-01-01T00:00:00+05:00"])
def test_reconcile_treats_an_unreadable_start_as_started_and_cancels_by_id(tmp_path, bad_start):
    """An unreadable event_start_utc is the worst case (a game that may have tipped), as in
    cancel_resting_live_fallbacks: the resting remainder is cancelled by its own id, through the same
    branch and counter a genuinely started game reaches. A year-1 offset start used to raise
    OverflowError out of the whole reconcile pass."""
    control = tmp_path / "control"
    control.mkdir()
    _write_ledger(control / "auto_buyer_ledger.jsonl", [_fallback_row()])
    want = reconcile_pending_auto_buyer_fallbacks(
        data_root=control,
        executor=_Exchange(
            {"gtd-1": ("ORDER_STATE_NEW", 0.0)}, after_cancel={"gtd-1": ("ORDER_STATE_CANCELED", 0.0)}
        ),
    )
    assert want["cancelled_expired"] == 1  # a really started game: the counter this path must reach

    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row(event_start_utc=bad_start)])
    exchange = _Exchange(
        {"gtd-1": ("ORDER_STATE_NEW", 0.0)}, after_cancel={"gtd-1": ("ORDER_STATE_CANCELED", 0.0)}
    )
    summary = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert exchange.cancelled == ["gtd-1"]
    assert [name for name, _ in exchange.calls].count("cancel") == 1
    assert summary["cancelled_expired"] == want["cancelled_expired"] == 1
    assert summary["still_pending"] == want["still_pending"] == 0
    assert _ledger(j_path)[0]["fallback_reconciled"] is True


def test_started_fallback_missing_from_the_snapshot_is_cancelled_and_restated(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row(primary_shares=2.0)])
    exchange = _Exchange({}, after_cancel={"gtd-1": ("ORDER_STATE_CANCELED", 3.0)})
    summary = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert exchange.cancelled == ["gtd-1"]
    assert summary["reconciled_filled"] == 1
    row = _ledger(j_path)[0]
    assert row["shares"] == 5.0
    assert row["fallback_reconciled"] is True
    assert row["result"] == "open"


def test_post_cancel_reread_restates_a_late_fill_instead_of_voiding(tmp_path):
    # Pre-cancel read: NEW with 0 filled. Between that read and the cancel, 3 shares filled.
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row(primary_shares=0.0)])
    exchange = _Exchange(
        {"gtd-1": ("ORDER_STATE_NEW", 0.0)}, after_cancel={"gtd-1": ("ORDER_STATE_CANCELED", 3.0)}
    )
    reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    row = _ledger(j_path)[0]
    assert row["result"] != "void"
    assert row["status"] == "open"
    assert row["shares"] == 3.0
    assert row["cost_usd"] == 1.5
    assert row["order_state"] == "ORDER_STATE_PARTIALLY_FILLED"
    assert row["fallback_reconciled"] is True


@pytest.mark.parametrize("reread", ["non_terminal", "error"])
def test_unconfirmed_cancel_keeps_the_row_pending(tmp_path, reread):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row(primary_shares=0.0)])
    exchange = _Exchange({"gtd-1": ("ORDER_STATE_NEW", 0.0)}, reread_error=reread == "error")
    summary = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert exchange.cancelled == ["gtd-1"]
    assert summary["still_pending"] == 1
    row = _ledger(j_path)[0]
    assert row["result"] == "open" and row["status"] == "open"
    assert row["fallback_reconciled"] is False
    assert row["cancel_requested_at_utc"]


def test_failed_cancel_is_reported_and_the_row_stays_pending(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row()])
    exchange = _Exchange({"gtd-1": ("ORDER_STATE_NEW", 0.0)}, cancel_error=True)
    summary = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange)
    assert summary["cancel_failed"] == ["gtd-1"]
    assert summary["errors"] == 1
    assert _ledger(j_path)[0]["fallback_reconciled"] is False


def test_cancel_all_resting_cancels_every_unreconciled_fallback(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(
        j_path, [_fallback_row("gtd-1", start_minutes=120.0), _fallback_row("gtd-2", start_minutes=-5)]
    )
    exchange = _Exchange(
        {"gtd-1": ("ORDER_STATE_NEW", 0.0), "gtd-2": ("ORDER_STATE_NEW", 0.0)},
        after_cancel={"gtd-1": ("ORDER_STATE_CANCELED", 0.0), "gtd-2": ("ORDER_STATE_CANCELED", 0.0)},
    )
    reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange, cancel_all_resting=True)
    assert sorted(exchange.cancelled) == ["gtd-1", "gtd-2"]
    reasons = {r["fallback_order_id"]: r["cancellation_reason"] for r in _ledger(j_path)}
    assert reasons == {"gtd-1": "kill_switch", "gtd-2": "game_started"}


def test_without_cancel_all_an_unstarted_fallback_is_left_resting(tmp_path):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_fallback_row(start_minutes=120.0)])
    exchange = _Exchange({"gtd-1": ("ORDER_STATE_NEW", 0.0)})
    reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=exchange, quote_fn=lambda _s: None)
    assert exchange.cancelled == []


# ---------------------------------------------------------------- D-02 cancel-only helper


def test_cancel_only_helper_cancels_due_fallbacks_by_id_and_never_writes_the_ledger(tmp_path):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(
        j_path,
        [
            _fallback_row("due", start_minutes=0.3),  # 18 s out: inside the 30 s lead
            _fallback_row("later", start_minutes=10.0),
            _fallback_row("done", start_minutes=0.1),
            _fallback_row("memo", start_minutes=0.1),
            _fallback_row("nostart", event_start_utc="not-a-time"),
            _fallback_row("reconciled", start_minutes=0.1, fallback_reconciled=True),
        ],
    )
    before = j_path.read_bytes()
    exchange = _Exchange({"due": ("ORDER_STATE_NEW", 0.0), "done": ("ORDER_STATE_FILLED", 10.0)})
    summary = auto_buyer_ledger.cancel_resting_live_fallbacks(
        data_root=tmp_path,
        executor=exchange,
        cancel_all=False,
        skip_order_ids=frozenset({"memo"}),
        reason="backstop_tipoff",
    )
    assert summary["candidates"] == 3
    assert sorted(summary["cancelled"]) == ["due", "nostart"]
    assert summary["already_terminal"] == ["done"]
    assert summary["failed"] == []
    assert {name for name, _ in exchange.calls} == {"order_snapshots", "cancel"}
    assert j_path.read_bytes() == before


def test_cancel_only_helper_treats_an_overflowing_start_as_due_and_started(tmp_path):
    """A year-1 start with an offset overflows astimezone(UTC): it must read as the worst case (due
    and started), not raise out of the backstop worker."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    _write_ledger(j_path, [_fallback_row("big", event_start_utc="0001-01-01T00:00:00+05:00")])
    exchange = _Exchange({"big": ("ORDER_STATE_NEW", 0.0)})
    summary = auto_buyer_ledger.cancel_resting_live_fallbacks(
        data_root=tmp_path, executor=exchange, cancel_all=False, reason="backstop_tipoff"
    )
    assert summary["candidates"] == 1 and summary["cancelled"] == ["big"]
    deferred = auto_buyer_ledger.cancel_resting_live_fallbacks(
        data_root=tmp_path,
        executor=_Exchange({"big": ("ORDER_STATE_NEW", 0.0)}),
        cancel_all=False,
        reason="backstop_tipoff",
        stop_before=lambda: True,
    )
    assert deferred["deadline_skipped"] == ["big"]
    assert deferred["deadline_skipped_started"] == ["big"]


def test_cancel_by_id_makes_one_call_and_propagates_a_typeerror():
    """The old TypeError retry re-sent the cancel without the slug whenever the executor raised a
    TypeError of its own; now it is one call and the caller's own TypeError handling records it."""

    class _Boom:
        def __init__(self) -> None:
            self.calls = 0

        def cancel(self, order_id, user_command, market_slug=None, stop_before=None):
            self.calls += 1
            raise TypeError("boom")

    boom = _Boom()
    with pytest.raises(TypeError, match="boom"):
        auto_buyer_ledger._cancel_by_id(boom, "gtd-1", "asc-x")
    assert boom.calls == 1


def test_cancel_only_helper_builds_no_executor_when_nothing_is_due(tmp_path, monkeypatch):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_fallback_row(start_minutes=10.0)])
    summary = auto_buyer_ledger.cancel_resting_live_fallbacks(
        data_root=tmp_path, cancel_all=False, reason="backstop_tipoff"
    )
    assert summary["candidates"] == 0
    assert summary["cancelled"] == []


def test_cancel_only_helper_cancel_all_and_failures(tmp_path):
    _write_ledger(
        tmp_path / "auto_buyer_ledger.jsonl",
        [_fallback_row("a", start_minutes=300.0), _fallback_row("b", start_minutes=600.0)],
    )
    exchange = _Exchange({"a": ("ORDER_STATE_NEW", 0.0), "b": ("ORDER_STATE_NEW", 0.0)}, cancel_error=True)
    summary = auto_buyer_ledger.cancel_resting_live_fallbacks(
        data_root=tmp_path, executor=exchange, cancel_all=True, reason="kill_switch"
    )
    assert summary["candidates"] == 2
    assert [f["order_id"] for f in summary["failed"]] == ["a", "b"]
    assert "cancel refused" in summary["failed"][0]["error"]


# ---------------------------------------------------------------- D-02 toggle and cycle


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "auto_buyer_state.json"
    path.write_text(json.dumps({"enabled": True, "mode": "live", "cohorts": []}), encoding="utf-8")
    monkeypatch.setattr(auto_executor, "AUTO_BUYER_STATE_FILE", path)
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    return path


def test_toggle_off_saves_first_then_cancels_resting_fallbacks_by_id(tmp_path, monkeypatch, state_file):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_fallback_row("gtd-1", start_minutes=120.0)])
    seen_enabled: list[Any] = []

    class _SavedFirst(_Exchange):
        def cancel(self, order_id, user_command, market_slug=None, stop_before=None):
            seen_enabled.append(json.loads(state_file.read_text(encoding="utf-8"))["enabled"])
            return super().cancel(order_id, user_command, market_slug)

    exchange = _SavedFirst({"gtd-1": ("ORDER_STATE_NEW", 0.0)})
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", lambda _root: exchange)
    state = auto_executor.toggle_auto_buyer(False)
    assert state["enabled"] is False
    assert state["kill_switch_cancel"]["cancelled"] == ["gtd-1"]
    assert seen_enabled == [False]
    assert "kill_switch_cancel" not in json.loads(state_file.read_text(encoding="utf-8"))


@pytest.mark.parametrize("failure", ["cancel", "executor"])
def test_toggle_off_reports_a_cancel_failure_and_still_saves(tmp_path, monkeypatch, state_file, failure):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_fallback_row("gtd-1", start_minutes=120.0)])
    if failure == "cancel":
        exchange = _Exchange({"gtd-1": ("ORDER_STATE_NEW", 0.0)}, cancel_error=True)
        monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", lambda _root: exchange)
    else:

        def boom(_root):
            raise RuntimeError("no credentials")

        monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", boom)
    state = auto_executor.toggle_auto_buyer(False)
    assert json.loads(state_file.read_text(encoding="utf-8"))["enabled"] is False
    report = state["kill_switch_cancel"]
    if failure == "cancel":
        assert [f["order_id"] for f in report["failed"]] == ["gtd-1"]
    else:
        assert report["status"] == "error" and "no credentials" in report["error"]


def test_toggle_on_cancels_nothing(tmp_path, monkeypatch, state_file):
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_fallback_row("gtd-1", start_minutes=120.0)])
    state = auto_executor.toggle_auto_buyer(True)
    assert state["enabled"] is True
    assert "kill_switch_cancel" not in state


def test_disabled_cycle_cancels_a_pending_live_fallback_through_reconcile(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", tmp_path / "paper_auto_buyer")
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: {"enabled": False, "cohorts": []})
    monkeypatch.setattr(auto_executor, "save_auto_buyer_state", lambda _s: None)
    monkeypatch.setattr(auto_executor, "_append_auto_buyer_cycle_history", lambda *_a, **_k: None)
    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", lambda *_a, **_k: {})
    monkeypatch.setattr("model_prediction.dashboard.picks.read_picks", lambda *a, **k: [])
    monkeypatch.setattr("model_prediction.dashboard.picks.read_flat_picks", lambda *a, **k: [])
    monkeypatch.setattr("model_prediction.dashboard.picks._parse_research_picks", lambda *a, **k: [])
    monkeypatch.setattr(auto_executor, "_capture_missing_active_snapshot_slates", lambda *a, **k: [])
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_fallback_row("gtd-1", start_minutes=120.0)])
    exchange = _Exchange(
        {"gtd-1": ("ORDER_STATE_NEW", 0.0)}, after_cancel={"gtd-1": ("ORDER_STATE_CANCELED", 0.0)}
    )
    monkeypatch.setattr(auto_buyer_ledger, "_default_live_executor", lambda _root: exchange)
    summary = auto_executor.run_auto_buyer_cycle()
    assert exchange.cancelled == ["gtd-1"]
    assert summary["reconciliation"]["cancelled_expired"] == 1
    assert _ledger(tmp_path / "auto_buyer_ledger.jsonl")[0]["cancellation_reason"] == "kill_switch"


# ---------------------------------------------------------------- daily (D-02, C8)


@pytest.mark.parametrize(("enabled", "cancel_all"), [(True, False), ("true", True), (1, True), (False, True)])
def test_daily_reconcile_cancels_all_unless_enabled_is_the_boolean_true(monkeypatch, enabled, cancel_all):
    from model_prediction.cli import daily

    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: {"enabled": enabled})
    assert daily._daily_cancels_all_resting() is cancel_all


def _run_daily_calls(name: str) -> list[ast.Call]:
    from model_prediction.cli import daily

    # Steps 11b/12/12b live in _run_auto_buyer_steps (under auto_buyer.lock), which run_daily calls once.
    calls: list[ast.Call] = []
    for function in (daily.run_daily, daily._run_auto_buyer_steps):
        tree = ast.parse(inspect.getsource(function).replace("\r\n", "\n"))
        calls += [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
        ]
    return calls


def test_daily_own_reconcile_calls_pass_the_kill_switch_flag():
    calls = _run_daily_calls("reconcile_pending_auto_buyer_fallbacks")
    assert len(calls) == 2
    for call in calls:
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        value = kwargs["cancel_all_resting"]
        assert isinstance(value, ast.Call) and getattr(value.func, "id", "") == "_daily_cancels_all_resting"


@pytest.mark.parametrize(("enabled", "runs"), [(True, True), ("true", False), (1, False), (False, False)])
def test_daily_step12_runs_the_cycle_only_for_the_boolean_true(monkeypatch, enabled, runs):
    from model_prediction.cli import daily

    ran: list[Any] = []
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: {"enabled": enabled, "mode": "live"})
    monkeypatch.setattr(auto_executor, "run_auto_buyer_cycle", lambda **kw: ran.append(kw) or {"mode": "X"})
    result = daily._run_auto_buyer_step12("2026-11-25")
    assert bool(ran) is runs
    if runs:
        assert ran == [{"forecast_date": "2026-11-25"}]
    else:
        assert result == {"status": "disabled", "reason": "auto_buyer_disabled"}
    # And step 12 of run_daily goes through it.
    assert len(_run_daily_calls("_run_auto_buyer_step12")) == 1


# ---------------------------------------------------------------- D-03 live pick age


@pytest.mark.parametrize(("age", "bought"), [(181.0, False), (179.0, True)])
def test_live_cohort_buys_only_a_model_call_at_most_three_hours_old(tmp_path, age, bought):
    row = _row(SPREAD_V7, pick_id="p", event="g1", model_age_minutes=age)
    result = _buyer(tmp_path, max_model_age_minutes=720.0).evaluate_and_execute([row])
    assert bool(_BuyExecutor.executed) is bought
    assert result.rejected_stale_model == (0 if bought else 1)


def test_paper_cohort_keeps_the_global_model_age_limit(tmp_path):
    row = _row(SPREAD_V7, pick_id="p", event="g1", model_age_minutes=600.0)
    buyer = _buyer(
        tmp_path, mode="paper", execute_live=False, cohorts=(SPREAD_PAPER,), max_model_age_minutes=720.0
    )
    result = buyer.evaluate_and_execute([row])
    assert result.rejected_stale_model == 0
    assert len(result.dry_run_orders) == 1


# ---------------------------------------------------------------- D-03 overnight extension
# Real timestamps from the 2026-11-23 slate (runs.db daily runs, ledger_records ncaab-spread-v7, event 248488).

_RUN_1800 = "2026-11-23T23:00:01.836968+00:00"  # 18:00 ET 11-23
_RUN_1930 = "2026-11-24T00:30:01.874923+00:00"  # 19:30 ET 11-23
_RUN_2100 = "2026-11-24T02:00:01.853271+00:00"  # 21:00 ET 11-23
_RUN_0030 = "2026-11-24T05:30:01.873477+00:00"  # 00:30 ET 11-24, the next slate date
LATE_CREATED = datetime(2026, 11, 24, 0, 44, 16, 895371, tzinfo=UTC)  # 19:44 ET, written by the 19:30 run
LATE_TIP = datetime(2026, 11, 24, 4, 30, tzinfo=UTC)  # 23:30 ET 11-23
BUY_AT = datetime(2026, 11, 24, 3, 56, tzinfo=UTC)  # 22:56 ET, 34 minutes before the tip
# The last run of the date writes the only rows no later same-date run re-prices (the ET-date clear).
LAST_RUN_CREATED = datetime(2026, 11, 24, 2, 10, 54, tzinfo=UTC)  # 21:10 ET, written by the 21:00 run
POST_MIDNIGHT_TIP = datetime(2026, 11, 24, 6, 0, tzinfo=UTC)  # 01:00 ET 11-24, on the 11-23 slate
OVERNIGHT_BUY_AT = datetime(2026, 11, 24, 5, 25, tzinfo=UTC)  # 00:25 ET, 3 h 14 min after the pick


class _AdvancingClock:
    """Each call is 1 ms after the previous one, so no two captures share an instant."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=1)
        return self.now


def _run(started: str, status: str = "completed") -> dict[str, Any]:
    return {
        "run_id": f"daily-{started}",
        "worker": "daily",
        "status": status,
        "started_at_utc": started,
        "exit_code": {"completed": 0, "failed": 1, "skipped": 75}.get(status),
    }


_EVENING_RUNS = [_run(_RUN_2100), _run(_RUN_1930), _run(_RUN_1800)]


def _late_row(
    *, created: datetime, tip: datetime, pick_id: str = "late", event: str = "248488", **kw: Any
) -> dict[str, Any]:
    row = _row(SPREAD_V7, pick_id=pick_id, event=event, **kw)
    row["created_at_utc"] = iso_utc(created)
    row["event_start_utc"] = iso_utc(tip)
    row["market_quote_observed_at_utc"] = iso_utc(created - timedelta(minutes=1))
    return row


def _overnight(
    monkeypatch, tmp_path, rows, runs, *, at: datetime = BUY_AT, **overrides: Any
) -> tuple[Any, list[int]]:
    calls: list[int] = []

    def _history(*_a: Any, **_k: Any) -> list[dict[str, Any]] | None:
        calls.append(1)
        return None if runs is None else [dict(r) for r in runs]

    monkeypatch.setattr(auto_executor, "_read_daily_run_history", _history)
    monkeypatch.setattr(auto_executor, "utc_now", _AdvancingClock(at))
    result = _buyer(tmp_path, max_model_age_minutes=720.0, **overrides).evaluate_and_execute(rows)
    return result, calls


def test_late_tip_the_2100_run_repriced_keeps_the_three_hour_limit(monkeypatch, tmp_path):
    # (a) The 21:00 ET run (--date 2026-11-23) retires the 19:44 ET pick: its created and event timestamps
    # (00:44Z and 04:30Z on 11-24) are both on ET date 2026-11-23. Until 2026-11-25 the clear prefix-matched
    # the raw UTC strings, kept the row, and this pick was bought 3 h 12 min old under the extension.
    row = _late_row(created=LATE_CREATED, tip=LATE_TIP)
    result, calls = _overnight(monkeypatch, tmp_path, [row], _EVENING_RUNS)
    assert not _BuyExecutor.executed
    assert result.rejected_stale_model == 1
    assert result.live_overnight_age_extended == 0
    assert calls == [1]


def test_row_from_the_last_run_of_its_date_is_bought_at_the_overnight_limit(monkeypatch, tmp_path):
    # (a') Written 21:10 ET by the 21:00 run for a 01:00 ET tip on the 11-23 slate: no later 2026-11-23 run
    # exists, so it is the freshest pick the scheduler could produce. 3 h 14 min old at the buy.
    row = _late_row(created=LAST_RUN_CREATED, tip=POST_MIDNIGHT_TIP)
    result, calls = _overnight(monkeypatch, tmp_path, [row], _EVENING_RUNS, at=OVERNIGHT_BUY_AT)
    assert len(_BuyExecutor.executed) == 1
    assert result.rejected_stale_model == 0
    assert result.live_overnight_age_extended == 1
    assert calls == [1]
    summary = auto_executor._cohort_pass_summary("LIVE_EXECUTION", (SPREAD_LIVE,), result, executing=True)
    assert summary["live_overnight_age_extended"] == 1


@pytest.mark.parametrize(
    ("status", "bought"), [("completed", False), ("failed", False), ("started", False), ("skipped", True)]
)
def test_a_run_that_would_reprice_the_row_keeps_the_three_hour_limit(monkeypatch, tmp_path, status, bought):
    # (b) Priced 18:16 ET (23:16Z, UTC date 2026-11-23): the 21:00 ET run's clear retires it, so a 21:00
    # run that started after the pick (finished, failed or still running) could have re-priced it. A lock
    # skip never forecast. The 19:30 run is a skip here so the 21:00 run alone decides.
    row = _late_row(created=datetime(2026, 11, 23, 23, 16, 30, tzinfo=UTC), tip=LATE_TIP)
    runs = [_run(_RUN_2100, status), _run(_RUN_1930, "skipped"), _run(_RUN_1800)]
    result, _ = _overnight(monkeypatch, tmp_path, [row], runs)
    assert bool(_BuyExecutor.executed) is bought
    assert result.rejected_stale_model == (0 if bought else 1)
    assert result.live_overnight_age_extended == (1 if bought else 0)


@pytest.mark.parametrize(("age", "bought"), [(359.0, True), (361.0, False)])
def test_overnight_limit_is_six_hours(monkeypatch, tmp_path, age, bought):
    # (c) No run could re-price the row; past 360 minutes it is refused anyway.
    at = LAST_RUN_CREATED + timedelta(minutes=age)
    row = _late_row(created=LAST_RUN_CREATED, tip=at + timedelta(minutes=30))
    result, _ = _overnight(monkeypatch, tmp_path, [row], [_run(_RUN_0030), *_EVENING_RUNS], at=at)
    assert bool(_BuyExecutor.executed) is bought
    assert result.rejected_stale_model == (0 if bought else 1)


@pytest.mark.parametrize("status", ["completed", "started"])
def test_next_date_0030_run_does_not_count_against_a_prior_slate_late_tip(monkeypatch, tmp_path, status):
    # (f) A 01:00 ET tip priced 21:10 ET by the 21:00 run (slate 2026-11-23). The 00:30 ET run belongs to
    # 2026-11-24; its clear matches the row's ET event date, but it forecasts the next slate, not this
    # game, and restore reopens the same row.
    at = datetime(2026, 11, 24, 5, 35, tzinfo=UTC)  # 3 h 24 min after the pick
    row = _late_row(created=LAST_RUN_CREATED, tip=POST_MIDNIGHT_TIP)
    result, _ = _overnight(monkeypatch, tmp_path, [row], [_run(_RUN_0030, status), *_EVENING_RUNS], at=at)
    assert len(_BuyExecutor.executed) == 1
    assert result.live_overnight_age_extended == 1


def test_unreadable_run_history_keeps_the_three_hour_limit(monkeypatch, tmp_path):
    # (d) The row (a') buys with a readable history.
    row = _late_row(created=LAST_RUN_CREATED, tip=POST_MIDNIGHT_TIP)
    result, calls = _overnight(monkeypatch, tmp_path, [row], None, at=OVERNIGHT_BUY_AT)
    assert not _BuyExecutor.executed
    assert result.rejected_stale_model == 1
    assert result.live_overnight_age_extended == 0
    assert calls == [1]


@pytest.mark.parametrize(
    "runs",
    [
        [],  # an empty history is not "no run started after the pick"
        [_run(_RUN_0030)],  # truncated: nothing at or before the pick, so the window is unproven
        [{"status": "completed", "started_at_utc": "not a time"}, _run(_RUN_2100)],
        [_run("2026-11-24T02:00:01"), _run(_RUN_1800)],  # naive timestamp
    ],
)
def test_history_that_cannot_prove_the_window_keeps_the_three_hour_limit(monkeypatch, tmp_path, runs):
    # The same row and buy time as (f), which buys with the full history.
    row = _late_row(created=LAST_RUN_CREATED, tip=POST_MIDNIGHT_TIP)
    result, _ = _overnight(monkeypatch, tmp_path, [row], runs, at=datetime(2026, 11, 24, 5, 35, tzinfo=UTC))
    assert not _BuyExecutor.executed
    assert result.rejected_stale_model == 1


def test_paper_cohort_age_limit_never_consults_run_history(monkeypatch, tmp_path):
    # (e) A paper cohort keeps the global 720-minute limit and never reads runs.db.
    row = _late_row(created=BUY_AT - timedelta(minutes=600), tip=LATE_TIP)
    result, calls = _overnight(
        monkeypatch, tmp_path, [row], None, mode="paper", execute_live=False, cohorts=(SPREAD_PAPER,)
    )
    assert result.rejected_stale_model == 0
    assert len(result.dry_run_orders) == 1
    assert result.live_overnight_age_extended == 0
    assert calls == []


def test_only_the_newest_open_row_of_a_slot_gets_the_extension(monkeypatch, tmp_path):
    # A run appends a new row when the line moves and leaves the old one open (event 303437, total
    # 139.5 -> 138.5 at 02:10Z on 2026-11-24). The 21:00 run is a lock skip here, so only the newer row
    # keeps the old line's 19:44 ET row, which is not the freshest pick, from the extension.
    old = _late_row(created=LATE_CREATED, tip=LATE_TIP, pick_id="old")
    new = _late_row(
        created=datetime(2026, 11, 24, 2, 10, 54, tzinfo=UTC),
        tip=LATE_TIP,
        pick_id="new",
        market_slug=_slug("spread", "248488", line="2pt5"),
    )
    new["line"] = -2.5
    runs = [_run(_RUN_2100, "skipped"), _run(_RUN_1930), _run(_RUN_1800)]
    result, _ = _overnight(monkeypatch, tmp_path, [old, new], runs)
    assert result.live_overnight_age_extended == 0
    assert result.rejected_stale_model == 1
    assert [ticket.pick_id for ticket, _row in _BuyExecutor.executed] == ["new"]


def test_run_history_is_read_once_per_cycle_and_only_when_a_row_needs_it(monkeypatch, tmp_path):
    rows = [
        _late_row(created=LAST_RUN_CREATED, tip=POST_MIDNIGHT_TIP, pick_id="a", event="e1"),
        _late_row(created=LAST_RUN_CREATED, tip=POST_MIDNIGHT_TIP, pick_id="b", event="e2"),
    ]
    result, calls = _overnight(monkeypatch, tmp_path / "late", rows, _EVENING_RUNS, at=OVERNIGHT_BUY_AT)
    assert calls == [1]
    assert result.live_overnight_age_extended == 2
    fresh = _late_row(
        created=OVERNIGHT_BUY_AT - timedelta(minutes=30), tip=POST_MIDNIGHT_TIP, pick_id="c", event="e3"
    )
    _result, calls = _overnight(monkeypatch, tmp_path / "fresh", [fresh], _EVENING_RUNS, at=OVERNIGHT_BUY_AT)
    assert calls == []


def test_daily_run_history_reader_fails_closed_and_never_creates_runs_db(tmp_path):
    assert _REAL_READ_DAILY_RUN_HISTORY is not None
    paths = RuntimePaths.for_test(tmp_path)
    assert _REAL_READ_DAILY_RUN_HISTORY(paths) is None
    assert not paths.runs_db.exists()
    # A separate root: the captured log record's traceback keeps the failed connection open on Windows.
    corrupt = RuntimePaths.for_test(tmp_path / "corrupt")
    corrupt.runs_db.write_bytes(b"not a sqlite database " * 64)
    assert _REAL_READ_DAILY_RUN_HISTORY(corrupt) is None
    supervisor = RunSupervisor(paths=paths)
    for run_id, worker, started in (
        ("daily-a", "daily", _RUN_1930),
        ("daily-b", "daily", _RUN_2100),
        ("buyer-a", "auto-buyer", "2026-11-24T03:00:00+00:00"),
    ):
        supervisor._insert_run(
            {
                "run_id": run_id,
                "worker": worker,
                "command": "[]",
                "status": "completed",
                "started_at_utc": started,
            }
        )
    supervisor.close()
    history = _REAL_READ_DAILY_RUN_HISTORY(paths)
    assert [r["run_id"] for r in history] == ["daily-b", "daily-a"]


@pytest.mark.parametrize("run_date", ["2026-11-23", "2026-11-24"])
def test_overnight_rule_mirrors_the_daily_clear(monkeypatch, run_date):
    # The extension rests on _clear_today_open's ET-date match. If that clear changes, this fails, and the
    # extension must be revisited rather than left open.
    from model_prediction.cli import commands

    class _Ledger:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def rows(self) -> list[dict[str, Any]]:
            return [dict(r) for r in self._rows]

        def supersede_open_rows(self, ids: list[str], **_k: Any) -> list[str]:
            return list(ids)

    created_values = ["2026-11-23T23:16:30.495832Z", "2026-11-24T00:44:16.895371Z", "2026-11-24T05:48:02Z"]
    event_values = ["2026-11-23T23:30:00Z", "2026-11-24T04:30:00Z", "2026-11-24T06:00:00+00:00"]
    rows = [
        {
            "pick_id": f"{i}-{j}",
            "status": "open",
            "league": "ncaab",
            "created_at_utc": c,
            "event_start_utc": e,
        }
        for i, c in enumerate(created_values)
        for j, e in enumerate(event_values)
    ]
    monkeypatch.setattr(commands, "utc_now", lambda: datetime(2026, 11, 23, 12, tzinfo=UTC))
    retired = commands._clear_today_open(_Ledger(rows), run_date, by_event_date=True)
    expected = [
        r["pick_id"]
        for r in rows
        if auto_executor._daily_clear_retires(run_date, r["created_at_utc"], r["event_start_utc"])
    ]
    assert 0 < len(expected) < len(rows)
    assert sorted(retired) == sorted(expected)


# ---------------------------------------------------------------- B7 identity calibration


@pytest.mark.parametrize(
    "served",
    [
        ServingCalibration(0.56, 0.58, True, "platt-recalibrated-v1", "applied"),
        ServingCalibration(0.58, 0.58, False, "isotonic", "odd"),
        ServingCalibration(0.57, 0.58, False, "identity", "odd"),
    ],
)
def test_live_cohort_refuses_a_non_identity_served_calibration(tmp_path, monkeypatch, served):
    monkeypatch.setattr(auto_executor, "calibrate_for_serving", lambda *_a, **_k: served)
    rows = [_row(SPREAD_V7, pick_id="p1", event="g1"), _row(SPREAD_V7, pick_id="p2", event="g2")]
    result = _buyer(tmp_path).evaluate_and_execute(rows)
    assert _BuyExecutor.executed == []
    assert result.rejected_live_calibration == 2
    assert [a["alarm"] for a in result.alarms] == ["live_calibration_not_identity"]


def test_paper_cohort_is_unaffected_by_the_live_calibration_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(
        auto_executor,
        "calibrate_for_serving",
        lambda *_a, **_k: ServingCalibration(0.60, 0.58, True, "platt-recalibrated-v1", "applied"),
    )
    buyer = _buyer(tmp_path, mode="paper", execute_live=False, cohorts=(SPREAD_PAPER,))
    result = buyer.evaluate_and_execute([_row(SPREAD_V7, pick_id="p1", event="g1")])
    assert result.rejected_live_calibration == 0
    assert len(result.dry_run_orders) == 1


# The NCAAB forward calibrates inside the forecast (college_basketball_forward.py) and writes the
# served value as model_probability, so calibrate_for_serving above sees an already-calibrated number.
# The buyer's dashboard rows carry only that served value; the raw value and method live in the
# canonical ledger's decision_payload_json (shape below from a real 2026-11-25 ncaab-total-v6 flat row).
_REAL_PAYLOAD_FIELDS = {
    "model_probability": 0.665201,
    "model_probability_raw": 0.665201,
    "serving_probability": 0.665201,
    "calibration_method": "identity",
    "calibration_version": "",
    "calibration_artifact_hash": "",
}


def _dashboard_row(pick_id: str, event: str, probability: float) -> dict[str, Any]:
    """A row as read_flat_picks projects it: no model_probability_raw or calibration_method."""
    row = _row(TOTAL_V6, pick_id=pick_id, event=event, market="total", model_probability=probability)
    row.pop("model_probability_raw", None)
    row.pop("calibration_method", None)
    return row


def _write_canonical_ledger(
    paths: RuntimePaths, records: list[tuple[str, str, float, dict[str, Any]]]
) -> None:
    import sqlite3

    paths.ledgers_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.ledgers_db)
    with conn:
        conn.execute(
            "CREATE TABLE ledger_records (pick_id TEXT, ledger_tier TEXT, model_probability REAL,"
            " decision_payload_json TEXT, feature_payload_json TEXT)"
        )
        conn.executemany(
            "INSERT INTO ledger_records VALUES (?, ?, ?, ?, '{}')",
            [(pid, tier, column, json.dumps(payload)) for pid, tier, column, payload in records],
        )
    conn.close()


@pytest.mark.parametrize(
    ("served", "raw", "method"),
    [(0.60, 0.57, "platt"), (0.60, 0.60, "platt"), (0.60, 0.57, "identity"), (0.60, 0.57, "")],
)
def test_live_row_calibrated_inside_the_forecast_is_refused(tmp_path, served, raw, method):
    rows = [
        _row(SPREAD_V7, pick_id="p1", event="g1", model_probability=served),
        _row(SPREAD_V7, pick_id="p2", event="g2", model_probability=served),
    ]
    for row in rows:
        row["model_probability_raw"] = raw
        row["calibration_method"] = method
    result = _buyer(tmp_path).evaluate_and_execute(rows)
    assert _BuyExecutor.executed == []
    assert result.rejected_live_calibration == 2
    assert [a["alarm"] for a in result.alarms] == ["live_calibration_not_identity"]


def test_real_shaped_identity_row_passes_the_live_calibration_guard(tmp_path, monkeypatch):
    paths = RuntimePaths.for_test(tmp_path)
    _write_canonical_ledger(
        paths,
        [
            ("p1", "flat", 0.665201, _REAL_PAYLOAD_FIELDS),
            ("p1", "main", 0.665201, _REAL_PAYLOAD_FIELDS),
        ],
    )
    monkeypatch.setattr(auto_executor, "_paths", paths)
    row = _dashboard_row("p1", "g1", 0.665201)
    row["test_ask"] = 0.58
    result = _buyer(tmp_path).evaluate_and_execute([row])
    assert result.rejected_live_calibration == 0
    assert len(_BuyExecutor.executed) == 1


@pytest.mark.parametrize(
    "records",
    [
        [],  # the pick is not in the canonical ledger
        [
            (
                "p1",
                "flat",
                0.665201,
                {k: v for k, v in _REAL_PAYLOAD_FIELDS.items() if k != "model_probability_raw"},
            )
        ],
        [("p1", "flat", 0.62, _REAL_PAYLOAD_FIELDS)],  # the raw column disagrees with the served value
        [
            ("p1", "flat", 0.665201, _REAL_PAYLOAD_FIELDS),
            ("p1", "main", 0.665201, {"calibration_method": "platt"}),
        ],
        None,  # no ledgers.db at all
    ],
)
def test_live_row_without_a_provable_raw_probability_is_refused(tmp_path, monkeypatch, records):
    paths = RuntimePaths.for_test(tmp_path)
    if records is not None:
        _write_canonical_ledger(paths, records)
    monkeypatch.setattr(auto_executor, "_paths", paths)
    row = _dashboard_row("p1", "g1", 0.665201)
    row["test_ask"] = 0.58
    result = _buyer(tmp_path).evaluate_and_execute([row])
    assert _BuyExecutor.executed == []
    assert result.rejected_live_calibration == 1
    assert [a["alarm"] for a in result.alarms] == ["live_calibration_not_identity"]


def test_paper_cohort_does_not_need_a_raw_probability(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_executor, "_paths", RuntimePaths.for_test(tmp_path))
    row = _row(SPREAD_V7, pick_id="p1", event="g1")
    del row["model_probability_raw"], row["calibration_method"]
    buyer = _buyer(tmp_path, mode="paper", execute_live=False, cohorts=(SPREAD_PAPER,))
    result = buyer.evaluate_and_execute([row])
    assert result.rejected_live_calibration == 0
    assert len(result.dry_run_orders) == 1


# ---------------------------------------------------------------- D-06 / C4 quarantine


def _settled(model_id: str, pnl: float, hours_ago: float, **extra: Any) -> dict[str, Any]:
    row = {
        "model_id": model_id,
        "status": "settled",
        "result": "win" if pnl > 0 else "loss",
        "settled_at_utc": iso_utc(utc_now() - timedelta(hours=hours_ago)),
        "cost_usd": 1.0,
        "pnl_usd": pnl,
    }
    row.update(extra)
    return row


def test_live_cohort_pass_never_quarantines_a_losing_model(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: {"enabled": True})
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [_settled(SPREAD_V7, -1.0, h) for h in range(20)])
    state = {
        "enabled": True,
        "mode": "live",
        "unit_value_usd": 5.0,
        "max_game_stake_usd": 25.0,
        "max_daily_spend_usd": 500.0,
        "max_event_lookahead_hours": 1.0,
        "disabled_sport_markets": ["ncaab:spread"],
        "cohorts": [],
    }
    config = auto_executor._cohort_pass_config(state, cohorts=(SPREAD_LIVE,), mode="live", execute_live=True)
    assert config.auto_quarantine_underperforming is False
    config = AutoExecutionConfig(**{**vars(config), "capture_missing_slates": False})
    buyer = AutoPolymarketBuyer(config=config, audit=AuditLog(tmp_path / "audit.jsonl"), data_root=tmp_path)
    result = buyer.evaluate_and_execute([_row(SPREAD_V7, pick_id="p1", event="g1")])
    assert result.rejected_quarantined_model == 0
    assert len(_BuyExecutor.executed) == 1


def test_quarantine_sample_excludes_void_zero_fill_and_unknown_rows(tmp_path):
    rows = [_settled("m", -1.0, 10 + h) for h in range(14)]
    rows += [_settled("m", 0.0, h, result="void", shares=0.0, cost_usd=0.0) for h in range(4)] + [
        _settled("m", 0.0, 5, submit_unknown=True, cost_usd=0.0)
    ]
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", rows)
    # 14 real losses: below the 15-pick sample, so no quarantine. Counting the 5 non-bets made it 19.
    assert auto_executor._get_quarantined_underperforming_models(tmp_path) == {}
    _write_ledger(tmp_path / "auto_buyer_ledger.jsonl", [*rows, _settled("m", -1.0, 30)])
    assert "m" in auto_executor._get_quarantined_underperforming_models(tmp_path)


def test_topup_merge_resets_the_earlier_legs_fallback_fills(tmp_path):
    # The merge folds the earlier fallback's fills into primary_filled_*; left in place, its
    # fallback_filled_shares would be subtracted from the new leg's remainder and under-count the caps.
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    earlier = _fallback_row("gtd-old", start_minutes=60.0, primary_shares=2.0, resting=8.0)
    earlier.update(
        {
            "shares": 6.0,
            "cost_usd": 3.0,
            "fallback_filled_shares": 4.0,
            "fallback_filled_cost_usd": 2.0,
            "fallback_reconciled": True,
            "order_state": "ORDER_STATE_PARTIALLY_FILLED",
        }
    )
    _write_ledger(j_path, [earlier])
    merged = auto_buyer_ledger.record_auto_buy_execution(
        {
            "pick_id": earlier["pick_id"],
            "market_slug": earlier["market_slug"],
            "token_side": "long",
            "is_topup": True,
            "shares": 0.0,
            "cost_usd": 0.0,
            "limit_price": 0.5,
            "unit_value_usd": 5.0,
            "order_ids": ["ioc-new", "gtd-new"],
            "fallback_order_id": "gtd-new",
            "fallback_status": "resting",
            "fallback_resting_shares": 6.0,
            "target_spend": 10.0,
        },
        order_id="ioc-new",
        order_state="ORDER_STATE_CANCELED",
        jsonl_path=j_path,
    )
    assert merged["fallback_order_id"] == "gtd-new"
    assert auto_executor._pending_exposure_cost(merged) == pytest.approx(6.0 * 0.5)
