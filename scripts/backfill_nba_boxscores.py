"""Backfill NBA ESPN boxscore captures for regular-season games.

Mirrors the WNBA boxscore capture shape consumed by
``features/wnba_boxscores.py`` / ``features/wnba_player_logs.py`` so the
same parsing logic can be reused for NBA: each file is
``{event_id, payload: <raw ESPN summary JSON>}`` under
``data/availability/nba/espn_boxscores/<event_id>.json``.

Read-only research backfill -- rate-limited, resumable (skips files already
on disk), never overwrites a game whose file already exists.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.data_sources.espn import ESPNClient

GAMES_FILE = Path("data/historical/nba_games_all.jsonl")
OUT_DIR = Path("data/availability/nba/espn_boxscores")
SLEEP_SECONDS = 0.35


def load_regular_season_event_ids() -> list[str]:
    event_ids: list[str] = []
    with GAMES_FILE.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("season_type") == "regular-season" and row.get("status") == "completed":
                event_ids.append(str(row["event_id"]))
    return event_ids


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    event_ids = load_regular_season_event_ids()
    client = ESPNClient()
    fetched = 0
    skipped = 0
    failed = 0
    for i, event_id in enumerate(event_ids):
        out_path = OUT_DIR / f"{event_id}.json"
        if out_path.exists():
            skipped += 1
            continue
        try:
            payload = client.summary("NBA", event_id)
        except Exception as exc:  # noqa: BLE001 -- best-effort research backfill
            failed += 1
            print(f"[{i + 1}/{len(event_ids)}] FAIL {event_id}: {exc}", file=sys.stderr)
            time.sleep(SLEEP_SECONDS)
            continue
        out_path.write_text(
            json.dumps({"event_id": event_id, "payload": payload}),
            encoding="utf-8",
        )
        fetched += 1
        if fetched % 100 == 0:
            print(f"[{i + 1}/{len(event_ids)}] fetched={fetched} skipped={skipped} failed={failed}")
        time.sleep(SLEEP_SECONDS)
    print(f"done: fetched={fetched} skipped={skipped} failed={failed} total={len(event_ids)}")


if __name__ == "__main__":
    main()
