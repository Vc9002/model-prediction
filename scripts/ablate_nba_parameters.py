"""Ablation study on NBA Validation set (n=886 games).

Explores mathematical parameter improvements on the validation set, keeping
holdout completely untouched until candidate selection.
"""

from __future__ import annotations

import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from nba_v5_pit_walkforward import split_complete_dates

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import BasketballModel, UpcomingGame


def load_nba_games(path: Path) -> list[GameRecord]:
    with open(path, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows.sort(key=lambda r: r["event_start_utc"])
    return [
        GameRecord(
            event_id=str(r["event_id"]),
            event_start_utc=r["event_start_utc"],
            league="NBA",
            away_team=r["away_team"],
            home_team=r["home_team"],
            away_score=int(r["away_score"]),
            home_score=int(r["home_score"]),
            season_type=r.get("season_type", "unknown"),
            season_year=r.get("season_year"),
        )
        for r in rows
    ]


def evaluate_config(
    all_games: list[GameRecord],
    val_games: list[GameRecord],
    cfg: dict[str, Any],
) -> dict[str, float]:
    model = BasketballModel(
        sport="nba",
        version="ablation",
        league="NBA",
        margin_sd=cfg.get("margin_sd", 14.5),
        total_sd=cfg.get("total_sd", 17.8),
        elo_weight=cfg.get("elo_weight", 0.5),
        trend_weight=cfg.get("trend_weight", 0.5),
        rest_weight=cfg.get("rest_weight", 0.2),
        home_court_points=cfg.get("home_court_points", 1.4),
        offseason_gap_days=cfg.get("offseason_gap_days", 60.0),
        offseason_retention=cfg.get("offseason_retention", 0.4),
        isolate_postseason=cfg.get("isolate_postseason", True),
        elo_home_advantage=cfg.get("elo_home_advantage", 38.0),
        possession_model=cfg.get("possession_model", True),
        b2b_penalty_away=cfg.get("b2b_penalty_away", 1.2),
        b2b_penalty_home=cfg.get("b2b_penalty_home", 0.6),
        b2b_pace_drag=cfg.get("b2b_pace_drag", 0.8),
        coherent_moneyline=cfg.get("coherent_moneyline", True),
        dynamic_matchup_pace=cfg.get("dynamic_matchup_pace", True),
        altitude_hca=cfg.get("altitude_hca", False),
        schedule_density_drag=cfg.get("schedule_density_drag", True),
        dynamic_volatility=cfg.get("dynamic_volatility", True),
        overtime_risk_adjustment=cfg.get("overtime_risk_adjustment", True),
        use_logistic_moneyline=cfg.get("use_logistic_moneyline", True),
        logistic_scale=cfg.get("logistic_scale", 11.2),
    )

    dates_map: dict[str, list[GameRecord]] = defaultdict(list)
    for g in val_games:
        dates_map[g.event_start_utc[:10]].append(g)

    sorted_dates = sorted(dates_map.keys())
    margin_errs = []
    total_errs = []
    briers = []
    accs = []

    for d in sorted_dates:
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

        preds = model.predict_games(history, upcoming)
        by_event: dict[str, dict[str, Any]] = defaultdict(dict)
        for p in preds:
            by_event[p.event_id][p.market_type] = p

        for g in games_today:
            actual_margin = g.home_score - g.away_score
            actual_total = g.home_score + g.away_score
            actual_home_win = 1.0 if actual_margin > 0 else 0.0

            ml_pred = by_event[g.event_id].get("moneyline")
            spread_pred = by_event[g.event_id].get("spread")
            total_pred = by_event[g.event_id].get("total")

            exp_margin = float(spread_pred.feature_basis.get("expected_margin", 0.0)) if spread_pred else 0.0
            exp_total = float(total_pred.feature_basis.get("expected_total", 228.5)) if total_pred else 228.5

            p_h = float(ml_pred.probabilities.get("home", 0.5)) if ml_pred else 0.5
            p_h_c = max(1e-4, min(1.0 - 1e-4, p_h))

            margin_errs.append(abs(exp_margin - actual_margin))
            total_errs.append(abs(exp_total - actual_total))
            briers.append((p_h_c - actual_home_win) ** 2)
            accs.append(1.0 if (p_h_c >= 0.5) == (actual_home_win == 1.0) else 0.0)

    return {
        "mae_margin": round(float(np.mean(margin_errs)), 4),
        "mae_total": round(float(np.mean(total_errs)), 4),
        "brier": round(float(np.mean(briers)), 4),
        "acc": round(float(np.mean(accs)), 4),
    }


def run_ablation():
    all_games = load_nba_games(Path("data/processed/nba/games.jsonl"))
    _train_games, val_games, _holdout_games, _ = split_complete_dates(all_games, 0.60, 0.20)

    # Baseline v5
    v5_baseline_cfg = {
        "elo_weight": 0.5,
        "trend_weight": 0.5,
        "rest_weight": 0.2,
        "home_court_points": 1.4,
        "altitude_hca": True,
        "b2b_penalty_home": 0.6,
        "b2b_penalty_away": 1.2,
        "dynamic_matchup_pace": True,
        "possession_model": True,
        "margin_sd": 14.5,
        "total_sd": 17.8,
    }
    baseline_res = evaluate_config(all_games, val_games, v5_baseline_cfg)
    print("BASELINE v5 (Validation n=886):", baseline_res)

    # Grid search on validation
    param_grid = {
        "altitude_hca": [False, True],
        "b2b_penalty_home": [0.6, 1.8, 2.8],
        "b2b_penalty_away": [0.8, 1.2, 1.6],
        "home_court_points": [1.2, 1.4, 1.7],
        "elo_weight": [0.4, 0.5, 0.6],
        "rest_weight": [0.15, 0.25],
    }

    best_margin = baseline_res["mae_margin"]
    best_brier = baseline_res["brier"]
    best_margin_cfg = None
    best_brier_cfg = None

    keys, values = zip(*param_grid.items())
    permutations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    print(f"Testing {len(permutations)} configurations on Validation...")

    results = []
    for i, p in enumerate(permutations):
        cfg = dict(v5_baseline_cfg)
        cfg.update(p)
        cfg["trend_weight"] = 1.0 - cfg["elo_weight"]
        res = evaluate_config(all_games, val_games, cfg)
        results.append((res, p))
        if res["mae_margin"] < best_margin:
            best_margin = res["mae_margin"]
            best_margin_cfg = p
        if res["brier"] < best_brier:
            best_brier = res["brier"]
            best_brier_cfg = p

    print("\n--- BEST MARGIN CONFIG ---")
    print(f"MAE: {best_margin:.4f} (Baseline: {baseline_res['mae_margin']:.4f})")
    print(best_margin_cfg)

    print("\n--- BEST BRIER CONFIG ---")
    print(f"Brier: {best_brier:.4f} (Baseline: {baseline_res['brier']:.4f})")
    print(best_brier_cfg)

    # Sort results by composite improvement (margin MAE + brier * 10)
    results.sort(key=lambda x: x[0]["mae_margin"] + x[0]["brier"] * 10.0)
    print("\n--- TOP 5 BALANCED CANDIDATES ---")
    for r, p in results[:5]:
        print(f"Margin MAE: {r['mae_margin']}, Brier: {r['brier']}, Acc: {r['acc']} | Params: {p}")


if __name__ == "__main__":
    run_ablation()
