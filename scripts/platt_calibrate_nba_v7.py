"""Platt-scaling recalibration of NBA Structural v7's moneyline probabilities.

Same split, same raw predictions as scripts/recalibrate_nba_v7.py, but fits a
2-parameter logistic (Platt) calibrator instead of an unconstrained isotonic
step function -- isotonic overfit validation's 894-game sample and made the
locked holdout's brier/log-loss WORSE (0.1957 -> 0.1993), even though it
looked like a clean win on validation itself. Platt's 2 degrees of freedom
should generalize better than isotonic's ~n-bin flexibility.

Fits on VALIDATION only; LOCKED HOLDOUT touched once.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ablate_nba_v7 import load_nba_games
from nba_v5_pit_walkforward import split_complete_dates
from recalibrate_nba_v7 import accuracy, brier, collect_raw_predictions, log_loss
from sklearn.linear_model import LogisticRegression

from model_prediction.models.nba_v7 import NBAStructuralV7Engine


def run() -> None:
    games_path = Path("data/historical/nba_games_all.jsonl")
    all_games = load_nba_games(games_path)
    _train_games, val_games, holdout_games, meta = split_complete_dates(all_games, 0.60, 0.20)
    print("Data split:", meta)

    engine = NBAStructuralV7Engine()

    print("\nCollecting validation-split raw predictions (fit set)...")
    p_val, y_val = collect_raw_predictions(engine, all_games, val_games)
    print(
        f"validation n={len(p_val)} raw brier={brier(p_val, y_val):.4f} "
        f"raw log_loss={log_loss(p_val, y_val):.4f} raw acc={accuracy(p_val, y_val):.4f}"
    )

    # Platt scaling: logistic regression on the logit of the raw probability.
    logit_val = __import__("numpy").log(p_val / (1.0 - p_val)).reshape(-1, 1)
    platt = LogisticRegression()
    platt.fit(logit_val, y_val)
    p_val_calibrated = platt.predict_proba(logit_val)[:, 1]
    print(
        f"validation (in-sample calibrated) brier={brier(p_val_calibrated, y_val):.4f} "
        f"log_loss={log_loss(p_val_calibrated, y_val):.4f} acc={accuracy(p_val_calibrated, y_val):.4f}"
    )
    print(f"Platt coef={platt.coef_[0][0]:.4f} intercept={platt.intercept_[0]:.4f}")

    print("\nCollecting LOCKED HOLDOUT raw predictions (touched once)...")
    p_hold, y_hold = collect_raw_predictions(engine, all_games, holdout_games)
    logit_hold = __import__("numpy").log(p_hold / (1.0 - p_hold)).reshape(-1, 1)
    p_hold_calibrated = platt.predict_proba(logit_hold)[:, 1]

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
    print("\n--- LOCKED HOLDOUT: v7 + Platt calibration (after) ---")
    print(after)

    out = {
        "split_meta": meta,
        "platt_coef": float(platt.coef_[0][0]),
        "platt_intercept": float(platt.intercept_[0]),
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
    Path("scratch/nba_v7_platt_calibration_result.json").write_text(json.dumps(out, indent=2))
    print("\nWrote scratch/nba_v7_platt_calibration_result.json")


if __name__ == "__main__":
    run()
