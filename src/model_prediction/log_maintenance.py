"""Automated log rotation, retention cleanup, and WAL checkpointing.

Prevents unbounded filesystem growth across:
1. Per-run supervisor logs (<runtime_root>/logs/supervisor/*.log, *.metrics.json)
2. Daily pipeline logs (data/logs/daily_*.log)
3. Large JSONL history files (data/logs/auto_buyer_cycle_history.jsonl)
4. Persistent service logs (dashboard/launchd.out.log, dashboard/launchd.err.log)
5. Active SQLite WAL files (runs.db-wal, market_quotes.db-wal, etc.)
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .runtime_paths import RuntimePaths


def prune_directory_by_mtime(
    directory: Path,
    max_age_days: int = 14,
    patterns: tuple[str, ...] = ("*.log", "*.json"),
) -> list[str]:
    """Delete files matching patterns in directory older than max_age_days."""
    if not directory.exists() or not directory.is_dir():
        return []

    cutoff = time.time() - (max_age_days * 86400)
    pruned: list[str] = []

    for pattern in patterns:
        for path in directory.glob(pattern):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    pruned.append(path.name)
            except OSError:
                continue

    return pruned


def truncate_large_text_file(
    path: Path,
    max_bytes: int = 10 * 1024 * 1024,
    keep_lines: int = 20_000,
) -> bool:
    """If file exceeds max_bytes, retain only the latest keep_lines."""
    if not path.exists() or not path.is_file():
        return False

    try:
        if path.stat().st_size <= max_bytes:
            return False

        # Read last keep_lines
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        if len(lines) <= keep_lines:
            return False

        retained = lines[-keep_lines:]
        parent = path.parent
        descriptor, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        os.close(descriptor)

        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.writelines(retained)
            os.replace(temp_path, path)
            return True
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temp_path)
            raise
    except OSError:
        return False


def checkpoint_sqlite_databases(paths: list[Path]) -> list[dict[str, Any]]:
    """Execute PRAGMA wal_checkpoint(PASSIVE) on SQLite databases to truncate WAL files."""
    results: list[dict[str, Any]] = []

    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            conn = sqlite3.connect(path, timeout=5.0)
            try:
                row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                results.append(
                    {
                        "database": str(path),
                        "status": "ok",
                        "busy": row[0] if row else 0,
                        "log_pages": row[1] if row else 0,
                        "checkpointed_pages": row[2] if row else 0,
                    }
                )
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            results.append(
                {
                    "database": str(path),
                    "status": "skipped_or_busy",
                    "error": str(exc),
                }
            )

    return results


def run_maintenance(
    repo_root: Path | str | None = None,
    paths: RuntimePaths | None = None,
    max_age_days: int = 14,
) -> dict[str, Any]:
    """Execute complete log retention and SQLite maintenance."""
    root = Path(repo_root) if repo_root else PROJECT_ROOT
    rt_paths = paths or RuntimePaths.resolve(repo_root=root, require_external_runtime=False)

    report: dict[str, Any] = {
        "executed_at_utc": datetime.now(UTC).isoformat(),
        "max_age_days": max_age_days,
        "pruned_supervisor_logs": [],
        "pruned_daily_logs": [],
        "truncated_files": [],
        "sqlite_checkpoints": [],
    }

    # 1. Prune supervisor logs
    if rt_paths.supervisor_log_root.exists():
        report["pruned_supervisor_logs"] = prune_directory_by_mtime(
            rt_paths.supervisor_log_root,
            max_age_days=max_age_days,
            patterns=("*.log", "*.json"),
        )

    # 2. Prune daily logs in data/logs/
    data_logs = root / "data" / "logs"
    if data_logs.exists():
        report["pruned_daily_logs"] = prune_directory_by_mtime(
            data_logs,
            max_age_days=max_age_days,
            patterns=("daily_*.log",),
        )

    # 3. Truncate large continuous log files
    large_files = [
        data_logs / "auto_buyer_cycle_history.jsonl",
        data_logs / "auto_buyer.log",
        root / "dashboard" / "launchd.out.log",
        root / "dashboard" / "launchd.err.log",
    ]
    for lf in large_files:
        if truncate_large_text_file(lf, max_bytes=10 * 1024 * 1024, keep_lines=20_000):
            report["truncated_files"].append(str(lf))

    # 4. Checkpoint SQLite databases
    databases = [
        rt_paths.runs_db,
        rt_paths.market_quotes_db,
        rt_paths.production_db,
        rt_paths.ledgers_db,
        root / "data" / "games.db",
    ]
    report["sqlite_checkpoints"] = checkpoint_sqlite_databases(databases)

    return report
