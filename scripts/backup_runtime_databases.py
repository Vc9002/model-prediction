"""Hot backup of the runtime-root SQLite stores (ops brainstorm #2, 2026-08-17:
"the single most important piece of boring infra not yet present").

Uses sqlite3's online backup API (Connection.backup()) rather than a manual
WAL checkpoint + file copy -- it's designed for exactly this "copy a live,
actively-written database safely" case: it takes its own read transaction
against the source and pages the whole thing across, so a scheduled job
mid-write on the real connection never produces a torn/corrupt copy the way
`cp` on the raw file (or its -wal/-shm siblings) could.

This script only ever OPENS THE SOURCE READ-ONLY (`mode=ro` URI) -- it never
issues a checkpoint or any write against the live databases, so it cannot
contend with or block the daily/production/rebuild-shadow schedulers.

Destination is local by default (`--dest`, default `<runtime_root>/backups`)
-- wiring an actual OFFSITE target (rclone/rsync/cloud bucket) is a follow-up
decision for whoever owns those credentials, not something to guess at here.
This gives you a restorable, timestamped local copy to point that at.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.auto_buyer_lock import auto_buyer_lock_path
from model_prediction.daily_lock import acquire_lock, release_lock
from model_prediction.runtime_paths import RuntimePaths

# The Auto-Buyer's flat files (real-money ledger and state, the paper book, the cycle histories),
# relative to the runtime root. They are not SQLite, so they get dated plain copies in their own
# subdirectory with their own retention: the live and paper ledgers share a file name, which the
# per-stem pruning of the database copies below would mix up.
AUTO_BUYER_BACKUP_FILES = (
    Path("auto_buyer_ledger.jsonl"),
    Path("auto_buyer_state.json"),
    Path("logs") / "auto_buyer_cycle_history.jsonl",
    Path("paper_auto_buyer") / "auto_buyer_ledger.jsonl",
    Path("paper_auto_buyer") / "logs" / "auto_buyer_cycle_history.jsonl",
)
AUTO_BUYER_BACKUP_SUBDIR = "auto_buyer"
AUTO_BUYER_KEEP_DAYS = 30
# Long enough for one buyer cycle to finish its writes; past it the copy goes ahead unlocked.
AUTO_BUYER_LOCK_WAIT_SECONDS = 120.0
_STAMP_DIR = re.compile(r"^\d{8}T\d{6}Z$")


def backup_auto_buyer_files(
    runtime_root: Path,
    dest_dir: Path,
    stamp: str,
    *,
    lock_path: Path | None = None,
    lock_wait_seconds: float = AUTO_BUYER_LOCK_WAIT_SECONDS,
) -> list[dict]:
    """Copy each Auto-Buyer file into ``dest_dir/auto_buyer/<stamp>/`` (same relative path).

    Copy only: each source is opened read-only and never moved, truncated or rewritten. The copy
    runs under auto_buyer.lock when it can be had, so no buyer cycle is mid-write; a busy lock is
    recorded (``locked: false``, status ``ok_unlocked``) and the copy still runs, since a missed
    night is worse than a copy whose last JSONL line may be torn.
    """
    target_root = dest_dir / AUTO_BUYER_BACKUP_SUBDIR / stamp
    lock = acquire_lock(lock_path, wait_seconds=lock_wait_seconds) if lock_path is not None else None
    try:
        results = []
        for relative in AUTO_BUYER_BACKUP_FILES:
            source = runtime_root / relative
            if not source.is_file():
                results.append({"source": str(source), "status": "skipped_missing"})
                continue
            dest_path = target_root / relative
            try:
                data = source.read_bytes()
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with dest_path.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                copied = hashlib.sha256(dest_path.read_bytes()).hexdigest()
                if copied != hashlib.sha256(data).hexdigest():
                    status = "copy_mismatch"
                elif lock_path is not None and lock is None:
                    status = "ok_unlocked"  # a good copy, but a buyer cycle may have been mid-write
                else:
                    status = "ok"
            except OSError as exc:
                results.append({"source": str(source), "status": "failed", "error": str(exc)})
                continue
            results.append(
                {
                    "source": str(source),
                    "dest": str(dest_path),
                    "bytes": len(data),
                    "sha256": copied,
                    "locked": lock is not None,
                    "status": status,
                }
            )
        return results
    finally:
        release_lock(lock)


def prune_auto_buyer_backups(dest_dir: Path, keep_days: int, now: datetime) -> list[str]:
    """Delete dated Auto-Buyer copy directories older than ``keep_days``; never the newest one.

    Age comes from the directory's own stamp, not mtime, and only directories named exactly like a
    stamp are touched, so anything else an operator parks under the folder survives.
    """
    base = dest_dir / AUTO_BUYER_BACKUP_SUBDIR
    if not base.is_dir():
        return []
    dated = []
    for path in base.iterdir():
        if path.is_dir() and _STAMP_DIR.match(path.name):
            dated.append((datetime.strptime(path.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC), path))
    dated.sort()
    cutoff = now - timedelta(days=keep_days)
    pruned = []
    for stamped_at, path in dated[:-1]:
        if stamped_at < cutoff:
            shutil.rmtree(path)
            pruned.append(str(path))
    return pruned


def _backup_one(source: Path, dest_dir: Path, stamp: str) -> dict:
    if not source.exists():
        return {"source": str(source), "status": "skipped_missing"}
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{source.stem}.{stamp}{source.suffix}"
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dest_conn = sqlite3.connect(dest_path)
    try:
        source_conn.backup(dest_conn)
        # backup() copies the source's raw page image, including its file
        # header -- since the live sources run in WAL mode, the destination
        # comes out of backup() ALSO in WAL mode regardless of anything set
        # on dest_conn beforehand (that pragma gets clobbered by the copied
        # header). Only switching AFTER backup() actually sticks, and it's
        # required: WAL mode would otherwise leave a loose *.db-wal sidecar,
        # and the retention pruning below treats one backup as exactly one
        # file -- a stray sidecar with its own mtime could get pruned
        # independently of its .db, silently corrupting the copy on disk.
        dest_conn.execute("PRAGMA journal_mode=DELETE")
        dest_conn.commit()
    finally:
        dest_conn.close()
        source_conn.close()
    stray_wal = dest_path.with_name(dest_path.name + "-wal")
    if stray_wal.exists():
        raise RuntimeError(f"backup left a stray WAL sidecar: {stray_wal}")
    integrity = sqlite3.connect(dest_path).execute("PRAGMA integrity_check").fetchone()[0]
    return {
        "source": str(source),
        "dest": str(dest_path),
        "bytes": dest_path.stat().st_size,
        "integrity_check": integrity,
        "status": "ok" if integrity == "ok" else "integrity_check_failed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest", default=None, help="backup destination dir (default: <runtime_root>/backups)"
    )
    parser.add_argument(
        "--keep", type=int, default=14, help="how many timestamped copies of each db to retain"
    )
    parser.add_argument(
        "--auto-buyer-keep-days",
        type=int,
        default=AUTO_BUYER_KEEP_DAYS,
        help="days of dated Auto-Buyer ledger/state copies to retain",
    )
    args = parser.parse_args()

    paths = RuntimePaths.resolve(require_external_runtime=True)
    dest_dir = Path(args.dest) if args.dest else paths.runtime_root / "backups"
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    sources = [paths.ledgers_db, paths.runs_db, paths.production_db]
    if paths.research_db.exists():
        sources.append(paths.research_db)

    results = [_backup_one(source, dest_dir, stamp) for source in sources]
    results.extend(
        backup_auto_buyer_files(paths.runtime_root, dest_dir, stamp, lock_path=auto_buyer_lock_path(paths))
    )
    for result in results:
        print(result)
    auto_buyer_pruned = prune_auto_buyer_backups(dest_dir, args.auto_buyer_keep_days, now)
    if auto_buyer_pruned:
        print(f"pruned {len(auto_buyer_pruned)} old Auto-Buyer backup dir(s): {auto_buyer_pruned}")

    # Retention: keep the most recent --keep copies per source stem.
    pruned = []
    if dest_dir.exists():
        by_stem: dict[str, list[Path]] = {}
        for path in dest_dir.iterdir():
            if not path.is_file():
                continue
            stem = path.name.split(".")[0]
            by_stem.setdefault(stem, []).append(path)
        for stem, copies in by_stem.items():
            copies.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in copies[args.keep :]:
                stale.unlink()
                pruned.append(str(stale))
    if pruned:
        print(f"pruned {len(pruned)} old backup(s): {pruned}")

    failed = [r for r in results if r.get("status") not in ("ok", "ok_unlocked", "skipped_missing")]
    if failed:
        print(f"FAILED: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
