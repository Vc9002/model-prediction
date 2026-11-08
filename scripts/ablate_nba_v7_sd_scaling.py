"""Ablate v7's untested volatility/SD-scaling constants against actual outcomes.

The ridge-regularization test (ridge_regularize_nba_v7_margin.py) only covered
the 7 margin-side constants and found no overfitting problem there. The
larger stacked-adjustment surface -- dynamic_volatility, pace_sd_scaling, and
overtime_risk_adjustment -- controls the predicted *distribution width*
(effective_margin_sd / effective_total_sd), which directly drives spread/total
edge pricing but was never evaluated against real outcomes. Point-estimate
MAE can't test this (it doesn't depend on SD at all); the right metric is
Gaussian negative log-likelihood of the actual margin/total under
N(expected, effective_sd) -- this rewards both an accurate center AND a
correctly-sized spread, which is what actually matters for market pricing.

Fits/selects on validation only; locked holdout touched once for the
selected configuration.
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ablate_nba_v7 import load_nba_games
from nba_v5_pit_walkforward import split_complete_dates

from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v7 import NBAStructuralV7Engine

LOG_2PI = math.log(2.0 * math.pi)


def normal_nll(actual: float, mean: float, sd: float) -> float:
    sd = max(sd, 1e-3)
    z = (actual - mean) / sd
    return 0.5 * (LOG_2PI + 2.0 * math.log(sd) + z * z)


def evaluate(engine: NBAStructuralV7Engine, all_games: list, eval_games: list) -> dict[str, float]:
    dates_map: dict[str, list] = defaultdict(list)
    for g in eval_games:
        dates_map[g.event_start_utc[:10]].append(g)

    margin_nlls: list[float] = []
    total_nlls: list[float] = []
    margin_zs: list[float] = []
    total_zs: list[float] = []

    for d in sorted(dates_map.keys()):
        games_today = dates_map[d]
        first_start = games_today[0].event_start_utc
        history = [g for g in all_games if g.event_start_utc < first_start]
        upcoming = [
            UpcomingGame(
                event_id=g.event_id,
                event_start_utc=g.event_start_utc,
                away_team=g.away_team,
                home_team=g.home_team,
                spread_away_line=0.0,
                total_line=228.5,
                league="NBA",
            )
            for g in games_today
        ]
        preds = engine.predict_games(history, upcoming)
        by_event: dict[str, dict[str, Any]] = defaultdict(dict)
        for p in preds:
            by_event[p.event_id][p.market_type] = p

        for g in games_today:
            actual_margin = float(g.home_score - g.away_score)
            actual_total = float(g.home_score + g.away_score)
            spread_pred = by_event[g.event_id].get("spread")
            total_pred = by_event[g.event_id].get("total")
            if spread_pred is not None:
                exp_margin = float(spread_pred.feature_basis["expected_margin"])
                sd_margin = float(spread_pred.feature_basis["effective_margin_sd"])
                margin_nlls.append(normal_nll(actual_margin, exp_margin, sd_margin))
                margin_zs.append((actual_margin - exp_margin) / max(sd_margin, 1e-3))
            if total_pred is not None:
                exp_total = float(total_pred.feature_basis["expected_total"])
                sd_total = float(total_pred.feature_basis["effective_total_sd"])
                total_nlls.append(normal_nll(actual_total, exp_total, sd_total))
                total_zs.append((actual_total - exp_total) / max(sd_total, 1e-3))

    def mean(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else float("nan")

    def stdev(xs: list[float]) -> float:
        if len(xs) < 2:
            return float("nan")
        m = sum(xs) / len(xs)
        return round(math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)), 4)

    return {
        "n": len(margin_nlls),
        "margin_nll": mean(margin_nlls),
        "total_nll": mean(total_nlls),
        "margin_z_stdev": stdev(margin_zs),  # should be ~1.0 if SD is well-calibrated
        "total_z_stdev": stdev(total_zs),
        "combined_nll": round(mean(margin_nlls) + mean(total_nlls), 4),
    }


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    _train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    configs: dict[str, dict[str, bool]] = {}
    for dv, pss, ora in itertools.product([False, True], [False, True], [False, True]):
        name = f"dv={dv},pss={pss},ora={ora}"
        configs[name] = {
            "dynamic_volatility": dv,
            "pace_sd_scaling": pss,
            "overtime_risk_adjustment": ora,
        }

    print("\n--- VALIDATION sweep (combined margin+total NLL, lower is better) ---")
    val_results: dict[str, dict[str, float]] = {}
    for name, kwargs in configs.items():
        engine = NBAStructuralV7Engine(**kwargs)
        m = evaluate(engine, all_games, val_games)
        val_results[name] = m
        print(
            f"{name}: combined_nll={m['combined_nll']} margin_z_sd={m['margin_z_stdev']} total_z_sd={m['total_z_stdev']}"
        )

    selected_name = min(val_results, key=lambda n: val_results[n]["combined_nll"])
    current_default_name = "dv=True,pss=True,ora=True"
    print(f"\nCurrent v7 default: {current_default_name} -> {val_results[current_default_name]}")
    print(f"Best on validation: {selected_name} -> {val_results[selected_name]}")

    print("\n--- LOCKED HOLDOUT (touched once): current default vs selected ---")
    default_engine = NBAStructuralV7Engine(**configs[current_default_name])
    selected_engine = NBAStructuralV7Engine(**configs[selected_name])
    holdout_default = evaluate(default_engine, all_games, holdout_games)
    holdout_selected = evaluate(selected_engine, all_games, holdout_games)
    print("default:", holdout_default)
    print("selected:", holdout_selected)

    out = {
        "split_meta": meta,
        "validation_sweep": val_results,
        "current_default": current_default_name,
        "selected": selected_name,
        "holdout_default": holdout_default,
        "holdout_selected": holdout_selected,
    }
    Path("scratch").mkdir(exist_ok=True)
    Path("scratch/nba_v7_sd_scaling_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_v7_sd_scaling_result.json")


if __name__ == "__main__":
    run()
