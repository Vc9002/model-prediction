"""Backfill raw_source/raw_hash/parser_version onto historical MLB games rows.

The daily ingest has written provenance fields since 2026-08-14, but the
8,131 pre-existing rows in mlb_games_all.jsonl carry none. The raw ESPN
snapshots they came from are still on disk (data/raw/mlb/<date>/
scores_mlb.json), so the provenance is reconstructible — this matches
every unprovenanced row to the EARLIEST snapshot containing its event_id
(ESPN scoreboards cover a rolling window, so later snapshots re-list
older events) and stamps the ingest convention verbatim:

    raw_source      = f"espn:MLB:{snapshot_date}"
    raw_hash        = sha256 of the canonical snapshot payload
    parser_version  = PARSER_VERSION (current)

Event order and every other field are preserved byte-for-byte; rows with
no matching snapshot stay unprovenanced (honest gap, not fabricated).

Safety: the script takes the same lock the daily pipeline uses
(runtime-root locks/daily.lock) so it can never race a live ingest, and
writes the new file via temp+rename.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.config import PROJECT_ROOT
from model_prediction.ingest import PARSER_VERSION

GAMES_PATH = PROJECT_ROOT / "data" / "historical" / "mlb_games_all.jsonl"
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "mlb"
LOCK_PATH = (
    Path(os.environ.get("MODEL_PREDICTION_RUNTIME_ROOT", str(PROJECT_ROOT / "data"))) / "locks" / "daily.lock"
)


def _snapshot_index() -> dict[str, tuple[str, str]]:
    """event_id -> (raw_source, raw_hash) from the raw snapshot cache."""
    index: dict[str, tuple[str, str]] = {}
    if not RAW_ROOT.is_dir():
        return index
    for snapshot_dir in sorted(RAW_ROOT.iterdir()):
        snapshot = snapshot_dir / "scores_mlb.json"
        if not snapshot.is_file():
            continue
        try:
            payload_text = snapshot.read_text(encoding="utf-8")
            payload = json.loads(payload_text)
        except (OSError, json.JSONDecodeError):
            continue
        # The cache file IS the canonical payload dump (sort_keys +
        # compact separators), so its sha256 equals the ingest's raw_hash.
        raw_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for event in payload.get("events") or []:
            event_id = str(event.get("id") or "")
            if event_id and event_id not in index:  # earliest wins
                index[event_id] = (f"espn:MLB:{snapshot_dir.name}", raw_hash)
    return index


def main() -> int:
    lock_path_parent = LOCK_PATH.parent
    lock_path_parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_PATH.open("a+", encoding="utf-8")
    fcntl.flock(lock, fcntl.LOCK_EX)  # block until the daily cycle is done
    try:
        index = _snapshot_index()
        print(f"snapshot index: {len(index)} events")

        rows = []
        for line in GAMES_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(row)

        filled = skipped = 0
        for row in rows:
            if row.get("raw_source"):
                skipped += 1
                continue
            provenance = index.get(str(row.get("event_id") or ""))
            if provenance:
                row["raw_source"], row["raw_hash"] = provenance
                row["parser_version"] = PARSER_VERSION
                filled += 1
        print(
            f"rows: {len(rows)} | filled: {filled} | already had provenance: {skipped} "
            f"| still unmatched: {len(rows) - filled - skipped}"
        )

        handle, temp_name = tempfile.mkstemp(prefix=".mlb_games_all.jsonl.", dir=GAMES_PATH.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_name, GAMES_PATH)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name)
            raise
        print(f"wrote {GAMES_PATH}")
        return 0
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
