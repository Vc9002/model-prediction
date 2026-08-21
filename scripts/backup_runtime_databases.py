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
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.runtime_paths import RuntimePaths


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
    args = parser.parse_args()

    paths = RuntimePaths.resolve(require_external_runtime=True)
    dest_dir = Path(args.dest) if args.dest else paths.runtime_root / "backups"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    sources = [paths.ledgers_db, paths.runs_db, paths.production_db]
    if paths.research_db.exists():
        sources.append(paths.research_db)

    results = [_backup_one(source, dest_dir, stamp) for source in sources]
    for result in results:
        print(result)

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

    failed = [r for r in results if r.get("status") not in ("ok", "skipped_missing")]
    if failed:
        print(f"FAILED: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
