"""Advisory drawdown report for the live auto-buyer cohorts (quick-261125-hih Task 3, DD / D-06).

Every ledger here is a tmp file; nothing reads the runtime root or reaches the exchange.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from model_prediction.portfolio import auto_buyer_ledger, auto_executor
from model_prediction.portfolio.auto_buyer_drawdown import live_cohort_drawdown_report

SPREAD = {
    "model_id": "ncaab-spread-v7",
    "sport": "ncaab",
    "market": "spread",
    "min_saved_edge": 0.05,
    "max_saved_edge_exclusive": 0.12,
    "mode": "live",
    "enabled": True,
    "approved_by": "operator",
    "approved_at_utc": "2026-11-25",
}
TOTAL = {**SPREAD, "model_id": "ncaab-total-v6", "market": "total"}
PAPER = {**SPREAD, "model_id": "ncaab-moneyline-v7m", "market": "moneyline", "mode": "paper"}
SPREAD_LABEL = "ncaab-spread-v7:spread:[0.05,0.12):live"

_counter = 0


def _row(
    result: str,
    pnl: float,
    *,
    cost: float = 10.0,
    unit: float = 5.0,
    model: str = "ncaab-spread-v7",
    market: str = "spread",
    executed: str = "2026-11-25T20:00:00Z",
    settled_minute: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    global _counter
    _counter += 1
    minute = _counter if settled_minute is None else settled_minute
    settled = result in ("win", "loss", "push", "void")
    return {
        "order_id": f"o-{_counter}",
        "pick_id": f"p-{_counter}",
        "model_id": model,
        "sport": "NCAAB",
        "market_type": market,
        "executed_at_utc": executed,
        "result": result,
        "status": "settled" if settled else "open",
        "pnl_usd": pnl,
        "cost_usd": cost,
        "shares": 20.0,
        "unit_value_usd": unit,
        "settled_at_utc": f"2026-11-26T{minute // 60:02d}:{minute % 60:02d}:00Z" if settled else "",
        **extra,
    }


def _report(tmp_path: Path, rows: list[dict[str, Any]], **state: Any) -> dict[str, Any]:
    (tmp_path / "auto_buyer_ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    full_state = {"enabled": True, "cohorts": [SPREAD, TOTAL, PAPER], **state}
    return live_cohort_drawdown_report(full_state, data_root=tmp_path)


def _cohort(report: dict[str, Any], label: str = SPREAD_LABEL) -> dict[str, Any]:
    return next(c for c in report["cohorts"] if c["label"] == label)


def test_figures_are_exact_and_excluded_rows_never_count(tmp_path):
    rows = [
        _row("win", 5.0),
        _row("loss", -10.0),
        _row("loss", -10.0),
        _row("win", 4.0),
        _row("push", 0.0),
        _row("open", 0.0, cost=7.0),
        # Excluded: void zero-fill, manual, submit_unknown, before approval (11-24 23:00 ET), other model.
        _row("void", 0.0, cost=0.0, shares=0.0),
        _row("loss", -50.0, pick_id="MANUAL-x"),
        _row("open", 0.0, cost=0.0, submit_unknown=True, requested_cost_usd=25.0),
        _row("loss", -40.0, executed="2026-11-25T04:00:00Z"),
        _row("loss", -30.0, model="ncaab-spread-v6"),
        _row("loss", -20.0, market="total", model="ncaab-total-v6"),
    ]
    report = _report(tmp_path, rows)
    assert report["status"] == "ok"
    assert [c["label"] for c in report["cohorts"]] == [SPREAD_LABEL, "ncaab-total-v6:total:[0.05,0.12):live"]
    c = _cohort(report)
    assert (c["wins"], c["losses"], c["pushes"], c["settled"]) == (2, 2, 1, 5)
    assert c["rows"] == 6 and c["open"] == 1 and c["open_cost_usd"] == pytest.approx(7.0)
    assert c["cost_usd"] == pytest.approx(50.0)
    assert c["pnl_usd"] == pytest.approx(-11.0)
    assert c["pnl_units"] == pytest.approx(-2.2)
    assert c["roi"] == pytest.approx(-0.22)
    assert c["last30_n"] == 5 and c["last30_roi"] == pytest.approx(-0.22)
    # Cumulative P&L 5, -5, -15, -11, -11: peak 5.
    assert c["max_drawdown_usd"] == pytest.approx(20.0)
    assert c["max_drawdown_units"] == pytest.approx(4.0)
    assert c["current_drawdown_usd"] == pytest.approx(16.0)
    assert c["current_drawdown_units"] == pytest.approx(3.2)
    assert c["warnings"] == [] and report["warnings"] == []
    total = _cohort(report, "ncaab-total-v6:total:[0.05,0.12):live")
    assert total["losses"] == 1 and total["pnl_usd"] == pytest.approx(-20.0)


def test_last30_roi_warns_at_fifteen_settled_not_fourteen(tmp_path):
    report = _report(tmp_path, [_row("loss", -1.2) for _ in range(15)])
    c = _cohort(report)
    assert c["last30_n"] == 15 and c["last30_roi"] == pytest.approx(-0.12)
    assert any("last-30 ROI" in w for w in c["warnings"])
    assert report["warnings"] and report["warnings"][0].startswith(SPREAD_LABEL)

    report = _report(tmp_path, [_row("loss", -1.2) for _ in range(14)])
    assert _cohort(report)["warnings"] == []


def test_last30_uses_only_the_latest_thirty_settled(tmp_path):
    rows = [_row("loss", -10.0, settled_minute=i) for i in range(10)]
    rows += [_row("win", 1.0, settled_minute=100 + i) for i in range(30)]
    c = _cohort(_report(tmp_path, rows, drawdown_warning_usd=1000.0, drawdown_warning_units=1000.0))
    assert c["last30_n"] == 30 and c["last30_roi"] == pytest.approx(0.1)
    assert c["settled"] == 40


def test_current_drawdown_of_100_dollars_warns_and_99_99_does_not(tmp_path):
    c = _cohort(_report(tmp_path, [_row("loss", -100.0, cost=100.0, unit=10.0)]))
    assert c["current_drawdown_usd"] == pytest.approx(100.0)
    assert any("$100.00" in w for w in c["warnings"])

    c = _cohort(_report(tmp_path, [_row("loss", -99.99, cost=100.0, unit=10.0)]))
    assert c["warnings"] == []


def test_twenty_units_warn_independently_of_dollars(tmp_path):
    c = _cohort(_report(tmp_path, [_row("loss", -100.0, cost=100.0)], drawdown_warning_usd=1000.0))
    assert c["current_drawdown_units"] == pytest.approx(20.0)
    assert any("20.0U" in w for w in c["warnings"])

    c = _cohort(_report(tmp_path, [_row("loss", -99.0, cost=100.0)], drawdown_warning_usd=1000.0))
    assert c["warnings"] == []


def test_state_keys_override_and_bad_values_fall_back_to_defaults(tmp_path):
    rows = [_row("loss", -60.0, cost=60.0, unit=10.0)]
    report = _report(tmp_path, rows, drawdown_warning_usd=50.0)
    assert report["thresholds"]["drawdown_warning_usd"] == 50.0
    assert _cohort(report)["warnings"]
    for bad in (True, "abc", float("nan"), -5.0, 0):
        report = _report(tmp_path, rows, drawdown_warning_usd=bad, drawdown_warning_units=bad)
        assert report["thresholds"]["drawdown_warning_usd"] == 100.0
        assert report["thresholds"]["drawdown_warning_units"] == 20.0
        assert _cohort(report)["warnings"] == []


def test_warning_clears_on_recovery_but_max_drawdown_stays(tmp_path):
    rows = [_row("loss", -100.0, cost=100.0), _row("win", 100.0, cost=100.0)]
    c = _cohort(_report(tmp_path, rows))
    assert c["max_drawdown_usd"] == pytest.approx(100.0)
    assert c["current_drawdown_usd"] == pytest.approx(0.0)
    assert c["warnings"] == []


def test_never_raises(tmp_path, monkeypatch):
    def boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("disk gone")

    monkeypatch.setattr(auto_buyer_ledger, "read_auto_buyer_ledger", boom)
    report = live_cohort_drawdown_report({"cohorts": [SPREAD]}, data_root=tmp_path)
    assert report["status"] == "error" and "disk gone" in report["error"]
    assert report["cohorts"] == [] and report["warnings"] == []


def test_no_live_cohorts_is_an_empty_ok_report(tmp_path):
    report = live_cohort_drawdown_report({"enabled": True, "cohorts": [PAPER]}, data_root=tmp_path)
    assert report["status"] == "ok" and report["cohorts"] == []
    report = live_cohort_drawdown_report({"enabled": True}, data_root=tmp_path)
    assert report["status"] == "ok" and report["cohorts"] == []


# ---------------------------------------------------------------- status and last_run


def _isolate_state(tmp_path, monkeypatch, state: dict[str, Any]) -> None:
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: dict(state))


@pytest.mark.parametrize(("enabled", "active"), [(True, True), ("true", False), (False, False)])
def test_auto_buyer_status_carries_live_cohorts_and_drawdown(tmp_path, monkeypatch, enabled, active):
    (tmp_path / "auto_buyer_ledger.jsonl").write_text(json.dumps(_row("loss", -5.0)) + "\n", encoding="utf-8")
    _isolate_state(tmp_path, monkeypatch, {"enabled": enabled, "mode": "paper", "cohorts": [SPREAD, PAPER]})
    status = auto_executor.auto_buyer_status()
    assert status["enabled"] == enabled
    assert status["live_cohorts"] == [SPREAD_LABEL]
    assert status["live_trading_active"] is active
    assert status["live_drawdown"]["status"] == "ok"
    assert _cohort(status["live_drawdown"])["losses"] == 1


def test_auto_buyer_status_legacy_live_mode_without_cohorts(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch, {"enabled": True, "mode": "live"})
    status = auto_executor.auto_buyer_status()
    assert status["live_cohorts"] == [] and status["live_trading_active"] is True
    _isolate_state(tmp_path, monkeypatch, {"enabled": True, "mode": "live", "cohorts": []})
    assert auto_executor.auto_buyer_status()["live_trading_active"] is False


def test_auto_buyer_status_survives_a_drawdown_failure(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch, {"enabled": True, "cohorts": [SPREAD]})

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("unexpected")

    monkeypatch.setattr("model_prediction.portfolio.auto_buyer_drawdown.live_cohort_drawdown_report", boom)
    status = auto_executor.auto_buyer_status()
    assert status["live_trading_active"] is True
    assert status["live_drawdown"]["status"] == "error"


def test_status_route_serves_auto_buyer_status(monkeypatch):
    from io import BytesIO
    from unittest.mock import Mock

    from model_prediction.dashboard import routes

    monkeypatch.setattr(routes, "auto_buyer_status", lambda: {"enabled": True, "live_trading_active": True})
    handler = routes.Handler.__new__(routes.Handler)
    handler.path = "/api/auto-buyer/status"
    handler.headers = {}
    handler.wfile = BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler.do_GET()
    assert json.loads(handler.wfile.getvalue().decode("utf-8")) == {
        "enabled": True,
        "live_trading_active": True,
    }


def test_cycle_last_run_carries_live_drawdown_and_a_warning_never_alarms(tmp_path, monkeypatch):
    """A drawdown warning is advisory (D-06): it lands in last_run and the live pass, never in alarms."""
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", tmp_path / "paper_auto_buyer")
    state = {"enabled": False, "cohorts": [SPREAD]}
    monkeypatch.setattr(auto_executor, "load_auto_buyer_state", lambda: dict(state))
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(auto_executor, "save_auto_buyer_state", lambda s: saved.append(dict(s)))
    history: list[dict[str, Any]] = []
    monkeypatch.setattr(
        auto_executor, "_append_auto_buyer_cycle_history", lambda rec, **_k: history.append(rec)
    )
    monkeypatch.setattr(auto_buyer_ledger, "settle_auto_buyer_ledger", lambda *_a, **_k: {})
    monkeypatch.setattr(
        auto_buyer_ledger, "reconcile_pending_auto_buyer_fallbacks", lambda *_a, **_k: {"checked": 0}
    )
    monkeypatch.setattr("model_prediction.dashboard.picks.read_picks", lambda *a, **k: [])
    monkeypatch.setattr("model_prediction.dashboard.picks.read_flat_picks", lambda *a, **k: [])
    monkeypatch.setattr("model_prediction.dashboard.picks._parse_research_picks", lambda *a, **k: [])
    monkeypatch.setattr(auto_executor, "_capture_missing_active_snapshot_slates", lambda *a, **k: [])
    (tmp_path / "auto_buyer_ledger.jsonl").write_text(
        json.dumps(_row("loss", -150.0, cost=150.0)) + "\n", encoding="utf-8"
    )
    summary = auto_executor.run_auto_buyer_cycle()
    drawdown = summary["live_drawdown"]
    assert drawdown["status"] == "ok" and drawdown["warnings"]
    assert summary["alarms"] == []
    assert summary["cohort_passes"]["live"]["drawdown_warnings"] == drawdown["warnings"]
    assert saved[-1]["last_run"]["live_drawdown"] == drawdown
    live_history = [h for h in history if h.get("pass") == "cohort_live"]
    assert live_history and live_history[0]["drawdown_warnings"] == drawdown["warnings"]


def test_preview_cycle_computes_no_drawdown(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_executor, "DATA", tmp_path)
    monkeypatch.setattr(auto_executor, "PAPER_AUTO_BUYER_DATA_ROOT", tmp_path / "paper_auto_buyer")
    monkeypatch.setattr(
        auto_executor, "load_auto_buyer_state", lambda: {"enabled": False, "cohorts": [SPREAD]}
    )

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("preview must not compute the drawdown report")

    monkeypatch.setattr("model_prediction.portfolio.auto_buyer_drawdown.live_cohort_drawdown_report", boom)
    monkeypatch.setattr(auto_executor, "_read_cohort_candidate_picks", list)
    monkeypatch.setattr(
        auto_executor.AutoPolymarketBuyer,
        "evaluate_and_execute",
        lambda self, *a: auto_executor.AutoExecutionResult(),
    )
    summary = auto_executor.run_auto_buyer_cycle(preview=True)
    assert "live_drawdown" not in summary


def test_an_unparseable_executed_at_is_counted_but_a_blank_one_is_not(tmp_path):
    """The approval window may only ADD rows: a present-but-unreadable executed_at_utc counts toward
    the drawdown (fail closed), while a blank or missing one keeps today's skip (fixtures omit it and
    the real writer always writes iso_utc)."""
    rows = [
        _row("loss", -10.0, executed="garbage"),
        _row("loss", -10.0, executed="0001-01-01T00:00:00+05:00"),
        _row("loss", -10.0, executed=""),
        _row("loss", -10.0, executed=None),
        _row("win", 4.0, executed="2026-11-25T04:00:00Z"),  # readable and before approval: still skipped
    ]
    c = _cohort(_report(tmp_path, rows))
    assert (c["wins"], c["losses"], c["settled"]) == (0, 2, 2)
    assert c["pnl_usd"] == pytest.approx(-20.0)


@pytest.mark.parametrize(
    "bad_approval", ["2026-02-30", "0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00", "not-a-date"]
)
def test_an_unreadable_approval_drops_the_window_and_the_report_still_computes(tmp_path, bad_approval):
    """A corrupt cohort approved_at_utc must not turn the advisory report into status:error: the
    window is dropped, so every row of the cohort counts (the loss alarm only ever widens)."""
    bad = {**SPREAD, "approved_at_utc": bad_approval}
    rows = [_row("loss", -10.0, executed="2026-11-20T04:00:00Z"), _row("win", 4.0)]
    report = _report(tmp_path, rows, cohorts=[bad])
    assert report.get("status") != "error", report
    c = _cohort(report)
    assert (c["wins"], c["losses"]) == (1, 1)


def test_rows_tagged_with_another_cohort_label_are_not_counted(tmp_path):
    """Rows carry the cohort label they were bought under; a row of the same model, sport and
    market bought under a different window (an earlier or replaced cohort) is not this cohort's."""
    rows = [
        _row("loss", -10.0, cohort=SPREAD_LABEL),
        _row("loss", -30.0, cohort="ncaab-spread-v7:spread:[0.05,0.08):live"),
        _row("win", 4.0),  # untagged rows (written before the label was recorded) still count
    ]
    c = _cohort(_report(tmp_path, rows))
    assert (c["wins"], c["losses"]) == (1, 1)
    assert c["pnl_usd"] == pytest.approx(-6.0)
