"""Real Cartesian head-family x distribution comparison, with cross-fitted
calibration on each exact combination -- corrects a real model-freeze bug
found in Task 17/18's output.

Real bug this fixes: Task 12 (`train_mlb_distribution_comparison.py`)
compared independent_poisson / negative_binomial / skellam only against
`MLBTwoHeadModel` (sklearn ElasticNet + HistGradientBoostingRegressor
heads) and picked negative_binomial as the best distribution *for that
head family*. Task 13/14/15 (`train_mlb_score_model_comparison.py`,
`train_mlb_calibration_comparison.py`, `train_mlb_calibrated_ensemble_comparison.py`)
only ever constructed `XGBoostTwoHeadModel(seed=42)` -- its constructor
default is `method="independent_poisson"` -- and picked `xgb_two_head` as
the best calibrated coherent model. Task 18's registry then froze
"primary_moneyline_model: xgb_two_head" together with
"score_distribution_family: negative_binomial" as if that were one
validated combination. It was never fit or OOF-scored together anywhere.

This script actually fits and OOF-scores all 6 real combinations
(2 head families x 3 distributions), cross-fit-calibrates each with all 4
real calibration methods (identical method to Task 14), and picks the
real overall winner by cross-fit log loss -- as one exact combination
of head family + distribution + calibrator, never assembled from
separate experiments' winners.

Registry-safe: does not touch test_consumption_registry.json (this
script only reports; the registry correction is applied as a separate,
deliberate edit after reviewing this real output, same as Task 18).

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_head_distribution_cartesian.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.calibration import cross_fit_calibration_eval, fit_calibrator
from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import dedupe_scoreboard
from model_prediction.rebuild.mlb_model_comparison import build_mlb_coherent_oof_for_combo
from model_prediction.rebuild.validation import brier_score, expanding_folds, log_loss

HORIZON = "late"
HEAD_FAMILIES = ["sklearn", "xgboost"]
DISTRIBUTIONS = ["independent_poisson", "negative_binomial", "skellam"]
CALIBRATION_METHODS = ["identity", "platt", "temperature", "isotonic"]
N_CALIBRATION_BLOCKS = 4


def main() -> None:
    sb_path = Path("data/rebuild/normalized/mlb/scoreboard.parquet")
    if not sb_path.exists():
        print(f"ERROR: {sb_path} not found. Run the MLB collector first.")
        sys.exit(1)

    sb = dedupe_scoreboard(pl.read_parquet(sb_path))
    completed = sb.filter(pl.col("status") == "STATUS_FINAL").sort("event_start_utc")
    if completed.height == 0:
        print("No completed games. Stopping honestly.")
        sys.exit(0)
    start_date = completed["event_start_utc"][0][:10]
    end_date = completed["event_start_utc"][-1][:10]

    dataset = build_mlb_historical_horizon_dataset("data/rebuild", start_date, end_date, HORIZON)
    features = dataset.features.sort("event_start_utc") if dataset.features.height else dataset.features
    print(f"1. Feature rows: {dataset.matched_games} matched; dataset_hash={dataset.dataset_hash[:12]}")

    if features.height < 30:
        print("Not enough matched games. Stopping honestly.")
        sys.exit(0)

    game_dates = features["game_date"].to_list()
    n_unique_dates = len(set(game_dates))
    val_size_days = max(1, n_unique_dates // 6)
    test_size_days = max(1, n_unique_dates // 6)
    folds = expanding_folds(game_dates, n_splits=3, val_size=val_size_days, test_size=test_size_days, gap=1)
    print(f"2. Chronological folds: {len(folds)} ({n_unique_dates} real distinct dates)")

    combo_results: dict[str, dict] = {}
    combo_oof: dict[str, dict] = {}
    print("\n3. Real per-combination OOF + cross-fit calibration:")
    for head_family in HEAD_FAMILIES:
        for method in DISTRIBUTIONS:
            combo_name = f"{head_family}__{method}"
            oof = build_mlb_coherent_oof_for_combo(features, folds, head_family, method)
            combo_oof[combo_name] = oof
            n_oof = len(oof["probs"])
            if n_oof < 20:
                print(f"   {combo_name:35s}: too few real OOF predictions ({n_oof}), skipping calibration")
                combo_results[combo_name] = {
                    "head_family": head_family, "distribution": method, "n_oof": n_oof,
                    "raw_log_loss": None, "raw_brier": None,
                    "calibration_methods": {}, "best_calibration_method": None,
                    "best_cross_fit_log_loss": None, "best_cross_fit_brier": None, "best_cross_fit_ece": None,
                }
                continue

            raw_ll = log_loss(oof["labels"], oof["probs"])
            raw_brier = brier_score(oof["labels"], oof["probs"])

            cal_results = {
                m: cross_fit_calibration_eval(oof["probs"], oof["labels"], m, n_blocks=N_CALIBRATION_BLOCKS)
                for m in CALIBRATION_METHODS
            }
            valid = {m: r for m, r in cal_results.items() if r.log_loss is not None}
            best_method = min(valid, key=lambda m: valid[m].log_loss) if valid else None
            best = valid[best_method] if best_method else None

            combo_results[combo_name] = {
                "head_family": head_family, "distribution": method, "n_oof": n_oof,
                "raw_log_loss": raw_ll, "raw_brier": raw_brier,
                "calibration_methods": {
                    m: {"log_loss": r.log_loss, "brier": r.brier, "ece": r.ece, "n_eval_total": r.n_eval_total}
                    for m, r in cal_results.items()
                },
                "best_calibration_method": best_method,
                "best_cross_fit_log_loss": best.log_loss if best else None,
                "best_cross_fit_brier": best.brier if best else None,
                "best_cross_fit_ece": best.ece if best else None,
                "best_cross_fit_n": best.n_eval_total if best else None,
            }
            best_ll_str = f"{best.log_loss:.4f}" if best else "n/a"
            print(f"   {combo_name:35s}: n_oof={n_oof:3d} raw_ll={raw_ll:.4f} "
                  f"best_cal={best_method} best_cross_fit_ll={best_ll_str}")

    ranked = sorted(
        (c for c in combo_results.values() if c["best_cross_fit_log_loss"] is not None),
        key=lambda c: c["best_cross_fit_log_loss"],
    )
    if not ranked:
        print("\nNo combination produced a real cross-fit result. Stopping honestly.")
        sys.exit(0)

    winner = ranked[0]
    print(f"\n4. Real overall winner (min cross-fit log loss across all {len(ranked)} scored combinations):")
    print(f"   head_family={winner['head_family']} distribution={winner['distribution']} "
          f"calibration={winner['best_calibration_method']} "
          f"cross_fit_log_loss={winner['best_cross_fit_log_loss']:.4f} "
          f"cross_fit_brier={winner['best_cross_fit_brier']:.4f} "
          f"cross_fit_ece={winner['best_cross_fit_ece']:.4f} (n={winner['best_cross_fit_n']})")

    # Real, persisted calibrator for the exact winning combination -- refit
    # on its full real OOF history now that cross-fitting has already
    # validated the (head_family, distribution, method) choice
    # out-of-sample. Named distinctly from the pre-existing
    # mlb-xgb_two_head-calibrator-v1.json (Task 14's artifact, fit against
    # the *default-Poisson* xgb_two_head OOF) so the two are never
    # confused -- that combination is a real, separately valid result for
    # its own (unvalidated-as-frozen) purpose, not overwritten here.
    winner_combo_name = f"{winner['head_family']}__{winner['distribution']}"
    winner_oof = combo_oof[winner_combo_name]
    final_calibrator = fit_calibrator(winner["best_calibration_method"], winner_oof["probs"], winner_oof["labels"])
    base_model_hash = hashlib.sha256(json.dumps(
        {"head_family": winner["head_family"], "distribution": winner["distribution"], "dataset_hash": dataset.dataset_hash},
        sort_keys=True,
    ).encode()).hexdigest()
    artifact_model_name = f"xgb_two_head_{winner['distribution']}" if winner["head_family"] == "xgboost" else f"two_head_{winner['distribution']}"
    calibrator_artifact = {
        "model_name": artifact_model_name,
        "head_family": winner["head_family"],
        "distribution": winner["distribution"],
        "method": winner["best_calibration_method"],
        "parameters": final_calibrator.parameters,
        "base_model_hash": base_model_hash,
        "dataset_hash": dataset.dataset_hash,
        "training_range": {"start": start_date, "end": end_date},
        "n_training_oof": len(winner_oof["probs"]),
    }
    calibrator_artifact["calibrator_hash"] = hashlib.sha256(
        json.dumps(calibrator_artifact, sort_keys=True, default=str).encode()
    ).hexdigest()
    # Added after calibrator_hash is computed -- these are supplementary
    # real evidence (the exact OOF rows the calibrator was fit on), not
    # part of the calibrator's own identity, so calibrator_hash stays
    # stable and doesn't change if this evidence is ever refreshed
    # separately. MLB-5 (multi-sport execution spec) needs these live: a
    # real calibration_uncertainty bootstrap requires resampling the
    # actual calibration-fitting data, which nothing else persists.
    calibrator_artifact["oof_probs"] = winner_oof["probs"]
    calibrator_artifact["oof_labels"] = winner_oof["labels"]
    artifact_dir = Path("config/models/challengers")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"mlb-{artifact_model_name}-calibrator-v1.json"
    artifact_path.write_text(json.dumps(calibrator_artifact, indent=2, default=str))
    print(f"6. Real winning combination's calibrator artifact saved to {artifact_path}")

    print("\n7. Real, disclosed scope: registry-safe (does not touch test_consumption_registry.json).")
    print("   Correcting mlb_moneyline_v2's frozen_choices in the registry to point at this")
    print("   exact validated combination is a separate, deliberate edit performed next.")

    results_path = Path("outputs/rebuild/mlb_head_distribution_cartesian.json")
    results_path.write_text(json.dumps({
        "dataset_hash": dataset.dataset_hash,
        "matched_games": dataset.matched_games,
        "n_calibration_blocks": N_CALIBRATION_BLOCKS,
        "combinations": combo_results,
        "ranked_by_cross_fit_log_loss": [
            {"combo": f"{c['head_family']}__{c['distribution']}", "calibration": c["best_calibration_method"],
             "cross_fit_log_loss": c["best_cross_fit_log_loss"]}
            for c in ranked
        ],
        "winner": {
            "head_family": winner["head_family"], "distribution": winner["distribution"],
            "calibration_method": winner["best_calibration_method"],
            "calibrator_artifact_path": str(artifact_path),
        },
    }, indent=2, default=str))
    print(f"8. Results saved to {results_path}")


if __name__ == "__main__":
    main()
