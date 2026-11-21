"""Backfill NCAAB possession components (FGA, FTA, OREB, TOV) for past seasons into a RESEARCH tree.

``scripts/backfill_ncaab_boxscores.py`` covers 2025-26 onward from ``ncaab_games_all.jsonl``; the
archived scoreboard (``data/research/ncaab_market/scoreboard.jsonl``, 2021-22 onward) carries FGA
and FTA but not offensive rebounds or turnovers, so per-possession ratings for 2021-22..2024-25
need the ESPN game summary of every final.

Differences from the serving-side backfill, on purpose:
- Output goes to ``data/research/ncaab_external/espn_boxscores/<date>/<event_id>.json``, NOT
  ``data/features/ncaab/boxscores``: ``NCAABPossessionStore`` loads every file in that tree for the
  served v6/v7 models, so ~22k historical files there would slow the daily run. Research reads this
  tree with ``NCAABPossessionStore(root=...)`` (same ``ncaab-boxscore-components-v1`` schema, parsed by
  the unchanged ``parse_completed_boxscore``).
- Events come from the archived scoreboard, not a fresh scoreboard fetch per date.
- Plain httpx, one request at a time, no in-memory response cache (``ESPNClient`` never evicts, and
  tens of thousands of summaries in one process would exhaust host memory).

Resumable and append-only: an existing file is never rewritten; every attempt appends one line to
``_progress.jsonl`` in the output root. Terminal box scores, captured long after the games: history
features only, read strictly before the decision cutoff.

Usage:
    .venv/Scripts/python scripts/backfill_ncaab_boxscores_history.py [--seasons 2022 2023 2024 2025]
        [--pause 0.6] [--include-non-d1] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from model_prediction.data_sources.espn import LEAGUE_PATHS, SITE_API
from model_prediction.data_sources.ncaab_boxscores import parse_completed_boxscore

SCOREBOARD = ROOT / "data" / "research" / "ncaab_market" / "scoreboard.jsonl"
OUT_ROOT = ROOT / "data" / "research" / "ncaab_external" / "espn_boxscores"
SUMMARY_URL = f"{SITE_API}/{LEAGUE_PATHS['NCAAB']}/summary"
USER_AGENT = "Mozilla/5.0 (compatible; research-data-backfill/1.0)"  # generic, no personal details
MAX_CONSECUTIVE_FAILURES = 25  # abort rather than hammer a provider that is refusing us


def iter_events(seasons: set[int], include_non_d1: bool) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    with SCOREBOARD.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            for event in row.get("events") or []:
                if event.get("season_year") not in seasons or not event.get("completed"):
                    continue
                # D1 game = both teams carry an ESPN conference id (non-D1 opponents have none).
                d1 = (
                    event.get("home_conference_id") is not None
                    and event.get("away_conference_id") is not None
                )
                if not d1 and not include_non_d1:
                    continue
                event_id = str(event["event_id"])
                if event_id in seen:
                    continue
                seen.add(event_id)
                game = {
                    "event_id": event_id,
                    "status": "completed",
                    "event_start_utc": event["start_utc"],
                    "home_team_id": event["home_team_id"],
                    "away_team_id": event["away_team_id"],
                    "home_team": event["home_team"],
                    "away_team": event["away_team"],
                    "home_score": event["home_score"],
                    "away_score": event["away_score"],
                }
                out.append((str(row["date"]), game))
    return out


def fetch_summary(client: httpx.Client, event_id: str) -> dict[str, Any]:
    delay = 5.0
    for attempt in range(5):
        response = client.get(SUMMARY_URL, params={"event": event_id})
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 4:
                response.raise_for_status()
            time.sleep(delay)
            delay *= 2
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("unreachable")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, nargs="+", default=[2022, 2023, 2024, 2025])
    ap.add_argument("--pause", type=float, default=0.6, help="seconds between requests")
    ap.add_argument("--include-non-d1", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after N fetch attempts (pilot runs)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    events = iter_events(set(args.seasons), args.include_non_d1)
    todo = [(day, game) for day, game in events if not (OUT_ROOT / day / f"{game['event_id']}.json").exists()]
    print(
        f"{len(events)} events in scope, {len(events) - len(todo)} on disk, {len(todo)} to fetch", flush=True
    )
    if args.dry_run:
        return
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    progress = OUT_ROOT / "_progress.jsonl"
    tally: Counter[str] = Counter()
    consecutive_failures = 0
    started = time.time()
    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}) as client:
        for i, (day, game) in enumerate(todo, 1):
            if args.limit and i > args.limit:
                break
            path = OUT_ROOT / day / f"{game['event_id']}.json"
            status, reason = "captured", None
            try:
                summary = fetch_summary(client, game["event_id"])
                observed = datetime.now(UTC).isoformat()
                record = parse_completed_boxscore(game, summary, observed)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as handle:
                    json.dump(record, handle, sort_keys=True, allow_nan=False)
                    handle.write("\n")
                consecutive_failures = 0
            except FileExistsError:
                status = "existing"
            except (KeyError, TypeError, ValueError) as exc:
                # Fetched fine but the components are absent: a data gap, not a provider fault.
                status, reason = "missing", str(exc)
                consecutive_failures = 0
            except (httpx.HTTPError, OSError) as exc:
                status, reason = "error", f"{type(exc).__name__}: {exc}"
                consecutive_failures += 1
            tally[status] += 1
            with progress.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "at_utc": datetime.now(UTC).isoformat(),
                            "date": day,
                            "event_id": game["event_id"],
                            "status": status,
                            "reason": reason,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            if i % 200 == 0:
                rate = i / max(time.time() - started, 1e-9)
                print(f"[{i}/{len(todo)}] {dict(tally)} {rate:.2f}/s", flush=True)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(
                    f"aborting after {consecutive_failures} consecutive provider errors: {reason}", flush=True
                )
                break
            time.sleep(args.pause)
    print(f"done in {time.time() - started:.0f}s: {dict(tally)}", flush=True)


if __name__ == "__main__":
    main()
