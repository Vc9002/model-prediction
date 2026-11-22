"""Build the frozen prior-seasons archive the NCAAB v7 serving path reads (quick 261115-v7s).

Why it exists: v7's gain comes from three seasons of history plus per-season ESPN conference ids, but the live
ledger history only holds 2025-26 onward. The research scoreboard (``data/research/ncaab_market``) is
untracked, so the seasons the live path lacks are frozen here into ``data/archive/`` (tracked evidence tree,
unlike ``data/historical/`` which is gitignored).

Output: ``data/archive/ncaab_v7_prior_seasons.jsonl.gz``, deterministic (sorted, gzip mtime 0). Line 1 is a
``_meta`` record (season cut, per-season conference ids, games sha256); every other line is one completed game
with the box aggregates ``NCAABScoreboardStats`` reads (ranks, neutral flag, shot stats) so the v6 components
inside v7 see exactly what the research replay saw.

Usage (repo root): ``PYTHONPATH=src:scripts .venv/Scripts/python scripts/build_ncaab_v7_serving_archive.py``
Re-run after the 2026-27 season fills in to refresh the 2026-27 conference map; games stay season_year <= 2025.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import ncaab_residual as nr

from model_prediction.models.ncaab_v7_multiseason import build_asof_conferences

OUT = REPO / "data" / "archive" / "ncaab_v7_prior_seasons.jsonl.gz"
MAX_ARCHIVED_SEASON_YEAR = 2025  # 2024-25; 2025-26 onward is served from the live history
STAT_FIELDS = (
    "fieldGoalsAttempted",
    "fieldGoalsMade",
    "threePointFieldGoalsAttempted",
    "threePointFieldGoalsMade",
    "freeThrowsAttempted",
    "freeThrowsMade",
    "rebounds",
    "assists",
)
GAME_KEYS = (
    "event_id",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "neutral",
    "season_year",
    "season_type",
    "competition_type",
    "conference_competition",
    "home_conference_id",
    "away_conference_id",
    "home_ap_rank",
    "away_ap_rank",
)


def game_row(e: dict[str, Any]) -> dict[str, Any]:
    row = {k: e.get(k) for k in GAME_KEYS}
    row["event_start_utc"] = e["start_utc"]
    for side in ("home", "away"):
        for f in STAT_FIELDS:
            v = e.get(f"{side}_{f}")
            row[f"{side}_{f}"] = int(v) if v is not None else None
    return row


def build() -> tuple[bytes, dict[str, Any]]:
    events = nr.load_scoreboard_events()
    games = [game_row(e) for e in events if int(e["season_year"]) <= MAX_ARCHIVED_SEASON_YEAR]
    games.sort(key=lambda r: (r["event_start_utc"], r["event_id"]))
    body = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in games)
    conferences = build_asof_conferences(events)  # raises on a team with two ids in one season
    meta = {
        "_meta": {
            "version": 1,
            "max_archived_season_year": MAX_ARCHIVED_SEASON_YEAR,
            "n_games": len(games),
            "games_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "conferences_by_season_year": {
                str(s): dict(sorted(m.items())) for s, m in sorted(conferences.items())
            },
        }
    }
    head = json.dumps(meta, sort_keys=True, separators=(",", ":")) + "\n"
    return (head + body).encode("utf-8"), meta["_meta"]


def main() -> int:
    payload, meta = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(payload)
    print(f"wrote {OUT} games={meta['n_games']} seasons={sorted(meta['conferences_by_season_year'])}")
    print(f"games_sha256={meta['games_sha256']} bytes={OUT.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
