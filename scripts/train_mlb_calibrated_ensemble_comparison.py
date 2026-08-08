"""Real chronologically meta-cross-fitted MLB ensemble comparison, using
CALIBRATED predictions -- CLAUDE.md's next-phase Task 15.

Run AFTER calibration (Task 14) deliberately: individual model calibration
can materially change ensemble behavior (a miscalibrated model can look
artificially strong or weak to a stacker fit on raw probabilities).

For each of the three real model families (two_head, xgb_two_head,
xgb_direct), takes the SAME real chronological cross-fit calibration
train_mlb_calibration_comparison.py already validated (the winning method
per model, never forced to a non-identity choice) and produces a real,
no-leak calibrated OOF sequence -- reusing
calibration.cross_fit_calibration_eval()'s own output rather than
recomputing calibration differently here, so the two scripts can never
silently disagree about what "the calibrated prediction for row i" means.

Compares, via real chronological meta-cross-fitting
(ensemble.meta_cross_fit_ensemble() -- the meta-model is only ever fit on
strictly earlier rows than the block it is scored on):

    1. the strongest single calibrated coherent score model (two_head or
       xgb_two_head, whichever calibrates better)
    2. the calibrated direct XGBoost moneyline challenger (xgb_direct)
    3. equal-weight ensemble
    4. inverse-log-loss weighting
    5. nonnegative constrained stack (logistic_stacking)
    6. logistic regression stack (logistic_regression_stack)

If the ensemble collapses to effectively one model's weight, that is
reported plainly as "the ensemble adds no value" -- CLAUDE.md's own
instruction -- not hidden behind an ensemble class that exists regardless.

Registry-safe: does not touch test_consumption_registry.json.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_calibrated_ensemble_comparison.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.calibration import cross_fit_calibration_eval
from model_prediction.rebuild.ensemble import meta_cross_fit_ensemble
from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import dedupe_scoreboard
from model_prediction.rebuild.mlb_model_comparison import MODEL_NAMES, build_mlb_moneyline_oof
from model_prediction.rebuild.validation import expanding_folds

HORIZON = "late"
CALIBRATION_METHODS = ["identity", "platt", "temperature", "isotonic"]
N_CALIBRATION_BLOCKS = 4
N_META_BLOCKS = 3
ENSEMBLE_METHODS = ["equal_weight", "inverse_log_loss", "logistic_stacking", "logistic_regression_stack"]


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

    raw_oof = build_mlb_moneyline_oof(features, folds)

    # Step 1: apply Task 14's own real, no-leak chronological calibration
    # cross-fit to each model -- reusing cross_fit_calibration_eval()'s
    # calibrated_probs/eval_labels directly, not recomputing calibration
    # a second, potentially-inconsistent way.
    calibrated_oof: dict[str, list[float]] = {}
    calibrated_labels: list[int] | None = None
    best_methods: dict[str, str] = {}
    for name in MODEL_NAMES:
        probs, labels = raw_oof[name]["probs"], raw_oof[name]["labels"]
        results = {
            m: cross_fit_calibration_eval(probs, labels, m, n_blocks=N_CALIBRATION_BLOCKS)
            for m in CALIBRATION_METHODS
        }
        valid = {m: r for m, r in results.items() if r.log_loss is not None}
        best = min(valid, key=lambda m: valid[m].log_loss) if valid else "identity"
        best_methods[name] = best
        calibrated_oof[name] = results[best].calibrated_probs
        if calibrated_labels is None:
            calibrated_labels = results[best].eval_labels
        print(f"3. {name}: calibrated with `{best}` (Task 14's own winner), "
              f"{len(calibrated_oof[name])} real calibrated OOF rows")

    assert calibrated_labels is not None
    n = len(calibrated_labels)
    if n < 2 * N_META_BLOCKS:
        print("Too few real calibrated OOF rows for meta-cross-fitting. Stopping honestly.")
        sys.exit(0)

    # Step 2: the strongest single calibrated coherent score model
    # (two_head or xgb_two_head) is the real "best coherent score model"
    # baseline -- decided by real calibrated log loss over the identical
    # rows, not assumed.
    from model_prediction.rebuild.validation import log_loss as _log_loss

    coherent_scores = {
        name: _log_loss(calibrated_labels, calibrated_oof[name])
        for name in ("two_head", "xgb_two_head")
    }
    best_coherent = min(coherent_scores, key=lambda n_: coherent_scores[n_])
    print(f"\n4. Best calibrated coherent score model: {best_coherent} "
          f"(two_head={coherent_scores['two_head']:.4f}, xgb_two_head={coherent_scores['xgb_two_head']:.4f})")

    candidates = [best_coherent, "xgb_direct", *ENSEMBLE_METHODS]
    print(f"\n5. Real chronological meta-cross-fit comparison ({n} real calibrated OOF rows, "
          f"{N_META_BLOCKS} meta-blocks, first block fit-only):")
    results_summary = {}
    for method in candidates:
        result = meta_cross_fit_ensemble(calibrated_oof, calibrated_labels, method, n_blocks=N_META_BLOCKS)
        results_summary[method] = result
        ll = f"{result['log_loss']:.4f}" if result["log_loss"] is not None else "n/a"
        br = f"{result['brier']:.4f}" if result["brier"] is not None else "n/a"
        print(f"   {method:24s}: n_eval={result['n_eval_total']} log_loss={ll} brier={br}")

    valid_results = {m: r for m, r in results_summary.items() if r["log_loss"] is not None}
    winner = min(valid_results, key=lambda m: valid_results[m]["log_loss"]) if valid_results else None
    print(f"\n6. Best by real meta-cross-fit log loss: {winner}")

    # Real, honest ensemble-value check: fit each real ensemble method on
    # the FULL calibrated OOF history (post-selection, matching Task 14's
    # own "fit the winner on everything once the method is chosen"
    # pattern) and inspect its real learned weights -- if they collapse to
    # effectively one model, the ensemble adds no value, reported plainly.
    from model_prediction.rebuild.ensemble import Ensemble

    ensemble_weights = {}
    for method in ENSEMBLE_METHODS:
        ens = Ensemble(method=method)
        ens.fit(calibrated_oof, calibrated_labels)
        ensemble_weights[method] = dict(ens.weights)
        weight_str = ", ".join(f"{k}={v:.3f}" for k, v in ens.weights.items())
        print(f"   {method:24s} real learned weights: {weight_str}")
        max_weight = max(ens.weights.values()) if ens.weights else 0.0
        if max_weight > 0.9:
            dominant = max(ens.weights, key=lambda k: ens.weights[k])
            print(f"      -> collapses to ~{dominant} (weight={max_weight:.3f}): "
                  f"this ensemble method adds no real value over using {dominant} directly.")

    print(
        "\n7. Real, disclosed scope: registry-safe (does not touch\n"
        "   test_consumption_registry.json). No promotion decision is made here.\n"
        "   If any ensemble method's real learned weights collapse to effectively\n"
        "   one model, that is reported above as a real, honest finding, not hidden."
    )

    results_path = Path("outputs/rebuild/mlb_calibrated_ensemble_comparison.json")
    results_path.write_text(json.dumps({
        "dataset_hash": dataset.dataset_hash,
        "matched_games": dataset.matched_games,
        "n_calibrated_oof": n,
        "best_calibration_method_per_model": best_methods,
        "best_calibrated_coherent_score_model": best_coherent,
        "coherent_score_model_log_loss": coherent_scores,
        "meta_cross_fit_results": results_summary,
        "best_method_by_meta_log_loss": winner,
        "full_history_ensemble_weights": ensemble_weights,
    }, indent=2, default=str))
    print(f"8. Results saved to {results_path}")


if __name__ == "__main__":
    main()
