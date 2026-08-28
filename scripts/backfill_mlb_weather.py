"""Backfill raw historical weather for MLB venues from Open-Meteo's archive.

Stores RAW hourly observations (temperature, humidity, dew point, pressure,
precipitation, wind speed/gust/direction), one JSONL line per (venue, date) --
never a pre-collapsed factor -- so feature builders can derive
park-relative wind vectors, temperature effects, etc. later without
re-fetching. See docs/ROADMAP.md's data-expansion backlog.

Idempotent and resumable: lines already present for a (venue, date) are
skipped, so an interrupted run can just be re-run. The cache lives under
``data/weather/`` -- untracked operational output, same as the other
``data/`` trees (CLAUDE.md consolidation K).

Venues are resolved through ``features.mlb_venue_geocoding`` (venue-name
keyed, relocation-correct); the deliberately-unsourced one-off venues
(Tokyo Dome, London Stadium, etc.) are skipped, not guessed.

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/backfill_mlb_weather.py \
        [--snapshots data/mlb_statsapi/game_snapshots.jsonl] \
        [--out data/weather/mlb_historical.jsonl] \
        [--workers 8] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from model_prediction.domain import parse_utc
from model_prediction.features.mlb_venue_geocoding import venue_location

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# Raw variables per the backlog spec: store the source, collapse later.
HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "surface_pressure",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
)


def _load_venue_dates(snapshot_path: Path) -> list[tuple[str, str]]:
    """Distinct (venue_name, game date) pairs from real completed snapshots.

    A game with no first-inning box score yet (scheduled/cancelled) is
    skipped: this backfill exists for training features, which only ever
    see completed games.
    """
    pairs: set[tuple[str, str]] = set()
    with snapshot_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            if snap.get("status") not in (
                "Final",
                "Completed Early",
                "Completed Early: Rain",
                "Completed Early: Wet Grounds",
            ):
                continue
            venue = snap.get("venue_name") or ""
            if not venue:
                continue
            try:
                day = parse_utc(str(snap["game_start_utc"])).date().isoformat()
            except (KeyError, ValueError):
                continue
            pairs.add((venue, day))
    return sorted(pairs)


def _already_fetched(out_path: Path) -> set[tuple[str, str]]:
    if not out_path.exists():
        return set()
    fetched: set[tuple[str, str]] = set()
    with out_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") == "ok" and row.get("venue_name") and row.get("date"):
                fetched.add((row["venue_name"], row["date"]))
    return fetched


def fetch_one(venue: str, day: str) -> dict:
    loc = venue_location(venue)
    if loc is None:
        return {"venue_name": venue, "date": day, "status": "unsourced_venue"}
    url = (
        f"{ARCHIVE_URL}?latitude={loc.latitude}&longitude={loc.longitude}"
        f"&start_date={day}&end_date={day}"
        f"&hourly={','.join(HOURLY_VARIABLES)}"
        "&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=UTC"
    )
    for attempt in range(3):
        try:
            resp = httpx.get(url, timeout=20)
            if resp.status_code == 200:
                hourly = resp.json().get("hourly") or {}
                if not hourly.get("time"):
                    return {"venue_name": venue, "date": day, "status": "no_data"}
                return {
                    "venue_name": venue,
                    "date": day,
                    "latitude": loc.latitude,
                    "longitude": loc.longitude,
                    "elevation_ft": loc.elevation_ft,
                    "timezone": loc.timezone,
                    "roof": loc.roof,
                    "hourly": hourly,
                    "status": "ok",
                    "observed_fetched_at_utc": datetime.now(UTC).isoformat(),
                }
            if resp.status_code == 429 and attempt < 2:
                # Open-Meteo throttles bursty archive traffic; back off and
                # retry rather than recording a permanent failure the resume
                # pass would have to rediscover anyway.
                time.sleep(3.0 * (attempt + 1))
                continue
            return {"venue_name": venue, "date": day, "status": f"http_{resp.status_code}"}
        except (httpx.HTTPError, ValueError) as exc:
            if attempt == 2:
                return {"venue_name": venue, "date": day, "status": f"error_{type(exc).__name__}"}
            time.sleep(1.0 * (attempt + 1))
    return {"venue_name": venue, "date": day, "status": "unreachable"}  # pragma: no cover


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", default="data/mlb_statsapi/game_snapshots.jsonl")
    parser.add_argument("--out", default="data/weather/mlb_historical.jsonl")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="0 = all")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pairs = _load_venue_dates(Path(args.snapshots))
    fetched = _already_fetched(out_path)
    pending = [p for p in pairs if p not in fetched]
    if args.limit:
        pending = pending[: args.limit]
    print(f"venue-dates total={len(pairs)} already_fetched={len(fetched)} pending={len(pending)}")

    done = 0
    failures = 0
    with out_path.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, venue, day): (venue, day) for venue, day in pending}
        for future in as_completed(futures):
            row = future.result()
            if row["status"] != "ok":
                failures += 1
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            done += 1
            if done % 200 == 0 or done == len(pending):
                print(f"  done={done}/{len(pending)} failures={failures}")
    print(f"finished: {done} written, {failures} non-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
