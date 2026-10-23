"""Micro-test for log maintenance and SQLite compaction."""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

from model_prediction.log_maintenance import compact_sqlite_databases, prune_stale_logs, run_maintenance
from model_prediction.runtime_paths import RuntimePaths


def test_prune_stale_and_oversized_logs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        logs_dir = Path(tmp_dir)

        # 1. Old daily log (should be pruned)
        old_log = logs_dir / "daily_2026-08-01.log"
        old_log.write_text("old logs\n")
        # Set mtime to 30 days ago
        old_time = time.time() - (30 * 86400)
        import os

        os.utime(old_log, (old_time, old_time))

        # 2. Fresh daily log (should be kept)
        fresh_log = logs_dir / "daily_2026-10-18.log"
        fresh_log.write_text("fresh logs\n")

        # 3. Oversized log (should be truncated)
        oversized = logs_dir / "server.log"
        oversized.write_text("line\n" * 10000)

        res = prune_stale_logs(logs_dir=logs_dir, max_age_days=14, max_file_size_mb=0.01)
        assert res["deleted_files"] == 1
        assert res["truncated_files"] == 1
        assert not old_log.exists()
        assert fresh_log.exists()
        assert len(oversized.read_text().splitlines()) <= 5000


def test_compact_sqlite_databases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE foo (x INTEGER);")
            conn.executemany("INSERT INTO foo VALUES (?);", [(i,) for i in range(100)])
            conn.commit()
        finally:
            conn.close()

        compacted = compact_sqlite_databases(data_dir=tmp_dir)
        assert "test.db" in compacted


def test_daily_maintenance_entrypoint_uses_supplied_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    db_path = runtime / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE marker (value INTEGER)")
    paths = RuntimePaths(repo_root=tmp_path, runtime_root=runtime)
    supervisor_logs = paths.supervisor_log_root
    supervisor_logs.mkdir(parents=True)
    old_worker_log = supervisor_logs / "production-20260101.log"
    old_worker_log.write_text("old worker output\n")
    import os

    old_time = time.time() - 30 * 86400
    os.utime(old_worker_log, (old_time, old_time))

    report = run_maintenance(repo_root=tmp_path, paths=paths)

    assert report["compacted_databases"] == ["test.db"]
    assert report["daily_logs"] == {"deleted_files": 0, "truncated_files": 0}
    assert report["runtime_logs"]["deleted_files"] == 1
    assert not old_worker_log.exists()
