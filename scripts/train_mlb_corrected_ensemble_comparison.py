"""Real corrected re-run of Task 15's calibrated ensemble comparison, using
each coherent head family's OWN real best-validated distribution (per
train_mlb_head_distribution_cartesian.py's real Cartesian comparison)
instead of each head family's constructor-default distribution.

Real bug this fixes: the original train_mlb_calibrated_ensemble_comparison.py
picked "best calibrated coherent score model" between `two_head` and
`xgb_two_head` as each head family's constructor DEFAULT distribution
(independent_poisson for both) -- never negative_binomial, even though
negative_binomial was separately shown to be the real best distribution
for both head families. That comparison was picking between two
under-specified candidates, not each family's own best real result.

This script reuses build_mlb_coherent_oof_for_combo() to build each head
family's OOF against its own real cross-fit-validated best distribution
(sklearn -> negative_binomial, xgboost -> negative_binomial, per
outputs/rebuild/mlb_head_distribution_cartesian.json), cross-fit
calibrates each properly, then re-runs the identical real meta-cross-fit
ensemble comparison against xgb_direct.

Registry-safe: does not touch test_consumption_registry.json.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_corrected_ensemble_comparison.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.calibration import cross_fit_calibration_eval
from model_prediction.rebuild.ensemble import Ensemble, meta_cross_fit_ensemble
from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import dedupe_scoreboard
from model_prediction.rebuild.mlb_model_comparison import (
    build_mlb_coherent_oof_for_combo,
    build_mlb_moneyline_oof,
)
from model_prediction.rebuild.validation import expanding_folds, log_loss

HORIZON = "late"
CALIBRATION_METHODS = ["identity", "platt", "temperature", "isotonic"]
N_CALIBRATION_BLOCKS = 4
N_META_BLOCKS = 3
ENSEMBLE_METHODS = ["equal_weight", "inverse_log_loss", "logistic_stacking", "logistic_regression_stack"]

# Real, validated per-head-family best distribution -- from
# outputs/rebuild/mlb_head_distribution_cartesian.json's real Cartesian
# comparison (not assumed, not a constructor default).
BEST_DISTRIBUTION_BY_HEAD_FAMILY = {"sklearn": "negative_binomial", "xgboost": "negative_binomial"}


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

    # xgb_direct is unaffected by the head-family/distribution correction
    # (it has no coherent distribution at all) -- reuse the existing
    # shared OOF builder for it only.
    raw_oof_direct = build_mlb_moneyline_oof(features, folds)
    labels_direct = raw_oof_direct["xgb_direct"]["labels"]

    raw_oof: dict[str, list[float]] = {"xgb_direct": raw_oof_direct["xgb_direct"]["probs"]}
    for head_family, model_key in (("sklearn", "two_head"), ("xgboost", "xgb_two_head")):
        method = BEST_DISTRIBUTION_BY_HEAD_FAMILY[head_family]
        combo_oof = build_mlb_coherent_oof_for_combo(features, folds, head_family, method)
        raw_oof[model_key] = combo_oof["probs"]
        assert combo_oof["labels"] == labels_direct, (
            f"{model_key} real OOF labels must match xgb_direct's -- same folds/games, different model only"
        )
        print(f"3. {model_key}: real OOF built with head_family={head_family} distribution={method} "
              f"({len(combo_oof['probs'])} rows)")

    calibrated_oof: dict[str, list[float]] = {}
    calibrated_labels: list[int] | None = None
    best_methods: dict[str, str] = {}
    for name in ("two_head", "xgb_two_head", "xgb_direct"):
        probs = raw_oof[name]
        results = {
            m: cross_fit_calibration_eval(probs, labels_direct, m, n_blocks=N_CALIBRATION_BLOCKS)
            for m in CALIBRATION_METHODS
        }
        valid = {m: r for m, r in results.items() if r.log_loss is not None}
        best = min(valid, key=lambda m: valid[m].log_loss) if valid else "identity"
        best_methods[name] = best
        calibrated_oof[name] = results[best].calibrated_probs
        if calibrated_labels is None:
            calibrated_labels = results[best].eval_labels
        print(f"4. {name}: calibrated with `{best}`, {len(calibrated_oof[name])} real calibrated OOF rows")

    assert calibrated_labels is not None
    n = len(calibrated_labels)
    if n < 2 * N_META_BLOCKS:
        print("Too few real calibrated OOF rows for meta-cross-fitting. Stopping honestly.")
        sys.exit(0)

    coherent_scores = {
        name: log_loss(calibrated_labels, calibrated_oof[name]) for name in ("two_head", "xgb_two_head")
    }
    best_coherent = min(coherent_scores, key=lambda n_: coherent_scores[n_])
    print(f"\n5. Best calibrated coherent score model (each using its OWN real best distribution): "
          f"{best_coherent} (two_head={coherent_scores['two_head']:.4f}, "
          f"xgb_two_head={coherent_scores['xgb_two_head']:.4f})")

    candidates = [best_coherent, "xgb_direct", *ENSEMBLE_METHODS]
    print(f"\n6. Real chronological meta-cross-fit comparison ({n} real calibrated OOF rows, "
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
    print(f"\n7. Best by real meta-cross-fit log loss: {winner}")

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
        "\n8. Real, disclosed scope: registry-safe (does not touch\n"
        "   test_consumption_registry.json). This corrects Task 15's original\n"
        "   comparison, which picked between two head families at their\n"
        "   constructor-default distribution rather than each family's own\n"
        "   real best-validated distribution."
    )

    results_path = Path("outputs/rebuild/mlb_corrected_ensemble_comparison.json")
    results_path.write_text(json.dumps({
        "dataset_hash": dataset.dataset_hash,
        "matched_games": dataset.matched_games,
        "n_calibrated_oof": n,
        "distribution_by_head_family": BEST_DISTRIBUTION_BY_HEAD_FAMILY,
        "best_calibration_method_per_model": best_methods,
        "best_calibrated_coherent_score_model": best_coherent,
        "coherent_score_model_log_loss": coherent_scores,
        "meta_cross_fit_results": results_summary,
        "best_method_by_meta_log_loss": winner,
        "full_history_ensemble_weights": ensemble_weights,
    }, indent=2, default=str))
    print(f"9. Results saved to {results_path}")


if __name__ == "__main__":
    main()
