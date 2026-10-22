from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ledger_dedupe_report.py"
spec = importlib.util.spec_from_file_location("ledger_dedupe_report", SCRIPT)
report_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_module)


def _row(pick_id, created, units, pnl, result="win", event="e1", model="m1"):
    return {
        "pick_id": pick_id,
        "model_id": model,
        "event_id": event,
        "market_type": "total",
        "selection": "over",
        "line": 43.5,
        "units": units,
        "result": result,
        "pnl_units": pnl,
        "created_at_utc": created,
    }


def test_keeps_latest_row_per_pick():
    rows = [
        _row("a", "2026-10-17T05:00:00Z", 1.25, -1.25, "loss"),
        _row("b", "2026-10-17T10:00:00Z", 1.5, -1.5, "loss"),
        _row("c", "2026-10-17T16:00:00Z", 1.5, -1.5, "loss"),
        _row("d", "2026-10-17T05:00:00Z", 1.0, 0.9, "win", event="e2"),
    ]
    kept = {r["pick_id"] for r in report_module.dedupe(rows, "latest")}
    assert kept == {"c", "d"}


def test_keep_first_and_tiebreak():
    rows = [_row("b", "2026-10-17T05:00:00Z", 1, 1), _row("a", "2026-10-17T05:00:00Z", 1, 1)]
    assert report_module.dedupe(rows, "first")[0]["pick_id"] == "a"
    assert report_module.dedupe(rows, "latest")[0]["pick_id"] == "b"
    with pytest.raises(ValueError):
        report_module.dedupe(rows, "newest")


def test_totals_count_each_pick_once():
    rows = [_row("a", "t1", 1.0, 0.9, "win"), _row("b", "t2", 1.0, -1.0, "loss")]
    summary = report_module.totals(report_module.dedupe(rows, "latest"))
    assert summary["rows"] == 1
    assert summary["units"] == pytest.approx(-1.0)
    assert summary["hit"] == 0.0


def test_main_is_read_only_and_reports(tmp_path, capsys):
    db = tmp_path / "ledgers.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE ledger_records (pick_id TEXT, ledger_tier TEXT, sport TEXT, model_id TEXT, "
        "event_id TEXT, market_type TEXT, selection TEXT, line REAL, units REAL, result TEXT, "
        "pnl_units REAL, created_at_utc TEXT)"
    )
    conn.executemany(
        "INSERT INTO ledger_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "a",
                "main",
                "ncaaf",
                "m1",
                "e1",
                "total",
                "over",
                43.5,
                1.25,
                "loss",
                -1.25,
                "2026-10-17T05:00:00Z",
            ),
            (
                "b",
                "main",
                "ncaaf",
                "m1",
                "e1",
                "total",
                "over",
                43.5,
                1.5,
                "loss",
                -1.5,
                "2026-10-17T10:00:00Z",
            ),
            (
                "c",
                "flat",
                "ncaaf",
                "m1",
                "e1",
                "total",
                "over",
                43.5,
                1.0,
                "win",
                0.9,
                "2026-10-17T10:00:00Z",
            ),
        ],
    )
    conn.commit()
    conn.close()
    before = db.read_bytes()
    assert report_module.main(["--db", str(db), "--sport", "ncaaf", "--list-duplicates"]) == 0
    out = capsys.readouterr().out
    assert "m1" in out and "-2.75" in out and "-1.50" in out and "kept b" in out
    assert db.read_bytes() == before
    assert report_module.main(["--db", str(tmp_path / "missing.db")]) == 2
