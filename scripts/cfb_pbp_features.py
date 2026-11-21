"""Per-team-game play-by-play features from the cached ESPN summary payloads (NCAAF v7 research).

The row builder lives in ``model_prediction.features.cfb_pbp_rows`` (shared with the live incremental build);
this script keeps the research batch build, the sanity summary and the CLI, and re-exports the builder.

Reads ``data/research/ncaaf_v7/raw/<season>/<id>.json.gz`` written by ``cfb_espn_pbp_crawl.py`` and
writes one row per (event, team) to ``data/research/ncaaf_v7/team_game_pbp.jsonl``:
offense metrics for the team and the same metrics for its opponent's offense (the team's defense),
each in an all-plays version and a garbage-time-filtered (``_ng``) version.

Definitions (fixed here so later walk-forward work does not drift):

* Scrimmage play: a type in ``SCRIMMAGE_TYPES`` with a start down in 1-4 and a positive distance
  (ESPN's goal-to-go distance 0 is replaced by ``yardsToEndzone``).
  Kickoffs, punts, field goals, PATs, two-point tries, timeouts, period markers and penalty
  no-plays (type "Penalty") are excluded. Unknown types are excluded and counted.
* Offense: ``start.team.id`` of the play, falling back to the drive's team (kickoff rows carry the
  kicking team there, which is why kickoffs are excluded before this matters).
* Success: 1st down gains >= 50% of distance, 2nd >= 70%, 3rd/4th >= 100% (integer arithmetic).
  An offensive touchdown is a success; a turnover never is.
* Explosive: a non-turnover scrimmage play gaining >= 20 yards (one threshold for runs and passes).
* Yards: ``statYardage``; interception plays count 0 offensive yards (``statYardage`` there is the
  return), fumbles lost keep the gain before the fumble.
* Turnovers: scrimmage plays flagged ``isTurnover`` (interceptions, fumbles lost) charged to the offense.
  ESPN sometimes types a lost fumble as a plain Rush without the flag, so this undercounts slightly;
  special-teams fumbles (counted in the box score) are deliberately excluded.
* Garbage time: pre-play |margin| > 38 in Q2, > 28 in Q3, > 22 in Q4; never in Q1 or overtime.
  ESPN's ``homeScore``/``awayScore`` are post-play, so the pre-play margin is the previous row's score.
* Drives: drives with >= 1 scrimmage play by the drive team. Points per drive = the drive team's own
  score change from before the drive's first row to after its last row. A drive is garbage when its
  first scrimmage play is. Seconds per play = summed drive ``timeElapsed`` / scrimmage plays.

Point-in-time: every row carries ``kickoff_utc`` from the event header. A row describes a finished
game, so a decision at time T may use it only when ``kickoff_utc`` is well before T (the game must
also have ended); never use the crawl's ``fetched_at_utc`` as the availability time of the game.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from model_prediction.features.cfb_pbp_rows import (  # noqa: F401  (re-exported for the research scripts/tests)
    EXPLOSIVE_YARDS,
    GARBAGE_MARGIN_BY_PERIOD,
    INTERCEPTION_TYPES,
    NON_SCRIMMAGE_TYPES,
    SCRIMMAGE_TYPES,
    DriveRow,
    PlayRow,
    game_rows,
    is_garbage_time,
    is_success,
    iter_game,
    load_raw,
    offense_stats,
    play_distance,
)

REPO = Path(__file__).resolve().parents[1]
V7_ROOT = REPO / "data" / "research" / "ncaaf_v7"
RAW_ROOT = V7_ROOT / "raw"
OUT_PATH = V7_ROOT / "team_game_pbp.jsonl"
SANITY_PATH = REPO / "outputs" / "ncaaf" / "cfb_pbp_features_sanity.json"


def iter_raw_files(root: Path = RAW_ROOT) -> Iterator[Path]:
    yield from sorted(root.glob("*/*.json.gz"))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build(root: Path = RAW_ROOT, out_path: Path = OUT_PATH) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    unknown: Counter[str] = Counter()
    skipped = 0
    for path in iter_raw_files(root):
        meta, payload = load_raw(path)
        game, unk = game_rows(meta, payload)
        unknown.update(unk)
        if not game:
            skipped += 1
        rows.extend(game)
    rows.sort(key=lambda r: (r["kickoff_utc"] or "", r["event_id"], not r["is_home"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    os.replace(tmp, out_path)
    return summarize(rows, unknown, skipped)


def summarize(rows: list[dict[str, Any]], unknown: Counter[str], skipped: int) -> dict[str, Any]:
    """League sanity numbers plus the box-score cross-check, by game class."""
    by_class: dict[str, dict[str, Any]] = {}
    for cls in sorted({str(r["game_class"]) for r in rows}) + ["ALL"]:
        sub = [r for r in rows if cls == "ALL" or str(r["game_class"]) == cls]
        plays = sum(r["off_plays"] for r in sub)
        plays_ng = sum(r["off_plays_ng"] for r in sub)
        drives = sum(r["off_drives"] for r in sub)
        box = [r for r in sub if r["box_total_yards"] is not None]
        yard_diff = [abs(r["off_yards"] - r["box_total_yards"]) for r in box]
        to_box = [r for r in sub if r["box_turnovers"] is not None]
        by_class[cls] = {
            "team_games": len(sub),
            "success_rate": sum(r["off_successes"] for r in sub) / plays if plays else None,
            "success_rate_ng": sum(r["off_successes_ng"] for r in sub) / plays_ng if plays_ng else None,
            "yards_per_play": sum(r["off_yards"] for r in sub) / plays if plays else None,
            "explosive_rate": sum(r["off_explosives"] for r in sub) / plays if plays else None,
            "points_per_drive": sum(r["off_points"] for r in sub) / drives if drives else None,
            "plays_per_team_game": plays / len(sub) if sub else None,
            "garbage_play_share": 1 - plays_ng / plays if plays else None,
            "sec_per_play": _mean([r["off_sec_per_play"] for r in sub if r["off_sec_per_play"] is not None]),
            "box_yards_abs_diff_median": sorted(yard_diff)[len(yard_diff) // 2] if yard_diff else None,
            "box_yards_within_10_share": (
                sum(d <= 10 for d in yard_diff) / len(yard_diff) if yard_diff else None
            ),
            "box_turnover_match_share": (
                sum(r["off_turnovers"] == r["box_turnovers"] for r in to_box) / len(to_box)
                if to_box
                else None
            ),
            "points_for_vs_drive_points_mean": _mean(
                [r["points_for"] - r["off_points"] for r in sub if r["points_for"] is not None]
            ),
        }
    return {
        "team_game_rows": len(rows),
        "pbp_final_score_match_share": (
            sum(bool(r["pbp_final_score_match"]) for r in rows) / len(rows) if rows else None
        ),
        "events_without_plays": skipped,
        "explosive_definition": f"non-turnover scrimmage play gaining >= {EXPLOSIVE_YARDS} yards",
        "unknown_play_types": dict(unknown.most_common()),
        "by_class": by_class,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--sanity-out", type=Path, default=SANITY_PATH)
    args = parser.parse_args()
    summary = build(args.raw_root, args.out)
    args.sanity_out.parent.mkdir(parents=True, exist_ok=True)
    args.sanity_out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
