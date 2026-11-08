"""Walk-forward comparison: ncaab-tempo-v3 (incumbent) vs.
ncaab-spread-v5/ncaab-total-v5 (dedicated overhauls) on real NCAAB history.

Measures:
1. Total points MAE
2. Moneyline Brier score
3. Moneyline log loss
4. Spread error / cover direction
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import ncaab_v3_model
from model_prediction.models.ncaab_spread_v5 import NCAABSpreadModelV5
from model_prediction.models.ncaab_total_v5 import NCAABTotalModelV5

ROOT = Path(".")
GRADE_FROM = "2026-09-01"


def _load_games() -> list[dict]:
    with open(ROOT / "data/historical/ncaab_games_all.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    rows.sort(key=lambda r: r["event_start_utc"])
    return [r for r in rows if r.get("status") == "completed" and r.get("home_score") is not None]


def main() -> None:
    games = _load_games()
    print(f"Total completed games in database: {len(games)}")

    v3 = ncaab_v3_model()
    v5_spread = NCAABSpreadModelV5()
    v5_total = NCAABTotalModelV5()

    by_day: dict[str, list[dict]] = defaultdict(list)
    for g in games:
        by_day[g["event_start_utc"][:10]].append(g)
    days = sorted(by_day.keys())

    total_errs: dict[str, list[float]] = {"v3": [], "v5": []}
    moneyline_briers: dict[str, list[float]] = {"v3": [], "v5": []}
    moneyline_log_losses: dict[str, list[float]] = {"v3": [], "v5": []}
    spread_errs: dict[str, list[float]] = {"v3": [], "v5": []}

    history_so_far: list[GameRecord] = []

    for day in days:
        day_games = by_day[day]
        if day >= GRADE_FROM:
            for g in day_games:
                actual_total = g["away_score"] + g["home_score"]
                actual_margin = g["home_score"] - g["away_score"]
                home_won = 1.0 if g["home_score"] > g["away_score"] else 0.0

                upcoming = UpcomingGame(
                    event_id=g["event_id"],
                    event_start_utc=g["event_start_utc"],
                    away_team=g["away_team"],
                    home_team=g["home_team"],
                    total_line=150.0,
                    spread_away_line=0.0,
                    neutral_site=bool(g.get("neutral_site", False)),
                )

                # v3 prediction
                preds_v3 = v3.predict_games(history_so_far, [upcoming])
                total_v3 = next(p for p in preds_v3 if p.market_type == "total")
                ml_v3 = next(p for p in preds_v3 if p.market_type == "moneyline")
                spread_v3 = next(p for p in preds_v3 if p.market_type == "spread")

                total_errs["v3"].append(abs(actual_total - total_v3.feature_basis["expected_total"]))
                spread_errs["v3"].append(abs(actual_margin - spread_v3.feature_basis["expected_margin"]))
                p_home_v3 = max(1e-6, min(1 - 1e-6, ml_v3.probabilities["home"]))
                moneyline_briers["v3"].append((p_home_v3 - home_won) ** 2)
                ll_v3 = -(home_won * np.log(p_home_v3) + (1.0 - home_won) * np.log(1.0 - p_home_v3))
                moneyline_log_losses["v3"].append(ll_v3)

                # v5 prediction
                preds_v5_total = v5_total.predict_totals(history_so_far, [upcoming])
                total_v5 = preds_v5_total[0]
                total_errs["v5"].append(abs(actual_total - total_v5.feature_basis["expected_total"]))

                preds_v5_spread = v5_spread.predict_spreads(history_so_far, [upcoming])
                ml_v5 = next(p for p in preds_v5_spread if p.market_type == "moneyline")
                spread_v5 = next(p for p in preds_v5_spread if p.market_type == "spread")

                spread_errs["v5"].append(abs(actual_margin - spread_v5.feature_basis["expected_margin"]))
                p_home_v5 = max(1e-6, min(1 - 1e-6, ml_v5.probabilities["home"]))
                moneyline_briers["v5"].append((p_home_v5 - home_won) ** 2)
                ll_v5 = -(home_won * np.log(p_home_v5) + (1.0 - home_won) * np.log(1.0 - p_home_v5))
                moneyline_log_losses["v5"].append(ll_v5)

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

    n_graded = len(total_errs["v3"])
    print(f"\nGraded 2026-27 Season Games: {n_graded}")
    print("=" * 70)
    print(f"{'Metric':<25s} | {'v3 (Incumbent)':<18s} | {'v5 (Challenger)':<18s} | {'Delta':<10s}")
    print("-" * 70)

    # Total MAE
    t_v3 = float(np.mean(total_errs["v3"]))
    t_v5 = float(np.mean(total_errs["v5"]))
    t_delta = t_v5 - t_v3
    print(f"{'Total Points MAE':<25s} | {t_v3:<18.2f} | {t_v5:<18.2f} | {t_delta:<+8.2f} pts")

    # Spread Margin MAE
    s_v3 = float(np.mean(spread_errs["v3"]))
    s_v5 = float(np.mean(spread_errs["v5"]))
    s_delta = s_v5 - s_v3
    print(f"{'Spread Margin MAE':<25s} | {s_v3:<18.2f} | {s_v5:<18.2f} | {s_delta:<+8.2f} pts")

    # Moneyline Brier
    b_v3 = float(np.mean(moneyline_briers["v3"]))
    b_v5 = float(np.mean(moneyline_briers["v5"]))
    b_pct = ((b_v5 - b_v3) / b_v3) * 100.0
    print(f"{'Moneyline Brier Score':<25s} | {b_v3:<18.4f} | {b_v5:<18.4f} | {b_pct:<+7.1f}%")

    # Moneyline Log Loss
    l_v3 = float(np.mean(moneyline_log_losses["v3"]))
    l_v5 = float(np.mean(moneyline_log_losses["v5"]))
    l_pct = ((l_v5 - l_v3) / l_v3) * 100.0
    print(f"{'Moneyline Log Loss':<25s} | {l_v3:<18.4f} | {l_v5:<18.4f} | {l_pct:<+7.1f}%")
    print("=" * 70)


if __name__ == "__main__":
    main()
