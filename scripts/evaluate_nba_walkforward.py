"""Walk-forward evaluation of NBA models on settled games.

Evaluates incumbent NBA models (v2, v5) and challenger models (v6) using strict
point-in-time date-clustered walk-forward methodology on data/processed/nba/games.jsonl.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from nba_v5_pit_walkforward import split_complete_dates

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import BasketballModel, UpcomingGame


@dataclass
class EvalMetrics:
    sample_size: int
    mae_margin: float
    rmse_margin: float
    mae_total: float
    rmse_total: float
    moneyline_accuracy: float
    moneyline_brier: float
    moneyline_logloss: float


def load_nba_games(path: Path) -> list[GameRecord]:
    with open(path, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    # Sort strictly by event_start_utc
    rows.sort(key=lambda r: r["event_start_utc"])

    records = [
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
    return records


def evaluate_model_walkforward(
    model: BasketballModel,
    all_games: list[GameRecord],
    eval_games: list[GameRecord],
    use_player_availability: bool = False,
) -> tuple[EvalMetrics, list[dict[str, Any]]]:
    """Run sequential point-in-time date-clustered walk-forward evaluation."""
    # Group eval_games by calendar date
    dates_map: dict[str, list[GameRecord]] = defaultdict(list)
    for g in eval_games:
        dates_map[g.event_start_utc[:10]].append(g)

    sorted_dates = sorted(dates_map.keys())

    row_results: list[dict[str, Any]] = []

    for d in sorted_dates:
        games_today = dates_map[d]
        first_start = games_today[0].event_start_utc

        # Point-in-time history: games strictly prior to today's games
        history = [g for g in all_games if g.event_start_utc < first_start]

        upcoming = [
            UpcomingGame(
                event_id=g.event_id,
                event_start_utc=g.event_start_utc,
                away_team=g.away_team,
                home_team=g.home_team,
                spread_away_line=0.0,  # line=0 evaluates pure expected margin vs 0
                total_line=228.5,
                league="NBA",
            )
            for g in games_today
        ]

        preds = model.predict_games(history, upcoming)

        # Map predictions by event_id and market_type
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

            p_home_win = float(ml_pred.probabilities.get("home", 0.5)) if ml_pred else 0.5
            p_home_clamped = max(1e-4, min(1.0 - 1e-4, p_home_win))

            brier = (p_home_clamped - actual_home_win) ** 2
            logloss = -(
                actual_home_win * np.log(p_home_clamped)
                + (1.0 - actual_home_win) * np.log(1.0 - p_home_clamped)
            )
            pred_winner_home = 1.0 if p_home_clamped >= 0.5 else 0.0
            correct = 1.0 if pred_winner_home == actual_home_win else 0.0

            abs_err_margin = abs(exp_margin - actual_margin)
            abs_err_total = abs(exp_total - actual_total)

            row_results.append(
                {
                    "date": d,
                    "event_id": g.event_id,
                    "home_team": g.home_team,
                    "away_team": g.away_team,
                    "actual_margin": actual_margin,
                    "expected_margin": exp_margin,
                    "abs_error_margin": abs_err_margin,
                    "sq_error_margin": (exp_margin - actual_margin) ** 2,
                    "actual_total": actual_total,
                    "expected_total": exp_total,
                    "abs_error_total": abs_err_total,
                    "sq_error_total": (exp_total - actual_total) ** 2,
                    "p_home_win": p_home_win,
                    "actual_home_win": actual_home_win,
                    "ml_correct": correct,
                    "ml_brier": brier,
                    "ml_logloss": logloss,
                }
            )

    n = len(row_results)
    mae_margin = float(np.mean([r["abs_error_margin"] for r in row_results]))
    rmse_margin = float(np.sqrt(np.mean([r["sq_error_margin"] for r in row_results])))
    mae_total = float(np.mean([r["abs_error_total"] for r in row_results]))
    rmse_total = float(np.sqrt(np.mean([r["sq_error_total"] for r in row_results])))
    acc = float(np.mean([r["ml_correct"] for r in row_results]))
    brier_mean = float(np.mean([r["ml_brier"] for r in row_results]))
    logloss_mean = float(np.mean([r["ml_logloss"] for r in row_results]))

    metrics = EvalMetrics(
        sample_size=n,
        mae_margin=round(mae_margin, 4),
        rmse_margin=round(rmse_margin, 4),
        mae_total=round(mae_total, 4),
        rmse_total=round(rmse_total, 4),
        moneyline_accuracy=round(acc, 4),
        moneyline_brier=round(brier_mean, 4),
        moneyline_logloss=round(logloss_mean, 4),
    )
    return metrics, row_results


if __name__ == "__main__":
    games_path = Path("data/processed/nba/games.jsonl")
    all_games = load_nba_games(games_path)
    train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)

    print("Data split:", meta)

    # Load v5 artifact configuration
    with open("config/models/nba-spread-margin-v5.json", "r", encoding="utf-8") as f:
        v5_cfg = json.load(f)

    v5_model = BasketballModel(
        sport="nba",
        version="nba-spread-margin-v5",
        margin_sd=v5_cfg["margin_sd"],
        total_sd=v5_cfg["total_sd"],
        league="NBA",
        elo_weight=v5_cfg.get("elo_weight", 0.5),
        trend_weight=v5_cfg.get("trend_weight", 0.5),
        rest_weight=v5_cfg.get("rest_weight", 0.2),
        home_court_points=v5_cfg.get("home_court_points", 1.4),
        offseason_gap_days=v5_cfg.get("offseason_gap_days", 60.0),
        offseason_retention=v5_cfg.get("offseason_retention", 0.4),
        isolate_postseason=v5_cfg.get("isolate_postseason", True),
        elo_home_advantage=v5_cfg.get("elo_home_advantage", 38.0),
        possession_model=v5_cfg.get("possession_model", True),
        b2b_penalty_away=v5_cfg.get("b2b_penalty_away", 1.2),
        b2b_penalty_home=v5_cfg.get("b2b_penalty_home", 0.6),
        b2b_pace_drag=v5_cfg.get("b2b_pace_drag", 0.8),
        coherent_moneyline=v5_cfg.get("coherent_moneyline", True),
        dynamic_matchup_pace=v5_cfg.get("dynamic_matchup_pace", True),
        altitude_hca=v5_cfg.get("altitude_hca", True),
        schedule_density_drag=v5_cfg.get("schedule_density_drag", True),
        dynamic_volatility=v5_cfg.get("dynamic_volatility", True),
        overtime_risk_adjustment=v5_cfg.get("overtime_risk_adjustment", True),
        use_logistic_moneyline=v5_cfg.get("use_logistic_moneyline", True),
        logistic_scale=v5_cfg.get("logistic_scale", 11.2),
    )

    print("\n--- EVALUATING NBA v5 ON VALIDATION SET ---")
    val_metrics_v5, _ = evaluate_model_walkforward(v5_model, all_games, val_games)
    print(val_metrics_v5)

    print("\n--- EVALUATING NBA v5 ON LOCKED HOLDOUT SET ---")
    holdout_metrics_v5, _ = evaluate_model_walkforward(v5_model, all_games, holdout_games)
    print(holdout_metrics_v5)
