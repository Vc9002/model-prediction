"""Write-behind spool for ModelLedger (quick 261122-spd).

Every pick appended to the Main and Flat ledgers rewrote a whole per-model workbook (about 90% of one
append's cost, quadratic in ledger size). Under ``defer_model_ledger_writes`` the row goes to an fsync'd
JSONL spool instead and the workbook is rebuilt once on exit. These tests pin that the batching loses
nothing and never changes what a reader or the settlement hook sees.
"""

from __future__ import annotations

import json

import pytest

from model_prediction import model_ledger as ml
from model_prediction.model_ledger import (
    SPOOL_SUFFIX,
    ModelLedger,
    defer_model_ledger_writes,
    flush_pending_model_ledgers,
)


def _record(event_id: str, **extra) -> dict:
    return {
        "model_id": "m1",
        "model_version": "v1",
        "event_id": event_id,
        "market_type": "spread",
        "selection": "home",
        "line": -3.5,
        "model_probability": 0.6,
        "observed_at_utc": "2026-11-22T12:00:00Z",
        "status": "open",
        **extra,
    }


def _workbook_ids(path) -> set[str]:
    return {row["prediction_id"] for row in ml._read_predictions(path)}


def test_deferred_appends_reach_the_workbook_once_on_exit(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "m1.xlsx")
    ledger.append_prediction(_record("e0"))  # immediate: workbook exists
    with defer_model_ledger_writes():
        ids = [ledger.append_prediction(_record(f"e{i}"))["prediction_id"] for i in range(1, 4)]
        assert _workbook_ids(ledger.path).isdisjoint(ids), "deferred rows must not rewrite the workbook"
        assert ledger.spool_path.exists()
        assert {row["prediction_id"] for row in ledger.rows()} >= set(ids), "readers must see the spool"
    assert not ledger.spool_path.exists()
    assert set(ids) <= _workbook_ids(ledger.path)
    assert len(ledger.rows()) == 4


def test_nested_contexts_flush_only_when_the_outermost_exits(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "m1.xlsx")
    with defer_model_ledger_writes():
        with defer_model_ledger_writes():
            row = ledger.append_prediction(_record("e1"))
        assert ledger.spool_path.exists()
        assert row["prediction_id"] not in (_workbook_ids(ledger.path) if ledger.path.exists() else set())
    assert row["prediction_id"] in _workbook_ids(ledger.path)


def test_failed_flush_leaves_a_recoverable_spool(tmp_path, monkeypatch) -> None:
    ledger = ModelLedger(tmp_path / "m1.xlsx")
    row = _append_then_crash(ledger, monkeypatch)
    assert ledger.spool_path.exists()
    assert [r["prediction_id"] for r in ledger.rows()] == [row["prediction_id"]]
    assert flush_pending_model_ledgers(tmp_path) == 1
    assert not ledger.spool_path.exists()
    assert _workbook_ids(ledger.path) == {row["prediction_id"]}


def test_settlement_sees_rows_that_are_only_in_the_spool(tmp_path) -> None:
    """A first-ever deferred row has no workbook yet; the settlement hook must still grade it."""
    ledger = ModelLedger(tmp_path / "m1.xlsx")
    with defer_model_ledger_writes():
        ledger.append_prediction(_record("e1"))
        assert not ledger.path.exists() and ledger.exists()
        settled = ledger.settle_event(ml._event_settlement_key(_record("e1")), result="win", pnl_units=1.0)
        assert len(settled) == 1
    rows = ledger.rows()
    assert [r["status"] for r in rows] == ["settled"]
    assert not ledger.spool_path.exists()


def test_spool_row_already_in_the_workbook_is_not_duplicated(tmp_path) -> None:
    """A flush that crashed between the workbook replace and the spool delete."""
    ledger = ModelLedger(tmp_path / "m1.xlsx")
    row = ledger.append_prediction(_record("e1"))
    ledger.spool_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert [r["prediction_id"] for r in ledger.rows()] == [row["prediction_id"]]
    assert ledger.flush_pending() == 1
    assert len(ledger.rows()) == 1


def _append_then_crash(ledger: ModelLedger, monkeypatch, event_id: str = "e1") -> dict:
    """Append under a deferral whose exit flush fails, as after a crash; the spool stays behind."""
    with monkeypatch.context() as patch:
        patch.setattr(ModelLedger, "flush_pending", _failing_flush)
        with defer_model_ledger_writes():
            return ledger.append_prediction(_record(event_id))


def _failing_flush(self) -> int:
    raise OSError("crash")


def test_torn_spool_line_is_ignored(tmp_path, monkeypatch) -> None:
    ledger = ModelLedger(tmp_path / "m1.xlsx")
    row = _append_then_crash(ledger, monkeypatch)
    with ledger.spool_path.open("a", encoding="utf-8") as handle:
        handle.write('{"prediction_id": "torn')
    assert [r["prediction_id"] for r in ledger.rows()] == [row["prediction_id"]]


def test_without_a_deferral_context_the_write_is_immediate(tmp_path) -> None:
    ledger = ModelLedger(tmp_path / "m1.xlsx")
    row = ledger.append_prediction(_record("e1"))
    assert not ledger.spool_path.exists()
    assert _workbook_ids(ledger.path) == {row["prediction_id"]}


def test_spool_is_never_mistaken_for_a_workbook(tmp_path, monkeypatch) -> None:
    assert not SPOOL_SUFFIX.endswith(".xlsx")
    ledger = ModelLedger(tmp_path / "m1.xlsx")
    _append_then_crash(ledger, monkeypatch)
    assert [p.name for p in tmp_path.glob("*.xlsx")] == []


@pytest.fixture(autouse=True)
def _no_leaked_deferral():
    yield
    assert not ml._model_ledger_writes_deferred()
