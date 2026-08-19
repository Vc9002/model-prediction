"""Backfill model-ledger rows that settlement left open.

Why this exists: `model_ledger.settle_from_pick_row` used to match on the
APPEND-side key, which carries `observed_at_utc`. Only the single row whose
forecast timestamp equalled the settled pick's ever graded, so every
re-forecast row for the same finished game stayed open forever. The forward
fix is in `model_ledger.py`; this repairs the rows already stranded.

Grading is done from the game's FINAL SCORE, not by copying another row's
result. That matters for two cases the pick-propagation path cannot reach:

  * rows at a line that was never staked (a re-forecast can move the line,
    and away +4.5 is a different contract from away +7.5), and
  * events whose picks were all `removed` rather than settled, so no graded
    pick exists to copy from at all.

The script refuses to write unless it can first reproduce EVERY
already-settled row's recorded result from the score. That self-check is the
whole safety argument: if the grading rule were wrong, it would disagree
with the settlements the live pipeline already made.

Dry-run by default; `--apply` writes, after taking a timestamped backup.

    python scripts/backfill_model_ledger_settlement.py \
        --ledger data/model_ledgers/wnba-spread-margin.xlsx \
        --scores data/historical/wnba_games_all.jsonl [--apply]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.model_ledger import ModelLedger


def load_final_scores(path: Path) -> dict[str, tuple[int, int]]:
    """event_id -> (away_score, home_score) for completed games only."""
    scores: dict[str, tuple[int, int]] = {}
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            game = json.loads(line)
            if game.get("status") != "completed":
                continue
            away, home = game.get("away_score"), game.get("home_score")
            if away is None or home is None:
                continue
            scores[str(game["event_id"])] = (int(away), int(home))
    return scores


def grade_spread(selection: str, line: float, away_score: int, home_score: int) -> str:
    """The stored `line` is what the SELECTED side receives (a positive line
    means that side is getting points). Validated against every already-
    settled row in the live WNBA spread ledger."""
    margin = home_score - away_score  # positive => home won by this much
    if selection == "away":
        threshold = line
        return "push" if margin == threshold else ("win" if margin < threshold else "loss")
    threshold = -line
    return "push" if margin == threshold else ("win" if margin > threshold else "loss")


def plan_settlements(
    rows: list[dict[str, str]],
    scores: dict[str, tuple[int, int]],
    staked: dict[tuple[str, str, str], dict[str, str]],
) -> tuple[list[tuple[dict[str, str], str, str | None]], list[dict[str, str]]]:
    """Grade every still-open row with a final score available.

    `row["line"] in (None, "")` is the missingness test on purpose: a
    pick'em spread stores line "0", which is a real contract and must be
    graded, not skipped forever as if absent -- found by code review
    2026-08-19.
    """
    planned: list[tuple[dict[str, str], str, str | None]] = []
    ungradeable: list[dict[str, str]] = []
    for row in rows:
        if row["status"] != "open":
            continue
        score = scores.get(row["event_id"])
        if score is None or row["line"] in (None, ""):
            ungradeable.append(row)
            continue
        result = grade_spread(row["selection"], float(row["line"]), *score)
        source = staked.get((row["event_id"], row["line"], row["selection"]))
        planned.append((row, result, source["pnl_units"] if source else None))
    return planned, ungradeable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    scores = load_final_scores(args.scores)
    ledger = ModelLedger(args.ledger)
    rows = ledger.rows()
    print(f"ledger: {args.ledger}  rows={len(rows)}  completed games known={len(scores)}")

    # --- Self-check: reproduce every already-settled result from the score.
    checked = mismatched = 0
    for row in rows:
        if row["status"] != "settled" or not row["result"]:
            continue
        score = scores.get(row["event_id"])
        if score is None or row["line"] in (None, ""):
            continue
        graded = grade_spread(row["selection"], float(row["line"]), *score)
        checked += 1
        if graded != row["result"]:
            mismatched += 1
            print(
                f"  MISMATCH ev={row['event_id']} sel={row['selection']} line={row['line']} "
                f"score={score[0]}-{score[1]} graded={graded} recorded={row['result']}"
            )
    print(f"self-check: {checked} settled rows reproduced, {mismatched} mismatched")
    if mismatched:
        print("ABORT: grading rule disagrees with the live pipeline's own settlements.")
        return 1
    if not checked:
        print("ABORT: no already-settled rows to validate the grading rule against.")
        return 1

    # Economic fields come from the real staked pick; a line that was never
    # staked has no P&L to inherit and must not have one invented.
    staked: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        if row["status"] == "settled" and row["pnl_units"]:
            staked[(row["event_id"], row["line"], row["selection"])] = row

    planned, ungradeable = plan_settlements(rows, scores, staked)

    print(f"\nto settle: {len(planned)}   still open (no final score yet): {len(ungradeable)}")
    for row, result, pnl in planned:
        away, home = scores[row["event_id"]]
        print(
            f"  ev={row['event_id']} sel={row['selection']:4s} line={row['line']:>5s} "
            f"score={away}-{home} -> {result:5s} pnl={pnl if pnl else '(never staked)'}"
        )

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to write.")
        return 0

    backup = args.ledger.with_suffix(f".xlsx.bak-presettle-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(args.ledger, backup)
    print(f"\nbackup: {backup}")

    for row, result, pnl in planned:
        ledger.settle(
            row["prediction_id"],
            result=result,
            pnl_units=float(pnl) if pnl else None,
        )
    print(f"settled {len(planned)} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
