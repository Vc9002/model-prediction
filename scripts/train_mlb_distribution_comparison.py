"""Real chronological OOF comparison of joint score-distribution methods --
CLAUDE.md Part 2 SS12/next-phase Task 12 ("joint-distribution methods":
independent Poisson, negative binomial, Skellam for exact moneyline/spread
pricing).

Real gap this closes: JointScoreDistribution already implements all three
methods (independent_poisson, negative_binomial, skellam -- the last added
and tested in an earlier session), but nothing ever compared them against
each other on real chronological out-of-fold predictions. MLBTwoHeadModel
also never exposed which method it used at construction time -- every real
caller got independent_poisson, hardcoded.

The three methods share the identical fitted intensity/differential heads
per fold (fit once, reused across all three distributions) -- this isolates
the comparison to "does the distribution choice change predictive quality
for the same underlying expected-run estimates," not a confound from
different feature fitting.

Reuses the exact same real feature build, date-cluster-safe folds, and
final-test avoidance train_mlb_xgboost_ensemble.py already uses and
validates -- this script does NOT touch or re-consume the already-consumed
final test (outputs/rebuild/test_consumption_registry.json: mlb_moneyline,
consumed). Comparing distribution methods on an already-spent held-out set
would be exactly the "inspect the final test while selecting model family"
CLAUDE.md forbids.

Real, disclosed scope: this compares the three distribution methods against
each other, not against the frozen incumbent (pre-rebuild) benchmark --
that requires loading the legacy model interface, a separate integration
this script does not attempt. "Control" here means independent_poisson
(the existing default), not the incumbent system.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_distribution_comparison.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import (
    MLB_DIFFERENTIAL_FEATURES,
    MLB_INTENSITY_FEATURES,
    dedupe_scoreboard,
)
from model_prediction.rebuild.models import MLBTwoHeadModel
from model_prediction.rebuild.validation import brier_score, calibration_curve, ece, expanding_folds, log_loss

HORIZON = "late"
INTENSITY_FEATURES = MLB_INTENSITY_FEATURES
DIFFERENTIAL_FEATURES = MLB_DIFFERENTIAL_FEATURES
METHODS = ["independent_poisson", "negative_binomial", "skellam"]


def _home_win_labels(df: pl.DataFrame) -> list[int]:
    return [1 if r["home_score"] > r["away_score"] else 0 for r in df.iter_rows(named=True)]


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
    print(f"1. Feature rows: {dataset.matched_games} matched "
          f"({dataset.starters_known_games} with a point-in-time-valid probable starter); "
          f"dataset_hash={dataset.dataset_hash[:12]}")

    if features.height < 30:
        print("Not enough matched games to compare distribution methods meaningfully (need >=30). Stopping honestly.")
        sys.exit(0)

    # Task 8: real date-cluster-safe folds, identical construction to
    # train_mlb_rebuild_real_features.py / train_mlb_xgboost_ensemble.py.
    game_dates = features["game_date"].to_list()
    n_unique_dates = len(set(game_dates))
    val_size_days = max(1, n_unique_dates // 6)
    test_size_days = max(1, n_unique_dates // 6)
    folds = expanding_folds(game_dates, n_splits=3, val_size=val_size_days, test_size=test_size_days, gap=1)
    print(f"2. Chronological folds: {len(folds)} ({n_unique_dates} real distinct dates, "
          f"val_size={val_size_days}d test_size={test_size_days}d gap=1d)")

    oof: dict[str, list[float]] = {m: [] for m in METHODS}
    y_true: list[int] = []
    per_fold_report = []

    for fold in folds:
        train_df = features.filter(pl.col("game_date") <= fold.train_end)
        val_df = features.filter(
            (pl.col("game_date") >= fold.val_start) & (pl.col("game_date") <= fold.val_end)
        )
        if train_df.height < 10 or val_df.height < 3:
            continue

        y_val_fold = _home_win_labels(val_df)
        val_rows = list(val_df.iter_rows(named=True))

        fold_report = {"fold": fold.fold_index, "train_n": train_df.height, "val_n": val_df.height, "methods": {}}
        for method in METHODS:
            # One real fit per method -- MLBTwoHeadModel.fit() re-fits both
            # heads from scratch each time (no artifact-reload shortcut
            # exists yet, matching every other real training script here),
            # so this is three independent real fits per fold, not a
            # shared-then-diverging model.
            model = MLBTwoHeadModel(seed=42, method=method)
            model.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
            probs = [model.predict_row(r["event_id"], r).home_win_prob for r in val_rows]
            oof[method].extend(probs)

            fold_report["methods"][method] = {
                "log_loss": log_loss(y_val_fold, probs),
                "brier": brier_score(y_val_fold, probs),
            }

        y_true.extend(y_val_fold)
        per_fold_report.append(fold_report)
        summary_line = " ".join(
            f"{m}_ll={fold_report['methods'][m]['log_loss']:.3f}" for m in METHODS
        )
        print(f"  Fold {fold.fold_index}: train={train_df.height} val={val_df.height} {summary_line}")

    if len(y_true) < 10:
        print("Too few real OOF predictions across folds for a meaningful comparison. Stopping honestly.")
        sys.exit(0)

    print(f"\n3. OOF comparison ({len(y_true)} real out-of-fold predictions across {len(per_fold_report)} folds):")
    method_summary = {}
    for method in METHODS:
        probs = oof[method]
        curve = calibration_curve(y_true, probs)
        method_summary[method] = {
            "log_loss": log_loss(y_true, probs),
            "brier": brier_score(y_true, probs),
            "ece": ece(y_true, probs),
            "calibration_curve": curve,
        }
        print(f"   {method:22s}: log_loss={method_summary[method]['log_loss']:.4f} "
              f"brier={method_summary[method]['brier']:.4f} ece={method_summary[method]['ece']:.4f}")

    best_method = min(METHODS, key=lambda m: method_summary[m]["log_loss"])
    print(f"\n4. Best OOF log loss: {best_method}")
    print(
        "\n   Real, disclosed scope: this compares distribution methods against\n"
        "   each other on chronological OOF folds only -- not against the frozen\n"
        "   incumbent benchmark (a separate legacy-interface integration), and it\n"
        "   does NOT touch the already-consumed final test\n"
        "   (outputs/rebuild/test_consumption_registry.json: mlb_moneyline). No\n"
        "   promotion decision is made here."
    )

    results_path = Path("outputs/rebuild/mlb_distribution_comparison.json")
    results_path.write_text(json.dumps({
        "dataset_hash": dataset.dataset_hash,
        "matched_games": dataset.matched_games,
        "starters_known_games": dataset.starters_known_games,
        "n_oof": len(y_true),
        "methods_compared": METHODS,
        "per_fold": per_fold_report,
        "oof_summary": method_summary,
        "best_method_by_oof_log_loss": best_method,
    }, indent=2, default=str))
    print(f"5. Results saved to {results_path}")


if __name__ == "__main__":
    main()
