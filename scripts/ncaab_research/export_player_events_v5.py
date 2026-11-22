"""Export the 2014-2022 NCAAB event/schedule list for the player-data confirmation path (addendum v5 draft).

``data/research/ncaab_market/scoreboard.jsonl`` starts 2021-11-01, so the player box backfill and the
feature builder have no event list for season years 2014-2021. The confirmation replay already owns one:
``replay_261120.load_confirmation_history()`` = CBBD-converted 2014-15 (ESPN event ids from the local CBBD
cache, no API call) + the ESPN scoreboard backfill 2016-22, first occurrence per event id, non-D1 stubs
demoted. This script writes that list, unchanged in content, in the ``scoreboard.jsonl`` row format the
two pinned scripts read (``{"date": <sb_date>, "events": [...]}``), keeping only the fields they use.

Scores pass through only because the box gate uses them as a checksum (as with scoreboard.jsonl);
nothing here joins an outcome to a feature, line or prediction.

    OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/export_player_events_v5.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ncaab_research" / "confirmation"))

import replay_261120 as rp

OUT_DIR = REPO / "data" / "research" / "ncaab_external" / "player_events_v5"
OUT = OUT_DIR / "events_2014_2022.jsonl"
SEASONS = (2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022)
KEEP = (
    "event_id",
    "season_year",
    "completed",
    "start_utc",
    "neutral",
    "home_team",
    "home_team_id",
    "home_conference_id",
    "home_score",
    "away_team",
    "away_team_id",
    "away_conference_id",
    "away_score",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    events, meta = rp.load_confirmation_history()
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if int(e["season_year"]) not in SEASONS:
            continue
        slim = {k: e.get(k) for k in KEEP}
        slim["event_id"] = str(slim["event_id"])
        slim["season_year"] = int(slim["season_year"])
        by_date[str(e["sb_date"])].append(slim)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as fh:
        for day in sorted(by_date):
            rows = sorted(by_date[day], key=lambda x: (x["start_utc"], x["event_id"]))
            fh.write(json.dumps({"date": day, "events": rows}, sort_keys=True) + "\n")
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for rows in by_date.values():
        for x in rows:
            d1 = x["home_conference_id"] is not None and x["away_conference_id"] is not None
            counts[str(x["season_year"])]["d1_vs_d1" if d1 else "other"] += 1
    info = {
        "output": str(OUT.relative_to(REPO)).replace("\\", "/"),
        "output_sha256": _sha(OUT),
        "source": "replay_261120.load_confirmation_history()",
        "replay_261120_sha256": _sha(REPO / "scripts/ncaab_research/confirmation/replay_261120.py"),
        "backfill_scoreboard_sha256": _sha(rp.BACKFILL_SCOREBOARD),
        "events_by_season": {k: dict(v) for k, v in sorted(counts.items())},
        "non_d1_stubs_demoted": meta["non_d1_stubs_demoted"],
        "cbbd_conversion": meta["cbbd_conversion"],
    }
    (OUT_DIR / "events_2014_2022.meta.json").write_text(
        json.dumps(info, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(info, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
