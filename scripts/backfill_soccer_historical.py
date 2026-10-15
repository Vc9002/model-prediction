"""Backfill soccer historical matches from ESPN for missing dates.

Backfills from 2026-07-20 through 2026-10-15 across all supported soccer leagues.
Saves raw payloads under data/raw/soccer/{date}/scores_{league.lower()}.json
and appends deduplicated completed games to:
  - data/processed/soccer/games.jsonl
  - data/historical/soccer_games_all.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import httpx

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.config import PROJECT_ROOT
from model_prediction.data_sources.espn import SPORT_LEAGUES, ESPNClient
from model_prediction.ingest import PARSER_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_soccer")


def backfill_soccer(
    data_root: Path,
    from_date: str = "2026-07-20",
    to_date: str = "2026-10-15",
    max_workers: int = 16,
) -> dict[str, int]:
    client = ESPNClient()
    leagues = SPORT_LEAGUES["soccer"]
    start_d = date.fromisoformat(from_date)
    end_d = date.fromisoformat(to_date)

    processed_path = data_root / "processed" / "soccer" / "games.jsonl"
    historical_path = data_root / "historical" / "soccer_games_all.jsonl"
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    historical_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing event IDs
    existing_ids: set[str] = set()
    if processed_path.exists():
        with processed_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        existing_ids.add(str(json.loads(line).get("event_id")))
                    except (json.JSONDecodeError, OSError):
                        continue

    logger.info(
        "Starting backfill from %s to %s across %d leagues. Existing games: %d",
        from_date,
        to_date,
        len(leagues),
        len(existing_ids),
    )

    total_days = (end_d - start_d).days + 1
    total_games_appended = 0
    t0 = time.time()

    cursor = start_d
    while cursor <= end_d:
        day_str = cursor.isoformat()
        raw_dir = data_root / "raw" / "soccer" / day_str
        raw_dir.mkdir(parents=True, exist_ok=True)

        def _fetch_league(lg: str, current_raw_dir: Path = raw_dir, current_day: str = day_str) -> list[dict]:
            raw_path = current_raw_dir / f"scores_{lg.lower()}.json"
            if raw_path.exists():
                try:
                    payload = json.loads(raw_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    payload = None
            else:
                payload = None

            if payload is None:
                try:
                    payload = client.scoreboard(lg, current_day)
                    raw_path.write_text(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8",
                    )
                except (httpx.HTTPError, ValueError, OSError):
                    return []

            raw_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()

            fresh = ESPNClient.completed_games(payload)
            for g in fresh:
                g["league"] = lg
                g["raw_source"] = f"espn:{lg}:{current_day}"
                g["raw_hash"] = raw_hash
                g["parser_version"] = PARSER_VERSION
            return fresh

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(_fetch_league, leagues))

        new_games_for_day = []
        for lg_games in results:
            for g in lg_games:
                eid = str(g.get("event_id"))
                if eid and eid not in existing_ids:
                    existing_ids.add(eid)
                    new_games_for_day.append(g)

        if new_games_for_day:
            with (
                processed_path.open("a", encoding="utf-8") as pf,
                historical_path.open("a", encoding="utf-8") as hf,
            ):
                for g in new_games_for_day:
                    line = json.dumps(g, sort_keys=True, separators=(",", ":")) + "\n"
                    pf.write(line)
                    hf.write(line)
            total_games_appended += len(new_games_for_day)

        day_num = (cursor - start_d).days + 1
        if day_num % 10 == 0 or cursor == end_d:
            logger.info(
                "Progress: %d/%d days (%s) | Appended today: %d | Total new: %d (Elapsed: %.1fs)",
                day_num,
                total_days,
                day_str,
                len(new_games_for_day),
                total_games_appended,
                time.time() - t0,
            )

        cursor += timedelta(days=1)

    logger.info(
        "Backfill complete! Total new games appended: %d. Total games now: %d. Time: %.1fs",
        total_games_appended,
        len(existing_ids),
        time.time() - t0,
    )
    return {"new_games_appended": total_games_appended, "total_games": len(existing_ids)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default="2026-07-20")
    parser.add_argument("--to-date", default="2026-10-15")
    parser.add_argument("--data-root", default=str(PROJECT_ROOT / "data"))
    args = parser.parse_args()
    backfill_soccer(Path(args.data_root), args.from_date, args.to_date)
