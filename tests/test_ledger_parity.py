"""Tests for the XLSX ↔ SQLite parity checker (consolidation G5)."""

from __future__ import annotations

from model_prediction.ledger import FIELDNAMES, PickLedger
from model_prediction.ledger_parity import _reconcile_ledger, compare
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths
from model_prediction.xlsx_ledger import write_xlsx_rows_atomic


def _row(pick_id: str, **overrides) -> dict:
    row = {
        "pick_id": pick_id,
        "sport": "mlb",
        "status": "settled",
        "result": "won",
        "units": 1.5,
        "pnl_units": 1.32,
        "model_probability": 0.61,
        "line": None,
        "selection": "home",
        "model_id": "mlb-elo-trend-lr-v8",
        "model_artifact_hash": "abc123",
    }
    row.update(overrides)
    return row


def test_identical_rows_are_clean() -> None:
    report = compare([_row("p1"), _row("p2")], [_row("p1"), _row("p2")])
    assert report["clean"] is True
    assert report["rows"]["delta"] == 0


def test_row_count_delta_and_missing_ids() -> None:
    report = compare([_row("p1"), _row("p2")], [_row("p1")])
    assert report["clean"] is False
    assert report["rows"]["delta"] == -1
    assert report["pick_id"]["missing_sqlite"] == 1

    report = compare([_row("p1")], [_row("p1"), _row("p9")])
    assert report["pick_id"]["missing_xlsx"] == 1


def test_field_mismatches_are_counted_by_bucket() -> None:
    report = compare(
        [_row("p1")],
        [
            _row(
                "p1",
                status="open",
                result="lost",
                units=1.6,
                pnl_units=1.31,
                model_probability=0.62,
                line=1.5,
                selection="away",
                model_id="mlb-elo-trend-lr-v7",
                model_artifact_hash="def456",
            )
        ],
    )
    assert report["settlement"] == {"status_mismatches": 1, "result_mismatches": 1}
    assert report["financial"] == {"units_mismatches": 1, "pnl_mismatches": 1}
    assert report["prediction"] == {
        "prob_mismatches": 1,
        "line_mismatches": 1,
        "selection_mismatches": 1,
    }
    assert report["lineage"] == {"model_mismatches": 1, "artifact_mismatches": 1}
    assert report["clean"] is False
    assert report["details"]  # divergences are itemized


def test_tolerances_are_explicit_not_silent_rounding() -> None:
    # Within tolerance: clean.
    report = compare(
        [_row("p1", model_probability=0.61, pnl_units=1.32)],
        [_row("p1", model_probability=0.61 + 1e-13, pnl_units=1.32 + 1e-10)],
    )
    assert report["clean"] is True
    # Outside tolerance: counted.
    report = compare(
        [_row("p1", model_probability=0.61)],
        [_row("p1", model_probability=0.61 + 1e-9)],
    )
    assert report["prediction"]["prob_mismatches"] == 1


def test_none_values_compare_exactly() -> None:
    report = compare([_row("p1", line=None)], [_row("p1", line=None)])
    assert report["clean"] is True
    report = compare([_row("p1", line=None)], [_row("p1", line=1.5)])
    assert report["prediction"]["line_mismatches"] == 1


def test_compare_uses_raw_model_probability_when_sqlite_stores_raw_value() -> None:
    report = compare(
        [_row("p1", model_probability=0.58, model_probability_raw=0.61)],
        [_row("p1", model_probability=0.61)],
    )
    assert report["clean"] is True


def test_sqlite_authority_parity_reads_raw_export_and_repairs_export_only(tmp_path) -> None:
    from tests.test_ledger import request

    paths = RuntimePaths.for_test(tmp_path)
    store = RuntimeLedgerStore(paths)
    ledger = PickLedger(
        tmp_path / "main" / "mlb.xlsx",
        tier="main",
        mirror=store,
        authority="sqlite",
        sport="mlb",
    )
    logged = ledger.append_call(request(), 0.25, 70)
    canonical_events = store.event_count()
    write_xlsx_rows_atomic(ledger.path, FIELDNAMES, [])

    assert ledger.rows()[0]["pick_id"] == logged["pick_id"]
    assert ledger.export_rows() == []
    assert compare(ledger.export_rows(), store.records(tier="main", sport="mlb"))["clean"] is False

    result = _reconcile_ledger(ledger, store, tier="main", sport="mlb")
    assert result["applied"] == 1
    assert result["tombstoned"] == 0
    assert ledger.export_rows()[0]["pick_id"] == logged["pick_id"]
    assert store.records(tier="main", sport="mlb")[0]["status"] == "open"
    assert store.event_count() == canonical_events
    store.close()


def test_xlsx_authority_reconcile_repairs_sqlite_and_tombstones_missing_export(tmp_path) -> None:
    from tests.test_ledger import request

    paths = RuntimePaths.for_test(tmp_path)
    store = RuntimeLedgerStore(paths)
    ledger = PickLedger(
        tmp_path / "main" / "mlb.xlsx",
        tier="main",
        authority="xlsx",
        sport="mlb",
    )
    logged = ledger.append_call(request(), 0.25, 70)

    first = _reconcile_ledger(ledger, store, tier="main", sport="mlb")
    assert first["applied"] == 1
    assert store.records(tier="main", sport="mlb")[0]["pick_id"] == logged["pick_id"]

    canonical_only = {**logged, "pick_id": "canonical-only", "event_id": "event-2"}
    store.apply(ledger._row_mutation(canonical_only, "append", "op-canonical-only"))
    second = _reconcile_ledger(ledger, store, tier="main", sport="mlb")
    assert second["tombstoned"] == 1
    rows = {row["pick_id"]: row for row in store.records(tier="main", sport="mlb")}
    assert rows["canonical-only"]["status"] == "removed"
    assert ledger.export_rows() == [logged]
    store.close()
