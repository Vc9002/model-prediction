"""Isotonic recalibration of NBA Structural v7's moneyline probabilities (overhaul round 3, lever 5).

Fits on VALIDATION only; LOCKED HOLDOUT touched once. Same 60/20/20
chronological split and same historical dataset as scripts/ablate_nba_v7.py.
Reports Brier/log-loss/accuracy before and after calibration -- a
calibrator can only reorder probability quality, not flip win/loss
predictions (isotonic regression is monotonic), so accuracy should be
near-identical; Brier/log-loss are the metrics that matter here.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from ablate_nba_v7 import load_nba_games
from nba_v5_pit_walkforward import split_complete_dates
from sklearn.isotonic import IsotonicRegression

from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v7 import NBAStructuralV7Engine


def collect_raw_predictions(
    engine: NBAStructuralV7Engine,
    all_games: list,
    eval_games: list,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (p_home_raw, actual_home_win) arrays, point-in-time correct."""
    dates_map: dict[str, list] = defaultdict(list)
    for g in eval_games:
        dates_map[g.event_start_utc[:10]].append(g)

    probs: list[float] = []
    outcomes: list[float] = []
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
            ml_pred = by_event[g.event_id].get("moneyline")
            p_home = max(
                1e-4, min(1 - 1e-4, float(ml_pred.probabilities.get("home", 0.5)) if ml_pred else 0.5)
            )
            actual_home_win = 1.0 if (g.home_score - g.away_score) > 0 else 0.0
            probs.append(p_home)
            outcomes.append(actual_home_win)

    return np.array(probs), np.array(outcomes)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p_clipped = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p_clipped) + (1 - y) * np.log(1 - p_clipped)))


def accuracy(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p >= 0.5).astype(float) == y))


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    _train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    engine = NBAStructuralV7Engine()  # v7 defaults -- same engine instance used for fit and apply

    print("\nCollecting validation-split raw predictions (fit set)...")
    p_val, y_val = collect_raw_predictions(engine, all_games, val_games)
    print(
        f"validation n={len(p_val)} raw brier={brier(p_val, y_val):.4f} "
        f"raw log_loss={log_loss(p_val, y_val):.4f} raw acc={accuracy(p_val, y_val):.4f}"
    )

    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=1e-4, y_max=1 - 1e-4)
    calibrator.fit(p_val, y_val)
    p_val_calibrated = calibrator.predict(p_val)
    print(
        f"validation (in-sample calibrated) brier={brier(p_val_calibrated, y_val):.4f} "
        f"log_loss={log_loss(p_val_calibrated, y_val):.4f} acc={accuracy(p_val_calibrated, y_val):.4f}"
    )

    print("\nCollecting LOCKED HOLDOUT raw predictions (touched once)...")
    p_hold, y_hold = collect_raw_predictions(engine, all_games, holdout_games)
    p_hold_calibrated = calibrator.predict(p_hold)

    before = {
        "n": len(p_hold),
        "brier": round(brier(p_hold, y_hold), 4),
        "log_loss": round(log_loss(p_hold, y_hold), 4),
        "accuracy": round(accuracy(p_hold, y_hold), 4),
    }
    after = {
        "n": len(p_hold_calibrated),
        "brier": round(brier(p_hold_calibrated, y_hold), 4),
        "log_loss": round(log_loss(p_hold_calibrated, y_hold), 4),
        "accuracy": round(accuracy(p_hold_calibrated, y_hold), 4),
    }

    print("\n--- LOCKED HOLDOUT: v7 RAW (before calibration) ---")
    print(before)
    print("\n--- LOCKED HOLDOUT: v7 + isotonic calibration (after) ---")
    print(after)

    out = {
        "split_meta": meta,
        "validation_raw": {
            "brier": round(brier(p_val, y_val), 4),
            "log_loss": round(log_loss(p_val, y_val), 4),
            "accuracy": round(accuracy(p_val, y_val), 4),
            "n": len(p_val),
        },
        "validation_calibrated_in_sample": {
            "brier": round(brier(p_val_calibrated, y_val), 4),
            "log_loss": round(log_loss(p_val_calibrated, y_val), 4),
            "accuracy": round(accuracy(p_val_calibrated, y_val), 4),
        },
        "holdout_raw": before,
        "holdout_calibrated": after,
    }
    Path("scratch").mkdir(exist_ok=True)
    Path("scratch/nba_v7_recalibration_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_v7_recalibration_result.json")


if __name__ == "__main__":
    run()
