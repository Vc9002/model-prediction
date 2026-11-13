"""Backfill NCAAB team box-score components for every completed history date.

The daily job captures the previous day's finals (``cli/daily.py`` -> ``capture_completed_boxscores``)
and has done so since 2026-11-03; the 2025-26 season and 2026-11-02 were never captured, so the
possession features have no history to learn from. This walks every ET date with completed games
in ``data/historical/ncaab_games_all.jsonl`` and runs the same first-seen capture per date:
existing files are never rewritten, each record carries its own ``observed_at_utc`` (the capture
time, i.e. long after the game -- these are terminal box scores, not point-in-time snapshots, and
any feature built on them must only read games that precede the decision cutoff).

Polite to ESPN: a few workers per date and a pause between dates. Resumable: rerun to fill gaps.

Usage:
    env PYTHONPATH=src:. python scripts/backfill_ncaab_boxscores.py [--start 2025-11-03] [--end 2026-11-02]
        [--workers 3] [--pause 0.5] [--dry-run]
Writes a per-date summary to outputs/ncaab/boxscore_backfill.jsonl (append) and prints a tally.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "src")

from model_prediction.data_sources.espn import ESPNClient
from model_prediction.data_sources.ncaab_boxscores import capture_completed_boxscores

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "outputs" / "ncaab" / "boxscore_backfill.jsonl"
EASTERN = ZoneInfo("America/New_York")


def completed_dates(start: str, end: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    with (DATA / "historical" / "ncaab_games_all.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") != "completed":
                continue
            day = datetime.fromisoformat(row["event_start_utc"]).astimezone(EASTERN)
            key = day.date().isoformat()
            if start <= key <= end:
                counts[key] += 1
    return dict(sorted(counts.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-11-03")
    ap.add_argument("--end", default="2026-11-02")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--pause", type=float, default=0.5, help="seconds between dates")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dates = completed_dates(args.start, args.end)
    root = DATA / "features" / "ncaab" / "boxscores"
    todo = {d: n for d, n in dates.items() if len(list((root / d).glob("*.json"))) < n}
    print(
        f"{len(dates)} dates with completed games, {sum(dates.values())} games; {len(todo)} dates incomplete"
    )
    if args.dry_run:
        for d, n in todo.items():
            print(d, n, "on disk:", len(list((root / d).glob("*.json"))))
        return
    client = ESPNClient()
    tally: Counter[str] = Counter()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    for i, (day, expected) in enumerate(todo.items(), 1):
        try:
            result = capture_completed_boxscores(DATA, day, client=client, max_workers=args.workers)
        except Exception as exc:  # noqa: BLE001 -- one bad date must not stop a 150-date backfill
            result = {
                "date": day,
                "error": f"{type(exc).__name__}: {exc}",
                "captured": 0,
                "existing": 0,
                "missing": [],
            }
        result["expected_from_history"] = expected
        result["backfilled_at_utc"] = datetime.now(UTC).isoformat()
        with OUT.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, sort_keys=True) + "\n")
        tally["captured"] += int(result.get("captured", 0))
        tally["existing"] += int(result.get("existing", 0))
        tally["missing"] += len(result.get("missing", []))
        tally["errors"] += 1 if "error" in result else 0
        print(
            f"[{i}/{len(todo)}] {day}: completed {result.get('completed', '?')} captured {result.get('captured', 0)} "
            f"existing {result.get('existing', 0)} missing {len(result.get('missing', []))}"
            + (f" ERROR {result['error']}" if "error" in result else ""),
            flush=True,
        )
        time.sleep(args.pause)
    print(f"done in {time.time() - started:.0f}s: {dict(tally)}")


if __name__ == "__main__":
    main()
