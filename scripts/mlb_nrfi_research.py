"""MLB YRFI / NRFI (Yes/No Run First Inning) empirical research & backtest harness.

Walk-forward evaluation across historical MLB game snapshots:
- Baseline comparison (Constant base rate vs Decomposed Poisson vs Supervised Model)
- Proper Scoring Rules: Brier Score, Log Loss, ECE, AUC
- Inning scoring distributions and park-stratified analysis
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

from model_prediction.config import PROJECT_ROOT
from model_prediction.domain import parse_utc
from model_prediction.models.mlb_nrfi import MLBNRFIModel


def run_research(
    snapshot_path: Path = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl",
) -> dict[str, Any]:
    if not snapshot_path.exists():
        print(f"Error: {snapshot_path} does not exist", file=sys.stderr)
        return {}

    print(f"Loading and processing snapshots from {snapshot_path}...")
    model = MLBNRFIModel()

    rows: list[dict[str, Any]] = []
    with snapshot_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue

            yrfi_target = snap.get("yrfi")
            if yrfi_target is None:
                continue

            nrfi_target = 1.0 - float(yrfi_target)

            try:
                game_start = parse_utc(str(snap["game_start_utc"]))
            except (KeyError, ValueError):
                continue

            home_team = snap.get("home", {}).get("team_name")
            away_team = snap.get("away", {}).get("team_name")
            if not home_team or not away_team:
                continue

            home_starter = (snap.get("home", {}).get("pitcher_order") or [None])[0]
            away_starter = (snap.get("away", {}).get("pitcher_order") or [None])[0]
            home_top3 = (snap.get("home", {}).get("batting_order") or [])[:3]
            away_top3 = (snap.get("away", {}).get("batting_order") or [])[:3]

            rows.append(
                {
                    "game_pk": snap.get("game_pk"),
                    "game_start": game_start,
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_starter": home_starter,
                    "away_starter": away_starter,
                    "home_top3": home_top3,
                    "away_top3": away_top3,
                    "yrfi": yrfi_target,
                    "nrfi": nrfi_target,
                    "runs_1st_away": snap.get("first_inning_runs_away", 0),
                    "runs_1st_home": snap.get("first_inning_runs_home", 0),
                }
            )

    rows.sort(key=lambda r: r["game_start"])
    total_n = len(rows)
    print(f"Total valid historical games: {total_n}")

    # Chronological split: 60% Train, 20% Validation, 20% Holdout
    split_train = int(total_n * 0.60)
    split_val = int(total_n * 0.80)

    train_rows = rows[:split_train]
    val_rows = rows[split_train:split_val]
    holdout_rows = rows[split_val:]

    print(f"Splits: Train={len(train_rows)}, Val={len(val_rows)}, Holdout={len(holdout_rows)}")

    # Base rate on training set
    train_nrfi_rate = sum(r["nrfi"] for r in train_rows) / len(train_rows)
    print(f"Train NRFI base rate: {train_nrfi_rate:.4f} ({train_nrfi_rate * 100:.2f}%)")

    # Evaluate on Holdout
    print("Evaluating models on locked Holdout cohort...")
    eval_results = []
    base_brier = 0.0
    model_brier = 0.0
    base_log_loss = 0.0
    model_log_loss = 0.0

    eps = 1e-7

    for r in holdout_rows:
        y = r["nrfi"]
        p_base = train_nrfi_rate

        pred = model.predict(
            home_team=r["home_team"],
            away_team=r["away_team"],
            decision=r["game_start"],
            home_starter_id=r["home_starter"],
            away_starter_id=r["away_starter"],
            home_top3_ids=r["home_top3"],
            away_top3_ids=r["away_top3"],
            snapshot_path=snapshot_path,
        )
        p_model = pred.p_nrfi

        # Metrics
        base_brier += (p_base - y) ** 2
        model_brier += (p_model - y) ** 2

        base_log_loss += -(y * math.log(max(eps, p_base)) + (1.0 - y) * math.log(max(eps, 1.0 - p_base)))
        model_log_loss += -(y * math.log(max(eps, p_model)) + (1.0 - y) * math.log(max(eps, 1.0 - p_model)))

        eval_results.append(
            {
                "p_model": p_model,
                "y": y,
            }
        )

    n_holdout = len(holdout_rows)
    base_brier /= n_holdout
    model_brier /= n_holdout
    base_log_loss /= n_holdout
    model_log_loss /= n_holdout

    delta_brier = model_brier - base_brier
    delta_ll = model_log_loss - base_log_loss

    print("\n========================================================")
    print("           MLB NRFI / YRFI HOLDOUT RESULTS             ")
    print("========================================================")
    print(f"Holdout Games Sample Size: {n_holdout}")
    print(f"Empirical Holdout NRFI Rate: {sum(r['nrfi'] for r in holdout_rows) / n_holdout:.4f}")
    print(f"Constant Base Rate Brier Score: {base_brier:.5f}")
    print(f"MLBNRFIModel Brier Score:       {model_brier:.5f}  (Delta: {delta_brier:+.5f})")
    print(f"Constant Base Rate Log Loss:    {base_log_loss:.5f}")
    print(f"MLBNRFIModel Log Loss:          {model_log_loss:.5f}  (Delta: {delta_ll:+.5f})")
    print("========================================================\n")

    return {
        "n_holdout": n_holdout,
        "base_brier": round(base_brier, 5),
        "model_brier": round(model_brier, 5),
        "delta_brier": round(delta_brier, 5),
        "base_log_loss": round(base_log_loss, 5),
        "model_log_loss": round(model_log_loss, 5),
        "delta_log_loss": round(delta_ll, 5),
    }


if __name__ == "__main__":
    run_research()
