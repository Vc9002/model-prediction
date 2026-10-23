"""Log Maintenance, Compaction & Disk Hygiene.

Prevents unbounded log growth, disk bloat, and file lock contention during 24/7
unattended background execution.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from .runtime_paths import RuntimePaths

logger = logging.getLogger("model_prediction.maintenance")
EASTERN = ZoneInfo("America/New_York")


def prune_stale_logs(
    logs_dir: Path | str | None = None,
    max_age_days: int = 14,
    max_file_size_mb: float = 10.0,
    *,
    delete_all_old: bool = False,
) -> dict[str, int]:
    """Prune daily logs older than max_age_days and truncate oversized active log files."""
    if logs_dir is None:
        paths = RuntimePaths.resolve(require_external_runtime=False)
        logs_dir = paths.data_root / "logs"
    dir_path = Path(logs_dir)
    if not dir_path.exists():
        return {"deleted_files": 0, "truncated_files": 0}

    cutoff_sec = time.time() - (max_age_days * 86400)
    max_bytes = int(max_file_size_mb * 1024 * 1024)

    deleted_count = 0
    truncated_count = 0

    for file_path in dir_path.glob("*.log"):
        try:
            stat = file_path.stat()
            # If older than cutoff and is a dated log (e.g. daily_2026-10-04.log), delete it
            if stat.st_mtime < cutoff_sec and (delete_all_old or file_path.name.startswith("daily_")):
                file_path.unlink()
                deleted_count += 1
                continue

            # If oversized, truncate keeping last 5,000 lines
            if stat.st_size > max_bytes:
                lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if len(lines) > 5000:
                    trimmed = lines[-5000:]
                    file_path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
                    truncated_count += 1
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Failed maintenance for %s: %s", file_path.name, exc)

    return {"deleted_files": deleted_count, "truncated_files": truncated_count}


def compact_sqlite_databases(data_dir: Path | str | None = None) -> list[str]:
    """Execute WAL checkpoint and VACUUM on any SQLite stores to recover free pages."""
    if data_dir is None:
        paths = RuntimePaths.resolve(require_external_runtime=False)
        data_dir = paths.data_root
    data_path = Path(data_dir)
    compacted: list[str] = []

    for db_path in data_path.glob("*.db"):
        conn = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.execute("VACUUM;")
            compacted.append(db_path.name)
        except (sqlite3.Error, OSError) as exc:
            logger.debug("Compaction skipped for %s: %s", db_path.name, exc)
        finally:
            if conn is not None:
                conn.close()

    return compacted


def run_maintenance(
    repo_root: Path | str,
    paths: RuntimePaths,
    max_age_days: int = 14,
) -> dict[str, object]:
    """Run the daily runner's log and SQLite maintenance against its runtime."""
    repo_logs = Path(repo_root) / "data" / "logs"
    return {
        "daily_logs": prune_stale_logs(repo_logs, max_age_days=max_age_days),
        "runtime_logs": prune_stale_logs(
            paths.supervisor_log_root, max_age_days=max_age_days, delete_all_old=True
        ),
        "compacted_databases": compact_sqlite_databases(paths.runtime_root),
    }
