"""Capture-quality report for the prospective lineup archive.

The number that matters is not how many rows exist, it is whether the rows
that exist are a BIASED sample of games. Late-starting games are the ones
a sleeping machine misses, and they are not exchangeable with early games:
west-coast teams, different rest patterns, different bullpen states. If
7pm ET games are captured 96% of the time and 10pm ET games 61%, a lineup
model trained on this archive inherits that skew silently.

So capture rate is reported stratified by local start-time bucket, and
timing is reported as minutes before first pitch — a lineup captured 8
minutes before a game is a different (and more final) object than one
captured three hours out.

    python scripts/lineup_capture_quality.py
    python scripts/lineup_capture_quality.py --archive path/to/lineups.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.data_sources.mlb_lineups import (
    DEFAULT_LINEUP_ARCHIVE,
    LineupStore,
)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _bucket(start_local: datetime) -> str:
    hour = start_local.hour
    if hour < 16:
        return "before 4pm"
    if hour < 19:
        return "4-7pm"
    if hour < 21:
        return "7-9pm"
    return "9pm+"


def build_report(archive: str | Path) -> dict:
    rows = LineupStore(archive).rows()
    pregame = [r for r in rows if r.get("lineup_state") == "pregame"]

    # One entry per GAME, using its earliest pregame capture: the question
    # is whether the game was captured at all, not how many rows it has.
    by_game: dict[int, dict] = {}
    for row in pregame:
        game_pk = int(row.get("game_pk") or 0)
        first = _parse(row.get("first_observed_at_utc"))
        current = by_game.get(game_pk)
        if current is None or (first and first < current["_first"]):
            by_game[game_pk] = {**row, "_first": first}

    buckets: dict[str, dict] = defaultdict(
        lambda: {"games": 0, "complete": 0, "first_lead": [], "last_lead": []}
    )
    for row in by_game.values():
        start = _parse(row.get("game_start_utc"))
        first = _parse(row.get("first_observed_at_utc"))
        last = _parse(row.get("last_observed_at_utc"))
        if not start:
            continue
        bucket = buckets[_bucket(start.astimezone())]
        bucket["games"] += 1
        if row.get("lineup_complete"):
            bucket["complete"] += 1
        if first:
            bucket["first_lead"].append((start - first).total_seconds() / 60)
        if last:
            bucket["last_lead"].append((start - last).total_seconds() / 60)

    def summarize(values: list[float]) -> dict | None:
        if not values:
            return None
        return {
            "median": round(statistics.median(values), 1),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
        }

    strata = {}
    for name in ("before 4pm", "4-7pm", "7-9pm", "9pm+"):
        data = buckets.get(name)
        if not data or not data["games"]:
            continue
        strata[name] = {
            "games_captured": data["games"],
            "complete_lineups": data["complete"],
            "complete_rate": round(data["complete"] / data["games"], 3),
            "first_capture_minutes_before_start": summarize(data["first_lead"]),
            "last_confirmation_minutes_before_start": summarize(data["last_lead"]),
        }

    return {
        "archive": str(archive),
        "rows_total": len(rows),
        "rows_pregame": len(pregame),
        "distinct_games_captured": len(by_game),
        "note": (
            "Capture RATE needs a denominator only the collector can record "
            "(eligible_pregame_games in its run summary); this report covers "
            "what was captured and how early. Compare strata for skew: a "
            "materially lower complete_rate or shorter lead time in the 9pm+ "
            "bucket is the overnight-sleep signature."
        ),
        "by_local_start_time": strata,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default=DEFAULT_LINEUP_ARCHIVE)
    args = parser.parse_args()
    print(json.dumps(build_report(args.archive), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
