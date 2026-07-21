"""One-time purge of duplicated Odds-API soccer rows (run AFTER merging the
hash fix in odds_soccer_scores.py, from the project root, against live data).

Root cause: event IDs minted with Python's per-process-randomized ``hash()``
gave the same game a new ID on every daily run, so the same completed match
was re-appended for its whole 3-day lookback window (verified 489 duplicate
rows over 34 distinct games on 2026-07-21).

This script:
1. Backs up each target file to ``<name>.pre-dedupe-<ts>``.
2. Keeps the FIRST occurrence of each (commence_time, home, away) oddsapi row,
   rewrites it with the new deterministic sha1 event id, and drops the rest.
3. Never touches non-oddsapi (ESPN) rows.
4. Applies the same rewrite to data/historical/soccer_games_all.jsonl and,
   when present, data/processed/soccer/games.jsonl.

Usage:  PYTHONPATH=src:. .venv/bin/python scripts/purge_soccer_duplicates.py
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


def _rewritten_id(row: dict) -> str:
    prefix, odds_key, _day, _old = row["event_id"].split(":", 3)
    digest = hashlib.sha1(
        f"{row['home_team']}|{row['away_team']}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{prefix}:{odds_key}:{row['event_start_utc'][:10]}:{digest}"


def purge(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    backup = path.with_name(path.name + f".pre-dedupe-{int(time.time())}")
    backup.write_bytes(path.read_bytes())
    seen: set[tuple] = set()
    kept: list[dict] = []
    removed = 0
    for row in rows:
        event_id = str(row.get("event_id", ""))
        if not event_id.startswith("oddsapi:"):
            kept.append(row)
            continue
        key = (row.get("event_start_utc"), row.get("home_team"), row.get("away_team"))
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        row = dict(row)
        row["event_id"] = _rewritten_id(row)
        kept.append(row)
    with path.open("w", encoding="utf-8") as handle:
        for row in kept:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return {
        "path": str(path),
        "status": "ok",
        "rows_before": len(rows),
        "rows_after": len(kept),
        "duplicates_removed": removed,
        "backup": str(backup),
    }


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    results = [
        purge(root / "data" / "historical" / "soccer_games_all.jsonl"),
        purge(root / "data" / "processed" / "soccer" / "games.jsonl"),
    ]
    print(json.dumps(results, indent=2))
    print(
        "\nNext: re-run validation so soccer evidence reflects clean data:\n"
        "  env PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli "
        "validate-models --sports soccer --write-artifacts"
    )


if __name__ == "__main__":
    main()
