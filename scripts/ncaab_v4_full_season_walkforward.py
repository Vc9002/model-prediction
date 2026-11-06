"""Full-season walk-forward: ncaab-tempo-v3 (incumbent) vs. ncaab-spread-v4/
ncaab-total-v4 (dedicated classes) on EVERY settled NCAAB game in
data/historical/ncaab_games_all.jsonl, not just the narrow early-November
window scripts/ncaab_v4_walkforward.py was limited to.

Research-only: no ledger writes, no orders. Point-in-time correct -- each
game is predicted using only games strictly before it; history is extended
after grading, never before.
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import ncaab_v3_model
from model_prediction.models.ncaab_spread_v4 import NCAABSpreadModelV4
from model_prediction.models.ncaab_total_v4 import NCAABTotalModelV4

ROOT = Path(".")
WARMUP_DAYS = 14  # minimal warmup so TrendEngine has some signal before grading starts


def _load_games() -> list[dict]:
    with open(ROOT / "data/historical/ncaab_games_all.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    rows.sort(key=lambda r: r["event_start_utc"])
    return [r for r in rows if r.get("status") == "completed" and r.get("home_score") is not None]


def main() -> None:
    games = _load_games()
    print(f"total completed games: {len(games)}", flush=True)

    v3 = ncaab_v3_model()
    v4_spread = NCAABSpreadModelV4()
    v4_total = NCAABTotalModelV4()

    by_day: dict[str, list[dict]] = defaultdict(list)
    for g in games:
        by_day[g["event_start_utc"][:10]].append(g)
    days = sorted(by_day.keys())
    grade_from_day = days[WARMUP_DAYS] if len(days) > WARMUP_DAYS else days[0]
    print(f"grading from {grade_from_day} onward ({WARMUP_DAYS}-day warmup)", flush=True)

    total_errs: dict[str, list[float]] = {"v3": [], "v4": []}
    moneyline_briers: dict[str, list[float]] = {"v3": [], "v4": []}
    # Bucket by current-season sample thinness (the exact dimension the
    # 2026-11-05 incident and its fix are about).
    bucket_total_errs: dict[str, dict[str, list[float]]] = {
        "early_season": {"v3": [], "v4": []},
        "in_season": {"v3": [], "v4": []},
    }
    history_so_far: list[GameRecord] = []
    graded = 0
    t0 = time.time()

    for day_idx, day in enumerate(days):
        day_games = by_day[day]
        if day >= grade_from_day:
            for g in day_games:
                actual_total = g["away_score"] + g["home_score"]
                home_won = 1.0 if g["home_score"] > g["away_score"] else 0.0

                upcoming = UpcomingGame(
                    event_id=g["event_id"],
                    event_start_utc=g["event_start_utc"],
                    away_team=g["away_team"],
                    home_team=g["home_team"],
                    total_line=150.0,
                )

                preds_v3 = v3.predict_games(history_so_far, [upcoming])
                total_v3 = next(p for p in preds_v3 if p.market_type == "total")
                ml_v3 = next(p for p in preds_v3 if p.market_type == "moneyline")
                v3_total_err = abs(actual_total - total_v3.feature_basis["expected_total"])
                total_errs["v3"].append(v3_total_err)
                p_home_v3 = max(1e-6, min(1 - 1e-6, ml_v3.probabilities["home"]))
                moneyline_briers["v3"].append((p_home_v3 - home_won) ** 2)

                preds_v4_total = v4_total.predict_totals(history_so_far, [upcoming])
                total_v4 = preds_v4_total[0]
                v4_total_err = abs(actual_total - total_v4.feature_basis["expected_total"])
                total_errs["v4"].append(v4_total_err)

                preds_v4_spread = v4_spread.predict_spreads(history_so_far, [upcoming])
                ml_v4 = next(p for p in preds_v4_spread if p.market_type == "moneyline")
                p_home_v4 = max(1e-6, min(1 - 1e-6, ml_v4.probabilities["home"]))
                moneyline_briers["v4"].append((p_home_v4 - home_won) ** 2)

                current_season_sample = total_v4.feature_basis["current_season_sample"]
                bucket = "early_season" if current_season_sample <= 3 else "in_season"
                bucket_total_errs[bucket]["v3"].append(v3_total_err)
                bucket_total_errs[bucket]["v4"].append(v4_total_err)

                graded += 1

            if graded and graded % 500 == 0:
                elapsed = time.time() - t0
                print(f"  ...{graded} games graded ({elapsed:.0f}s elapsed)", flush=True)

        for g in day_games:
            history_so_far.append(
                GameRecord(
                    event_id=g["event_id"],
                    event_start_utc=g["event_start_utc"],
                    league=g["league"],
                    away_team=g["away_team"],
                    home_team=g["home_team"],
                    away_score=g["away_score"],
                    home_score=g["home_score"],
                )
            )

    elapsed = time.time() - t0
    print(f"\ngraded games: {graded} (took {elapsed:.0f}s)")
    for label in ("v3", "v4"):
        mae = float(np.mean(total_errs[label]))
        brier = float(np.mean(moneyline_briers[label]))
        print(f"{label}: total MAE={mae:.2f}  moneyline Brier={brier:.4f}")

    print("\nBy current-season sample bucket (total MAE only):")
    for bucket, data in bucket_total_errs.items():
        n = len(data["v3"])
        if n == 0:
            continue
        v3_mae = float(np.mean(data["v3"]))
        v4_mae = float(np.mean(data["v4"]))
        print(f"  {bucket} (n={n}): v3 MAE={v3_mae:.2f}  v4 MAE={v4_mae:.2f}")


if __name__ == "__main__":
    main()
