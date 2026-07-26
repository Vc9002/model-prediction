"""Tests for xlsx_ledger.py -- the Excel I/O every pick ledger read/write
goes through. Previously exercised only incidentally via PickLedger's own
tests; a misclassified field here would silently corrupt every real pick's
on-disk record.

Uses the real ledger.FIELDNAMES schema rather than a made-up minimal list:
_build_summary's dashboard formulas hard-require specific columns (status,
result, record_type, pnl_units, ...) to exist by name, so write_xlsx_rows_atomic
isn't actually generic over arbitrary fieldnames -- that coupling is itself
worth the test suite exercising against the real schema.
"""

from __future__ import annotations

import os

from model_prediction.ledger import FIELDNAMES
from model_prediction.xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic


def _row(**overrides: str) -> dict[str, str]:
    base = dict.fromkeys(FIELDNAMES, "")
    base.update(
        {
            "pick_id": "abc123",
            "status": "open",
            "result": "",
            "record_type": "QUALIFIED_SHADOW_CALL",
            "market_type": "moneyline",
            "american_odds": "-110",
            "model_probability": "0.6235",
            "units": "1.5",
            "pnl_units": "0.9091",
            "line": "-1.5",
            "rationale": "test rationale",
        }
    )
    base.update(overrides)
    return base


def test_round_trip_preserves_every_field_type(tmp_path) -> None:
    path = tmp_path / "picks.xlsx"
    write_xlsx_rows_atomic(path, FIELDNAMES, [_row()])
    headers, read_back = read_xlsx_rows(path)

    assert headers == FIELDNAMES
    assert len(read_back) == 1
    row = read_back[0]
    assert row["pick_id"] == "abc123"
    assert row["american_odds"] == "-110"  # integer field, no decimals
    assert row["model_probability"] == "0.623500"  # six-decimal field
    assert row["units"] == "1.50"  # two-decimal field
    assert row["pnl_units"] == "0.9091"  # four-decimal field
    assert row["line"] == "-1.5"  # general-number field, no padding
    assert row["rationale"] == "test rationale"


def test_empty_string_fields_round_trip_as_empty_not_zero(tmp_path) -> None:
    path = tmp_path / "picks.xlsx"
    write_xlsx_rows_atomic(
        path, FIELDNAMES,
        [_row(units="", pnl_units="", line="", rationale="")],
    )
    _, read_back = read_xlsx_rows(path)
    row = read_back[0]
    assert row["units"] == ""
    assert row["pnl_units"] == ""
    assert row["line"] == ""
    assert row["rationale"] == ""


def test_multiple_rows_preserve_order(tmp_path) -> None:
    path = tmp_path / "picks.xlsx"
    rows = [_row(pick_id=f"pick-{i}", american_odds=str(-100 - i)) for i in range(5)]
    write_xlsx_rows_atomic(path, FIELDNAMES, rows)
    _, read_back = read_xlsx_rows(path)
    assert [row["pick_id"] for row in read_back] == [f"pick-{i}" for i in range(5)]


def test_write_atomic_replaces_rather_than_merges(tmp_path) -> None:
    """A second write must fully replace the first, not append/merge."""
    path = tmp_path / "picks.xlsx"
    write_xlsx_rows_atomic(path, FIELDNAMES, [_row(pick_id="first")])
    write_xlsx_rows_atomic(path, FIELDNAMES, [_row(pick_id="second")])
    _, read_back = read_xlsx_rows(path)
    assert [row["pick_id"] for row in read_back] == ["second"]


def test_write_atomic_leaves_no_temp_files_behind(tmp_path) -> None:
    path = tmp_path / "picks.xlsx"
    write_xlsx_rows_atomic(path, FIELDNAMES, [_row()])
    leftovers = [name for name in os.listdir(tmp_path) if name.startswith("picks-") and name != path.name]
    assert leftovers == []


def test_write_atomic_creates_missing_parent_directories(tmp_path) -> None:
    path = tmp_path / "nested" / "dir" / "picks.xlsx"
    write_xlsx_rows_atomic(path, FIELDNAMES, [_row()])
    assert path.exists()


def test_read_empty_workbook_returns_headers_and_no_rows(tmp_path) -> None:
    path = tmp_path / "empty.xlsx"
    write_xlsx_rows_atomic(path, FIELDNAMES, [])
    headers, rows = read_xlsx_rows(path)
    assert headers == FIELDNAMES
    assert rows == []


def test_read_missing_sheet_raises_value_error(tmp_path) -> None:
    import pytest
    from openpyxl import Workbook

    path = tmp_path / "wrong_sheet.xlsx"
    workbook = Workbook()
    workbook.active.title = "NotPicks"
    workbook.save(path)
    with pytest.raises(ValueError, match="Picks"):
        read_xlsx_rows(path)


def test_negative_and_zero_values_round_trip_correctly(tmp_path) -> None:
    path = tmp_path / "picks.xlsx"
    write_xlsx_rows_atomic(
        path, FIELDNAMES,
        [_row(american_odds="150", model_probability="0.0", units="0.00", pnl_units="-1.5000", line="0")],
    )
    _, read_back = read_xlsx_rows(path)
    row = read_back[0]
    assert row["american_odds"] == "150"
    assert row["pnl_units"] == "-1.5000"
    assert row["line"] == "0"
