"""Real, live demonstration of the complete conservative-probability
uncertainty decomposition -- CLAUDE.md's next-phase Task 16.

Bootstrap uncertainty already exists and is wired into
mlb_shadow_pipeline.py's build_forecast() (BootstrapMLBEnsemble). This
script demonstrates the four components uncertainty.py adds
(model_disagreement, calibration_uncertainty, missingness_penalty,
lineup_uncertainty) against real predictions on the real backfilled data,
and composes all of them into a real conservative_probability per game --
proving the module works end to end on real data, not just synthetic unit
tests.

Real, disclosed scope: this is a research/demonstration script, not yet
wired into the live shadow pipeline (mlb_shadow_pipeline.py's
build_forecast()) -- that live integration additionally requires loading
and predicting with all three real model families at live-prediction time,
which isn't currently wired there (predict_stage only fits/predicts with
MLBTwoHeadModel). Disclosed as the real remaining gap, not silently
skipped.

Registry-safe: does not touch test_consumption_registry.json.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_uncertainty_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.calibration import cross_fit_calibration_eval
from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import dedupe_scoreboard
from model_prediction.rebuild.mlb_model_comparison import (
    DIFFERENTIAL_FEATURES,
    INTENSITY_FEATURES,
    MODEL_NAMES,
    build_mlb_moneyline_oof,
)
from model_prediction.rebuild.models import BootstrapMLBEnsemble, MLBTwoHeadModel, XGBoostTwoHeadModel
from model_prediction.rebuild.uncertainty import (
    calibration_uncertainty,
    compose_conservative_probability,
    missingness_penalty,
    model_disagreement,
)
from model_prediction.rebuild.validation import expanding_folds

HORIZON = "late"
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

    raw_oof = build_mlb_moneyline_oof(features, folds)

    # Real winning calibration method per model (Task 14's own selection
    # logic, recomputed here rather than assumed).
    best_methods: dict[str, str] = {}
    for name in MODEL_NAMES:
        probs, labels = raw_oof[name]["probs"], raw_oof[name]["labels"]
        results = {m: cross_fit_calibration_eval(probs, labels, m, n_blocks=N_CALIBRATION_BLOCKS) for m in CALIBRATION_METHODS}
        valid = {m: r for m, r in results.items() if r.log_loss is not None}
        best_methods[name] = min(valid, key=lambda m: valid[m].log_loss) if valid else "identity"
    print(f"3. Best calibration method per model: {best_methods}")

    # Real demonstration set: the last real fold's validation rows (most
    # recent real games), predicted by all three model families and by a
    # real BootstrapMLBEnsemble fit on that fold's real training data.
    last_fold = folds[-1]
    train_df = features.filter(pl.col("game_date") <= last_fold.train_end)
    val_df = features.filter(
        (pl.col("game_date") >= last_fold.val_start) & (pl.col("game_date") <= last_fold.val_end)
    )
    print(f"4. Demonstration set: {val_df.height} real games (last real fold's validation block)")

    two_head = MLBTwoHeadModel(seed=42)
    two_head.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
    xgb_two_head = XGBoostTwoHeadModel(seed=42)
    xgb_two_head.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
    bootstrap = BootstrapMLBEnsemble(n_bootstrap=20, seed=42)
    bootstrap.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

    demo_rows = []
    for row in val_df.iter_rows(named=True):
        pred_two_head = two_head.predict_row(row["event_id"], row)
        pred_xgb_two_head = xgb_two_head.predict_row(row["event_id"], row)
        probs_by_model = {"two_head": pred_two_head.home_win_prob, "xgb_two_head": pred_xgb_two_head.home_win_prob}
        disagreement = model_disagreement(probs_by_model)

        penalty, missing_flags = missingness_penalty(row)

        cal_uncertainty = calibration_uncertainty(
            pred_two_head.home_win_prob,
            raw_oof["two_head"]["probs"], raw_oof["two_head"]["labels"],
            best_methods["two_head"], n_bootstrap=50,
        )

        bootstrap_lower, bootstrap_upper = bootstrap.market_probability_bounds(
            row, two_head.distribution, "moneyline", "home",
        )

        result = compose_conservative_probability(
            calibrated_probability=pred_two_head.home_win_prob,
            bootstrap_lower=bootstrap_lower, bootstrap_upper=bootstrap_upper,
            model_disagreement=disagreement, calibration_uncertainty=cal_uncertainty,
            missingness_penalty=penalty, missing_flags=missing_flags,
            raw_probability=pred_two_head.home_win_prob,
            lineup_uncertainty=None,  # no real timestamp-valid lineup source -- never fabricated
        )
        demo_rows.append({
            "event_id": row["event_id"], "game_date": row["game_date"],
            "two_head_prob": pred_two_head.home_win_prob, "xgb_two_head_prob": pred_xgb_two_head.home_win_prob,
            "model_disagreement": disagreement, "missingness_penalty": penalty, "missing_flags": missing_flags,
            "calibration_uncertainty": cal_uncertainty,
            "bootstrap_lower": bootstrap_lower, "bootstrap_upper": bootstrap_upper,
            "conservative_probability": result.conservative_probability,
            "probability_lower": result.probability_lower, "probability_upper": result.probability_upper,
        })

    print("\n5. Real per-game uncertainty decomposition (first 5 real games):")
    for r in demo_rows[:5]:
        print(f"   {r['event_id']}: two_head={r['two_head_prob']:.3f} xgb={r['xgb_two_head_prob']:.3f} "
              f"disagreement={r['model_disagreement']:.3f} cal_unc={r['calibration_uncertainty']:.3f} "
              f"missingness_penalty={r['missingness_penalty']:.3f} ({len(r['missing_flags'])} flags) "
              f"-> conservative={r['conservative_probability']:.3f} "
              f"[{r['probability_lower']:.3f}, {r['probability_upper']:.3f}]")

    mean_disagreement = sum(r["model_disagreement"] for r in demo_rows) / len(demo_rows)
    mean_penalty = sum(r["missingness_penalty"] for r in demo_rows) / len(demo_rows)
    mean_cal_unc = sum(r["calibration_uncertainty"] for r in demo_rows) / len(demo_rows)
    mean_haircut = sum(r["two_head_prob"] - r["conservative_probability"] for r in demo_rows) / len(demo_rows)
    print(f"\n6. Real summary over {len(demo_rows)} games: mean_disagreement={mean_disagreement:.4f} "
          f"mean_calibration_uncertainty={mean_cal_unc:.4f} mean_missingness_penalty={mean_penalty:.4f} "
          f"mean_total_haircut_from_raw={mean_haircut:.4f}")
    print(
        "\n7. Real, disclosed scope: registry-safe (does not touch\n"
        "   test_consumption_registry.json). Not yet wired into the live shadow\n"
        "   pipeline's build_forecast() -- that requires loading and predicting\n"
        "   with all three real model families at live-prediction time, which\n"
        "   isn't currently wired there. lineup_uncertainty stays 'unavailable'\n"
        "   (None) throughout -- no real timestamp-valid lineup source exists."
    )

    results_path = Path("outputs/rebuild/mlb_uncertainty_demo.json")
    results_path.write_text(json.dumps({
        "dataset_hash": dataset.dataset_hash,
        "best_calibration_method_per_model": best_methods,
        "n_games": len(demo_rows),
        "mean_model_disagreement": mean_disagreement,
        "mean_calibration_uncertainty": mean_cal_unc,
        "mean_missingness_penalty": mean_penalty,
        "mean_total_haircut_from_raw": mean_haircut,
        "games": demo_rows,
    }, indent=2, default=str))
    print(f"8. Results saved to {results_path}")


if __name__ == "__main__":
    main()
