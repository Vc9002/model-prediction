from __future__ import annotations

import os

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
