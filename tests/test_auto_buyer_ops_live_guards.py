"""Live-pass guards added 2026-11-25 (ops): failure alarms when every live order is refused for auth
or balance, the once-per-cycle balance check, the tip-time cross-check against the exchange's own
event start, and SQLite-only pick reads for the live cohort pass.

Reuses the cohort test harness (test_auto_buyer_cohorts): FakeExecutor never trades, the pick readers
and the decorated quote are stubs, and conftest stubs ``_live_cash_check`` unless a test overrides it.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from test_auto_buyer_cohorts import (  # noqa: F401 -- _isolate is an autouse fixture
    ML_V7M,
    SPREAD_V7,
    FakeExecutor,
    _cycle_harness,
    _entry,
    _isolate,
    _live_buyer,
    _quote_from_row,
    _row,
    _state,
)

from model_prediction import dashboard_cache
from model_prediction.data_sources.polymarket_execute import ExecutionGateError
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio import auto_executor

REAL_LIVE_CASH_CHECK = getattr(auto_executor, "_live_cash_check", None)  # captured before conftest stubs it


def _live_rows() -> list[dict[str, Any]]:
    return [
        _row(SPREAD_V7, pick_id="a", event="g1", edge="0.07", model_probability=0.58, ask=0.51),
        _row(SPREAD_V7, pick_id="b", event="g2", edge="0.07", model_probability=0.58, ask=0.51),
    ]


class _RefusingExecutor(FakeExecutor):
    """Refuses picks listed in ``refusals`` with that ExecutionGateError; fills the rest."""

    refusals: ClassVar[dict[str, ExecutionGateError]] = {}

    def execute(self, ticket: Any, pick_row: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        error = _RefusingExecutor.refusals.get(ticket.pick_id)
        if error is not None:
            FakeExecutor.executed.append((ticket, dict(pick_row)))
            raise error
        return super().execute(ticket, pick_row, **kwargs)


def _unauthorized() -> ExecutionGateError:
    return ExecutionGateError(
        'REFUSED by Polymarket US (401): {"message":"unauthenticated"}', status_code=401
    )


# ------------------------------------------------------------------ item 2: refusal alarms


def test_every_live_order_refused_for_auth_fails_the_cycle(monkeypatch, tmp_path):
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=True), _live_rows())
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", _RefusingExecutor)
    _RefusingExecutor.refusals = {"a": _unauthorized(), "b": _unauthorized()}
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert [a["alarm"] for a in raised.value.alarms] == ["live_orders_refused_auth"]
    orders = raised.value.run_summary["cohort_passes"]["live"]["orders"]
    assert {o["refusal_kind"] for o in orders} == {"auth"}
    assert {o["http_status"] for o in orders} == {401}


def test_every_live_order_refused_for_balance_fails_the_cycle(monkeypatch, tmp_path):
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=True), _live_rows())
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", _RefusingExecutor)
    low = ExecutionGateError(
        'REFUSED by Polymarket US (400): {"message":"insufficient balance"}', status_code=400
    )
    _RefusingExecutor.refusals = {"a": low, "b": low}
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert [a["alarm"] for a in raised.value.alarms] == ["live_orders_refused_balance"]


@pytest.mark.parametrize(
    "refusals",
    [
        pytest.param({"a": "auth"}, id="one-auth-one-placed"),
        pytest.param({"a": "gate", "b": "gate"}, id="gate-refusals-only"),
        pytest.param({"a": "auth", "b": "server"}, id="auth-and-a-5xx"),
    ],
)
def test_no_refusal_alarm_unless_every_exchange_answer_is_auth_or_balance(monkeypatch, tmp_path, refusals):
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=True), _live_rows())
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", _RefusingExecutor)
    kinds = {
        "auth": _unauthorized(),
        "gate": ExecutionGateError("REFUSED: the live ask moved more than 2c; nothing was sent."),
        "server": ExecutionGateError("REFUSED by Polymarket US (503): busy", status_code=503),
    }
    _RefusingExecutor.refusals = {pick: kinds[kind] for pick, kind in refusals.items()}
    summary = auto_executor.run_auto_buyer_cycle()
    assert summary["cohort_passes"]["live"]["alarms"] == []


# ------------------------------------------------------------------ item 2: balance check


def test_cash_below_the_first_order_stops_live_buying_with_an_alarm(monkeypatch, tmp_path):
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=True), _live_rows())
    monkeypatch.setattr(
        auto_executor, "_live_cash_check", lambda: {"status": "ok", "available_cash_usd": 3.0}
    )
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert FakeExecutor.executed == []
    assert [a["alarm"] for a in raised.value.alarms] == ["insufficient_cash"]
    assert raised.value.run_summary["cohort_passes"]["live"]["live_halt_reason"] == "insufficient_cash"


def test_cash_for_one_order_buys_one_then_stops(monkeypatch, tmp_path):
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=True), _live_rows())
    monkeypatch.setattr(
        auto_executor, "_live_cash_check", lambda: {"status": "ok", "available_cash_usd": 15.0}
    )
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert len(FakeExecutor.executed) == 1
    assert [a["alarm"] for a in raised.value.alarms] == ["insufficient_cash"]


@pytest.mark.parametrize("status", ["auth_error", "unavailable"])
def test_unreadable_or_refused_balance_skips_live_buying(monkeypatch, tmp_path, status):
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=True), _live_rows())
    monkeypatch.setattr(auto_executor, "_live_cash_check", lambda: {"status": status, "detail": "x"})
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert FakeExecutor.executed == []
    assert FakeExecutor.constructed == 0
    assert [a["alarm"] for a in raised.value.alarms] == [f"live_balance_{status}"]
    live = raised.value.run_summary["cohort_passes"]["live"]
    assert live["mode"] == "LIVE_BLOCKED"


def test_disabled_or_paper_only_cycles_never_read_the_balance(monkeypatch, tmp_path):
    reads: list[int] = []
    monkeypatch.setattr(auto_executor, "_live_cash_check", lambda: reads.append(1) or {"status": "ok"})
    _cycle_harness(monkeypatch, tmp_path, _state([_entry()], enabled=False), _live_rows())
    auto_executor.run_auto_buyer_cycle()
    _cycle_harness(monkeypatch, tmp_path, _state([_entry(mode="paper")], enabled=True), _live_rows())
    auto_executor.run_auto_buyer_cycle()
    assert reads == []


class _BalanceExecutor:
    answer: ClassVar[Any] = None

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def account_balance(self) -> dict[str, Any]:
        if isinstance(_BalanceExecutor.answer, BaseException):
            raise _BalanceExecutor.answer
        return _BalanceExecutor.answer


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (lambda: {"available_cash_usd": 42.5}, "ok"),
        (lambda: {"available_cash_usd": None, "current_balance_usd": 50.0}, "unavailable"),
        (
            lambda: ExecutionGateError("REFUSED by Polymarket US (403): forbidden", status_code=403),
            "auth_error",
        ),
        (lambda: ExecutionGateError("REFUSED: POLYMARKET_KEY_ID is not set."), "auth_error"),
        (lambda: ExecutionGateError("REFUSED by Polymarket US (503): busy", status_code=503), "unavailable"),
        (lambda: OSError("network down"), "unavailable"),
    ],
)
def test_live_cash_check_outcomes(monkeypatch, tmp_path, answer, status):
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "PolymarketExecutor", _BalanceExecutor)
    _BalanceExecutor.answer = answer()
    assert REAL_LIVE_CASH_CHECK is not None, "auto_executor._live_cash_check is missing"
    result = REAL_LIVE_CASH_CHECK()
    assert result["status"] == status
    if status == "ok":
        assert result["available_cash_usd"] == 42.5


# ------------------------------------------------------------------ item 6: tip-time cross-check


def _quote_with_exchange_start(row: dict[str, Any]) -> dict[str, Any]:
    decorated = _quote_from_row(row)
    decorated["quote"]["event_start_utc"] = row.get("test_exchange_start")
    return decorated


@pytest.fixture
def exchange_start_quotes(monkeypatch):
    monkeypatch.setattr("model_prediction.dashboard.orders._decorate_pick", _quote_with_exchange_start)


def _start_in(minutes: float) -> str:
    return iso_utc(utc_now() + timedelta(minutes=minutes))


@pytest.mark.parametrize(
    ("row_minutes", "exchange_minutes", "bought"),
    [
        pytest.param(20, 22, True, id="within-5-min"),
        pytest.param(20, 30, False, id="exchange-10-min-later"),
        pytest.param(20, 12, False, id="exchange-8-min-earlier"),
        pytest.param(2, -2, False, id="exchange-start-passed"),
        pytest.param(20, None, False, id="snapshot-has-no-start"),
    ],
)
def test_live_buy_needs_row_and_exchange_starts_to_agree(
    tmp_path, exchange_start_quotes, row_minutes, exchange_minutes, bought
):
    row = _row(SPREAD_V7, pick_id="t", event="g1", start_minutes=row_minutes)
    row["test_exchange_start"] = None if exchange_minutes is None else _start_in(exchange_minutes)
    result = _live_buyer(tmp_path).evaluate_and_execute([row])
    assert bool(FakeExecutor.executed) is bought
    assert result.rejected_tip_time_mismatch == (0 if bought else 1)


def test_paper_pass_is_not_gated_by_the_exchange_start(tmp_path, exchange_start_quotes):
    row = _row(SPREAD_V7, pick_id="t", event="g1", start_minutes=20)
    row["test_exchange_start"] = None
    result = _live_buyer(tmp_path, mode="paper", execute_live=False, cohorts=()).evaluate_and_execute([row])
    assert result.rejected_tip_time_mismatch == 0


# ------------------------------------------------------------------ item 7: SQLite-only live picks


def test_unreadable_sqlite_picks_block_the_live_pass_and_paper_uses_its_fallback(monkeypatch, tmp_path):
    paper_row = _row(
        ML_V7M, pick_id="ml", event="m1", market="moneyline", edge="0.15", model_probability=0.66
    )
    state = _state(
        [
            _entry(),
            _entry(
                model_id=ML_V7M,
                market="moneyline",
                min_saved_edge=0.12,
                max_saved_edge_exclusive=None,
                mode="paper",
            ),
        ],
        enabled=True,
    )
    calls = _cycle_harness(monkeypatch, tmp_path, state, _live_rows())

    def unreadable():
        raise OSError("database disk image is malformed")

    fallback_reads: list[int] = []
    monkeypatch.setattr(auto_executor, "_read_cohort_candidate_picks", unreadable)
    monkeypatch.setattr(
        auto_executor,
        "_read_paper_cohort_fallback_picks",
        lambda: fallback_reads.append(1) or [dict(paper_row)],
        raising=False,
    )
    with pytest.raises(auto_executor.LiveAutoBuyerAlarm) as raised:
        auto_executor.run_auto_buyer_cycle()
    assert FakeExecutor.executed == []
    assert [a["alarm"] for a in raised.value.alarms] == ["live_picks_unreadable"]
    passes = raised.value.run_summary["cohort_passes"]
    assert passes["live"]["mode"] == "LIVE_BLOCKED"
    assert fallback_reads == [1]
    assert passes["paper"]["orders_count"] == 1
    assert [c for c in calls["configs"] if c.cohorts and c.mode == "live"] == []


def test_cohort_picks_never_fall_back_to_the_dashboard_or_xlsx_reader(monkeypatch):
    xlsx_reads: list[int] = []
    monkeypatch.setattr(
        "model_prediction.dashboard.picks.read_flat_picks", lambda *a, **k: xlsx_reads.append(1) or [{"x": 1}]
    )

    def broken(*_a, **_k):
        raise OSError("ledgers.db unreadable")

    monkeypatch.setattr(dashboard_cache, "read_sqlite_authority_picks", broken, raising=False)
    with pytest.raises(OSError):
        auto_executor._read_cohort_candidate_picks()
    assert xlsx_reads == []


class _FakeStore:
    rows: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *_a: Any) -> None:
        pass

    def event_count(self) -> int:
        return 7

    def pick_rows(self, *, tier: str | None = None, **_k: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in _FakeStore.rows if r.get("tier") == tier]

    def close(self) -> None:
        pass


def test_sqlite_authority_read_matches_the_cache_shape_and_ignores_the_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    _FakeStore.rows = [
        {"tier": "flat", "pick_id": "p1", "status": "open", "edge": 0.07, "units": 2.0, "line": None},
        {"tier": "flat", "pick_id": "p2", "status": "superseded", "edge": 0.05},
        {"tier": "main", "pick_id": "p3", "status": "open"},
    ]
    ledgers_db = tmp_path / "ledgers.db"
    ledgers_db.write_bytes(b"")
    paths = SimpleNamespace(ledgers_db=ledgers_db)
    monkeypatch.setattr(dashboard_cache, "RuntimeLedgerStore", _FakeStore)
    strict = dashboard_cache.read_sqlite_authority_picks("flat", paths=paths)
    cache = dashboard_cache.DashboardCache(
        tmp_path, db_path=tmp_path / "cache.db", authority="sqlite", ledger_store=_FakeStore()
    )
    cache.refresh()
    assert strict == cache.read_picks("flat")
    assert [r["pick_id"] for r in strict] == ["p1"]


def test_sqlite_authority_read_raises_when_the_ledger_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        dashboard_cache.read_sqlite_authority_picks(
            "flat", paths=SimpleNamespace(ledgers_db=Path(tmp_path / "x.db"))
        )
