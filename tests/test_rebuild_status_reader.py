"""Tests for RebuildStatusReader's runtime-path wiring.

`dashboard/rebuild_status.py` had zero test coverage before this file --
these tests focus on the specific thing changed here (accepting an explicit
`RuntimePaths`, defaulting safely when one isn't given), not a full rewrite
of coverage for the whole reader.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"
if str(DASHBOARD_DIR.parent) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR.parent))

from dashboard.rebuild_status import RebuildStatusReader, read_rebuild_view
from model_prediction.runtime_paths import RuntimePaths


def test_reader_defaults_to_repo_colocated_runtime_when_no_paths_given(tmp_path):
    reader = RebuildStatusReader(tmp_path)
    assert reader.output_root == (tmp_path / "outputs" / "rebuild").resolve()
    assert reader.data_root == (tmp_path / "data" / "rebuild").resolve()


def test_reader_honors_explicit_external_runtime_paths(tmp_path):
    paths = RuntimePaths(repo_root=tmp_path / "repo", runtime_root=tmp_path / "external-runtime")
    reader = RebuildStatusReader(paths=paths)
    assert reader.output_root == (tmp_path / "repo" / "outputs" / "rebuild").resolve()
    assert reader.data_root == (tmp_path / "external-runtime" / "rebuild").resolve()
    assert reader.shadow_db == reader.data_root / "shadow.db"


def test_reader_never_creates_a_database_it_finds_missing(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    reader = RebuildStatusReader(paths=paths)
    assert not reader.shadow_db.exists()
    rows, error = reader._query(reader.shadow_db, "SELECT 1")
    assert rows == []
    assert error is not None and "does not exist" in error
    # The read attempt itself must not have created the file or its parents.
    assert not reader.shadow_db.exists()
    assert not reader.shadow_db.parent.exists()


def test_reader_opens_an_existing_database_strictly_read_only(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    reader = RebuildStatusReader(paths=paths)
    reader.shadow_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(reader.shadow_db)
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO runs (id) VALUES (1)")
    conn.commit()
    conn.close()

    rows, error = reader._query(reader.shadow_db, "SELECT id FROM runs")
    assert error is None
    assert rows == [{"id": 1}]

    ro_conn = reader._open_readonly(reader.shadow_db)
    try:
        with __import__("pytest").raises(sqlite3.OperationalError):
            ro_conn.execute("INSERT INTO runs (id) VALUES (2)")
    finally:
        ro_conn.close()


def test_read_rebuild_view_with_missing_runtime_root_reports_unavailable_not_crash(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    result = read_rebuild_view("status", paths=paths)
    assert result["production_promotion"] is False
    assert "reason" in result


def test_read_rebuild_view_rejects_unknown_view(tmp_path):
    paths = RuntimePaths.for_test(tmp_path)
    result = read_rebuild_view("not_a_real_view", paths=paths)
    assert result["status"] == "unavailable"
    assert "unknown rebuild view" in result["reason"]
