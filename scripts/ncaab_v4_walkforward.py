"""Walk-forward comparison: ncaab-tempo-v3 (incumbent, shared BasketballModel)
vs. ncaab-spread-v4/ncaab-total-v4 (dedicated classes) on real NCAAB history.

Research-only: no ledger writes, no orders. Batches by calendar day to keep
the TrendEngine rebuild cost linear rather than quadratic in game count.
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
from model_prediction.models.ncaab_spread_v4 import NCAABSpreadModelV4
from model_prediction.models.ncaab_total_v4 import NCAABTotalModelV4

ROOT = Path(".")
GRADE_FROM = "2026-09-01"  # the historical data file has a gap from April to November; the
# graded window in practice only contains early-November 2026-27 season-start games (2026-11-01
# through 2026-11-05), not a true in-season control group.


def _load_games() -> list[dict]:
    with open(ROOT / "data/historical/ncaab_games_all.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    rows.sort(key=lambda r: r["event_start_utc"])
    return [r for r in rows if r.get("status") == "completed" and r.get("home_score") is not None]


def main() -> None:
    games = _load_games()
    print(f"total completed games: {len(games)}")

    v3 = ncaab_v3_model()
    v4_spread = NCAABSpreadModelV4()
    v4_total = NCAABTotalModelV4()

    by_day: dict[str, list[dict]] = defaultdict(list)
    for g in games:
        by_day[g["event_start_utc"][:10]].append(g)
    days = sorted(by_day.keys())

    total_errs: dict[str, list[float]] = {"v3": [], "v4": []}
    moneyline_briers: dict[str, list[float]] = {"v3": [], "v4": []}
    history_so_far: list[GameRecord] = []

    for day in days:
        day_games = by_day[day]
        if day >= GRADE_FROM:
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
                total_errs["v3"].append(abs(actual_total - total_v3.feature_basis["expected_total"]))
                p_home_v3 = max(1e-6, min(1 - 1e-6, ml_v3.probabilities["home"]))
                moneyline_briers["v3"].append((p_home_v3 - home_won) ** 2)

                preds_v4_total = v4_total.predict_totals(history_so_far, [upcoming])
                total_v4 = preds_v4_total[0]
                total_errs["v4"].append(abs(actual_total - total_v4.feature_basis["expected_total"]))

                preds_v4_spread = v4_spread.predict_spreads(history_so_far, [upcoming])
                ml_v4 = next(p for p in preds_v4_spread if p.market_type == "moneyline")
                p_home_v4 = max(1e-6, min(1 - 1e-6, ml_v4.probabilities["home"]))
                moneyline_briers["v4"].append((p_home_v4 - home_won) ** 2)

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

    print(f"\ngraded games: {len(total_errs['v3'])}")
    for label in ("v3", "v4"):
        mae = float(np.mean(total_errs[label]))
        brier = float(np.mean(moneyline_briers[label]))
        print(f"{label}: total MAE={mae:.2f}  moneyline Brier={brier:.4f}")


if __name__ == "__main__":
    main()
