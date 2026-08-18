"""Capture tonight's MLB batting orders. Safe to run repeatedly.

Intended to run HOURLY through the afternoon, not once a day. Lineups are
posted at different times per game, and a capture only sees the games that
have not started yet — the first live run at 23:40Z found 10 of 15 games
already in progress and salvaged 5 decision-grade lineups out of 15.
Running hourly from roughly 15:00Z converts that into full slate coverage.

This is the one MLB input that cannot be backfilled: a completed boxscore
tells you who played, never what was announced before first pitch. A day
not captured is a training row that can never be recovered, which is why
this runs on its own schedule rather than only inside the daily pipeline.

    python scripts/capture_mlb_lineups.py            # today (UTC)
    python scripts/capture_mlb_lineups.py --date 2026-08-19
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.data_sources.mlb_lineups import (
    DEFAULT_LINEUP_ARCHIVE,
    capture_and_store,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--archive", default=DEFAULT_LINEUP_ARCHIVE)
    args = parser.parse_args()

    game_date = args.date or datetime.now(UTC).date().isoformat()
    try:
        summary = capture_and_store(game_date, archive=args.archive)
    except Exception as error:  # noqa: BLE001 - a scheduled capture must not crash-loop
        print(json.dumps({"date": game_date, "status": "error", "error": str(error)}))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
