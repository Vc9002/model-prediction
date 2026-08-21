"""Tests for the XLSX ↔ SQLite parity checker (consolidation G5)."""

from __future__ import annotations

from model_prediction.ledger_parity import compare


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
