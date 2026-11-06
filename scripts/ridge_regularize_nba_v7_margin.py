"""Ridge-regularize NBA Structural v7's stacked hand-tuned margin constants.

v7's margin formula stacks ~7 hand-picked constants additively/multiplicatively
(b2b_penalty_home=2.0, b2b_penalty_away=1.5, home_3in4 penalty=-0.80,
away_3in4 penalty=-1.20, home_court_points=1.75, elo_weight=0.70,
trend_weight=0.30, rest_weight=0.15) with no regularization -- exactly the
overfitting risk flagged by the Sloan/APM ridge-regression literature (Sill
2010) for this class of stacked situational-adjustment model.

This fits the same functional form -- margin = w1*elo_margin +
w2*trend_margin_raw + w3*rest_diff + w4*home_b2b + w5*away_b2b +
w6*home_3in4_only + w7*away_3in4_only + intercept (replacing home_court_points)
-- as a single ridge regression against real margins on TRAIN only, with the
ridge alpha selected on VALIDATION, then compares fitted-coefficient margin
MAE against the hand-tuned v7 defaults on validation and the locked holdout
(touched once).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ablate_nba_v7 import load_nba_games
from nba_v5_pit_walkforward import split_complete_dates

from model_prediction.domain import parse_utc
from model_prediction.features.elo_ratings import build_elo
from model_prediction.features.trends import TrendEngine
from model_prediction.models.nba_v7 import NBAStructuralV7Engine

FEATURE_NAMES = [
    "elo_margin",
    "trend_margin_raw",
    "rest_diff",
    "home_b2b",
    "away_b2b",
    "home_3in4_only",
    "away_3in4_only",
]
ALPHA_GRID = [0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]


def build_design_rows(all_games: list, eval_games: list) -> tuple[np.ndarray, np.ndarray]:
    """Replicate v7's raw margin components for each game, point-in-time correct."""
    rows: list[list[float]] = []
    targets: list[float] = []
    engine = NBAStructuralV7Engine()

    by_date: dict[str, list] = {}
    for g in eval_games:
        by_date.setdefault(g.event_start_utc[:10], []).append(g)

    for d in sorted(by_date.keys()):
        games_today = by_date[d]
        first_start = games_today[0].event_start_utc
        history = [g for g in all_games if g.event_start_utc < first_start]
        elo = build_elo(
            history,
            "nba",
            offseason_gap_days=int(engine.offseason_gap_days),
            home_advantage=engine.elo_home_advantage,
        )
        trend = TrendEngine(
            history,
            offseason_gap_days=engine.offseason_gap_days,
            offseason_retention=engine.offseason_retention,
            isolate_postseason=engine.isolate_postseason,
        )
        baseline = trend.league_baseline or engine.baseline_points_per_team

        for game in games_today:
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)
            away_off = away_trend.offense["hl10"] if away_trend.games_played > 0 else baseline
            home_off = home_trend.offense["hl10"] if home_trend.games_played > 0 else baseline
            away_def = away_trend.defense["hl10"] if away_trend.games_played > 0 else baseline
            home_def = home_trend.defense["hl10"] if home_trend.games_played > 0 else baseline
            away_points = (away_off + home_def) / 2.0
            home_points = (home_off + away_def) / 2.0

            r_home = elo.rating(game.home_team) + elo.home_advantage
            r_away = elo.rating(game.away_team)
            elo_margin = (r_home - r_away) / 28.0
            trend_margin_raw = home_points - away_points

            game_start_dt = parse_utc(game.event_start_utc)
            home_prev = [p for p in history if p.home_team == game.home_team or p.away_team == game.home_team]
            away_prev = [p for p in history if p.home_team == game.away_team or p.away_team == game.away_team]
            home_prev.sort(key=lambda p: p.start)
            away_prev.sort(key=lambda p: p.start)
            home_rest = (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0 if home_prev else 3.0
            away_rest = (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0 if away_prev else 3.0
            rest_diff = min(5.0, max(-5.0, home_rest - away_rest))
            home_b2b = home_rest <= 1.25
            away_b2b = away_rest <= 1.25
            h_4d = [g for g in home_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
            a_4d = [g for g in away_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
            home_3in4_only = len(h_4d) >= 2 and not home_b2b
            away_3in4_only = len(a_4d) >= 2 and not away_b2b

            rows.append(
                [
                    elo_margin,
                    trend_margin_raw,
                    rest_diff,
                    1.0 if home_b2b else 0.0,
                    1.0 if away_b2b else 0.0,
                    1.0 if home_3in4_only else 0.0,
                    1.0 if away_3in4_only else 0.0,
                ]
            )
            targets.append(float(game.home_score - game.away_score))

    return np.array(rows), np.array(targets)


def hand_tuned_margin(X: np.ndarray) -> np.ndarray:
    """v7's current hand-tuned linear combination, for comparison."""
    e = NBAStructuralV7Engine()
    elo_margin, trend_margin_raw, rest_diff, home_b2b, away_b2b, home_3in4, away_3in4 = X.T
    # v7 applies b2b/3in4 penalties to home_points/away_points BEFORE taking the
    # trend margin difference -- equivalent to adding them with a sign flip here.
    trend_margin = (
        trend_margin_raw
        - e.b2b_penalty_home * home_b2b
        + e.b2b_penalty_away * away_b2b
        - 0.80 * home_3in4
        + 1.20 * away_3in4
        + e.home_court_points
    )
    return e.elo_weight * elo_margin + e.trend_weight * trend_margin + e.rest_weight * rest_diff


def mae(pred: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean(np.abs(pred - actual)))


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    print("\nBuilding design matrices (point-in-time correct)...")
    X_train, y_train = build_design_rows(all_games, train_games)
    X_val, y_val = build_design_rows(all_games, val_games)
    X_hold, y_hold = build_design_rows(all_games, holdout_games)
    print(f"train n={len(y_train)} val n={len(y_val)} holdout n={len(y_hold)}")

    baseline_val_mae = mae(hand_tuned_margin(X_val), y_val)
    print(f"\nhand-tuned v7 margin MAE on validation: {baseline_val_mae:.4f}")

    print("\n--- Ridge alpha sweep (fit on train, scored on validation) ---")
    best_alpha = None
    best_val_mae = float("inf")
    best_model = None
    for alpha in ALPHA_GRID:
        model = Ridge(alpha=alpha, fit_intercept=True)
        model.fit(X_train, y_train)
        val_mae = mae(model.predict(X_val), y_val)
        print(
            f"alpha={alpha}: val_mae={val_mae:.4f} coefs={dict(zip(FEATURE_NAMES, np.round(model.coef_, 3)))} intercept={model.intercept_:.3f}"
        )
        if val_mae < best_val_mae:
            best_val_mae = val_mae
            best_alpha = alpha
            best_model = model

    print(f"\nSelected alpha={best_alpha} (val_mae={best_val_mae:.4f} vs hand-tuned {baseline_val_mae:.4f})")

    print("\n--- LOCKED HOLDOUT (touched once) ---")
    holdout_handtuned_mae = mae(hand_tuned_margin(X_hold), y_hold)
    holdout_ridge_mae = mae(best_model.predict(X_hold), y_hold)
    print(f"hand-tuned v7: {holdout_handtuned_mae:.4f}")
    print(f"ridge (alpha={best_alpha}): {holdout_ridge_mae:.4f}")

    out = {
        "split_meta": meta,
        "feature_names": FEATURE_NAMES,
        "validation_sweep": {
            str(a): mae(Ridge(alpha=a, fit_intercept=True).fit(X_train, y_train).predict(X_val), y_val)
            for a in ALPHA_GRID
        },
        "hand_tuned_validation_mae": baseline_val_mae,
        "selected_alpha": best_alpha,
        "selected_ridge_coefs": dict(zip(FEATURE_NAMES, best_model.coef_.tolist())),
        "selected_ridge_intercept": float(best_model.intercept_),
        "holdout_hand_tuned_mae": holdout_handtuned_mae,
        "holdout_ridge_mae": holdout_ridge_mae,
    }
    Path("scratch").mkdir(exist_ok=True)
    Path("scratch/nba_v7_ridge_margin_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_v7_ridge_margin_result.json")


if __name__ == "__main__":
    run()
