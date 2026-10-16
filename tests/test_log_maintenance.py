"""Tests for automated log maintenance and WAL checkpointing."""

import os
import sqlite3
import time
from pathlib import Path

from model_prediction.log_maintenance import (
    checkpoint_sqlite_databases,
    prune_directory_by_mtime,
    run_maintenance,
    truncate_large_text_file,
)
from model_prediction.runtime_paths import RuntimePaths


def test_prune_directory_by_mtime(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Create fresh file
    fresh = log_dir / "fresh.log"
    fresh.write_text("fresh")

    # Create old file
    old = log_dir / "old.log"
    old.write_text("old")
    past_mtime = time.time() - (20 * 86400)
    os.utime(old, (past_mtime, past_mtime))

    # Prune files older than 14 days
    pruned = prune_directory_by_mtime(log_dir, max_age_days=14, patterns=("*.log",))
    assert "old.log" in pruned
    assert not old.exists()
    assert fresh.exists()


def test_truncate_large_text_file(tmp_path: Path):
    target = tmp_path / "history.jsonl"
    lines = [f"line {i}\n" for i in range(100)]
    target.write_text("".join(lines))

    # File size is small, max_bytes higher -> no truncation
    assert not truncate_large_text_file(target, max_bytes=100_000, keep_lines=10)
    assert len(target.read_text().splitlines()) == 100

    # max_bytes smaller than file size -> truncate to keep_lines
    assert truncate_large_text_file(target, max_bytes=50, keep_lines=10)
    remaining = target.read_text().splitlines()
    assert len(remaining) == 10
    assert remaining[-1] == "line 99"


def test_checkpoint_sqlite_databases(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x INT)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    res = checkpoint_sqlite_databases([db_path])
    assert len(res) == 1
    assert res[0]["status"] == "ok"


def test_run_maintenance(tmp_path: Path):
    paths = RuntimePaths(repo_root=tmp_path, runtime_root=tmp_path / "runtime")
    paths.supervisor_log_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "logs").mkdir(parents=True, exist_ok=True)

    report = run_maintenance(repo_root=tmp_path, paths=paths, max_age_days=14)
    assert "executed_at_utc" in report
    assert "pruned_supervisor_logs" in report
    assert "sqlite_checkpoints" in report
