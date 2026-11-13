from __future__ import annotations

import os
import time

import pytest

from model_prediction import xlsx_ledger
from model_prediction.ledger import FIELDNAMES


def test_atomic_write_survives_transient_windows_lock(tmp_path, monkeypatch) -> None:
    """A briefly locked destination (WinError 5) must not fail the whole
    settlement write; the replace is retried until the handle is released."""
    destination = tmp_path / "ncaaf.xlsx"
    real_replace = os.replace
    failures = {"left": 2}

    def flaky_replace(src, dst):
        if failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(xlsx_ledger.os, "replace", flaky_replace)
    monkeypatch.setattr(xlsx_ledger.time, "sleep", lambda _s: None)

    xlsx_ledger.write_xlsx_rows_atomic(destination, FIELDNAMES, [{field: "" for field in FIELDNAMES}])

    assert destination.exists()
    assert failures["left"] == 0
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("picks-")]


def test_atomic_write_gives_up_after_persistent_lock(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "ncaaf.xlsx"

    def locked_replace(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(xlsx_ledger.os, "replace", locked_replace)
    monkeypatch.setattr(xlsx_ledger.time, "sleep", lambda _s: None)

    try:
        xlsx_ledger.write_xlsx_rows_atomic(destination, FIELDNAMES, [{field: "" for field in FIELDNAMES}])
    except PermissionError:
        pass
    else:
        raise AssertionError("a persistently locked destination did not raise")
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("picks-")]


def test_write_atomic_sweeps_a_stale_orphaned_temp_file(tmp_path) -> None:
    """A hard process kill (run_supervisor.py's taskkill/proc.kill(), or an OS
    crash) leaves an orphaned picks-*.xlsx temp file with no Python exception
    handler ever running. The next normal write to the same directory must
    clean it up before starting its own write."""
    stale = tmp_path / "picks-deadbeef.xlsx"
    stale.write_bytes(b"not a real workbook")
    stale_time = time.time() - 10000
    os.utime(stale, (stale_time, stale_time))

    destination = tmp_path / "picks.xlsx"
    xlsx_ledger.write_xlsx_rows_atomic(destination, FIELDNAMES, [{field: "" for field in FIELDNAMES}])

    assert not stale.exists()
    assert destination.exists()


def test_write_atomic_does_not_sweep_a_fresh_temp_file(tmp_path) -> None:
    """A picks-*.xlsx file young enough to be a concurrent write still in
    flight must never be swept -- only files older than the age threshold."""
    fresh = tmp_path / "picks-feedbead.xlsx"
    fresh.write_bytes(b"not a real workbook")

    destination = tmp_path / "picks.xlsx"
    xlsx_ledger.write_xlsx_rows_atomic(destination, FIELDNAMES, [{field: "" for field in FIELDNAMES}])

    assert fresh.exists()
    assert destination.exists()


def test_write_atomic_cleans_up_on_keyboard_interrupt(tmp_path, monkeypatch) -> None:
    """KeyboardInterrupt is a BaseException, not an Exception -- under the old
    `except Exception` clause this would have leaked the temp file."""
    destination = tmp_path / "picks.xlsx"

    def raise_keyboard_interrupt(_fd):
        raise KeyboardInterrupt

    monkeypatch.setattr(xlsx_ledger.os, "fsync", raise_keyboard_interrupt)

    with pytest.raises(KeyboardInterrupt):
        xlsx_ledger.write_xlsx_rows_atomic(destination, FIELDNAMES, [{field: "" for field in FIELDNAMES}])

    assert not [p for p in os.listdir(tmp_path) if p.startswith("picks-")]
