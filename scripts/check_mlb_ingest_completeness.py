"""Detect MLB games ESPN listed but the ingest never recorded (P1-12).

For each date in a window (ET game dates), fetch ESPN's MLB scoreboard
and compare its event ids against the games file's event ids for that
date. Reports missing events — the historical evidence behind P1-12's
"MLB ingest intermittently misses games" (an intermittent ESPN API
issue with no detector until now).

Read-only: performs GETs, writes nothing except stdout + an optional
JSON report. Rate-limits 1.2s between requests like the ingest does.

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/check_mlb_ingest_completeness.py \
        --days 7 [--json-output outputs/latest/mlb_ingest_completeness.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.config import PROJECT_ROOT  # noqa: E402
from model_prediction.data_sources.espn import ESPNClient  # noqa: E402

GAMES_PATH = PROJECT_ROOT / "data" / "historical" / "mlb_games_all.jsonl"


def _ingested_ids_by_date() -> dict[str, set[str]]:
    by_date: dict[str, set[str]] = {}
    for line in GAMES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            game = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_start = str(game.get("event_start_utc") or "")
        if not event_start:
            continue
        # ET game date, matching the walk-forward convention.
        day = (
            datetime.fromisoformat(event_start)
            .astimezone(ZoneInfo("America/New_York"))
            .date()
            .isoformat()
        )
        by_date.setdefault(day, set()).add(str(game.get("event_id") or ""))
    return by_date


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json-output", default=None)
    args = parser.parse_args()

    client = ESPNClient()
    ingested = _ingested_ids_by_date()
    report: dict = {"generated_at_utc": datetime.now(UTC).isoformat(), "days": []}

    today = datetime.now(UTC).date()
    missing_total = 0
    # Start at yesterday: today's games are still in progress, so their
    # absence from the games file is expected, not a miss.
    for offset in range(1, args.days + 1):
        day = (today - timedelta(days=offset)).isoformat()
        time.sleep(1.2)
        try:
            payload = client.scoreboard("MLB", day)
        except Exception as error:  # noqa: BLE001 — detector must report per-day provider failures, not crash the scan
            print(f"{day}: provider error ({type(error).__name__})")
            report["days"].append({"date": day, "provider_error": str(error)[:120]})
            continue
        listed = {
            str(event.get("id") or "")
            for event in (payload.get("events") or [])
        }
        have = ingested.get(day, set())
        missing = sorted(listed - have)
        missing_total += len(missing)
        print(f"{day}: ESPN listed {len(listed)}, ingested {len(listed & have)}, "
              f"missing {len(missing)}" + (f" -> {missing}" if missing else ""))
        report["days"].append(
            {"date": day, "listed": len(listed), "ingested": len(listed & have),
             "missing": missing}
        )

    print(f"\ntotal missing across {args.days} days: {missing_total}")
    if args.json_output:
        out = PROJECT_ROOT / args.json_output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
