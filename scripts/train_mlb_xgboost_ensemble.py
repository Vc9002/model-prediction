"""Real chronological OOF comparison: MLBTwoHeadModel vs. XGBoostChallenger,
combined via Ensemble -- CLAUDE.md Part 2 SS3/SS13 ("Add XGBoost... Build
OOF ensemble").

Real gap this closes: XGBoostChallenger (xgboost_stress.py) and Ensemble
(ensemble.py) were both real, complete, CLAUDE.md-compliant implementations
with zero real callers anywhere in this codebase (grep-verified) -- the
model-family ensemble machinery existed but never actually combined two
independently-trained model families' real out-of-fold predictions.

Reuses the exact same real feature build, backfill, and chronological folds
train_mlb_rebuild_real_features.py already uses and validates -- this script
does NOT touch or re-consume the already-consumed final test
(outputs/rebuild/test_consumption_registry.json: mlb_moneyline, consumed).
Comparing model families on an already-spent held-out set would be exactly
the "inspect the final test while selecting model family" CLAUDE.md
forbids. Everything here is OOF-fold-only, matching how the original
fold_metrics were computed.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_xgboost_ensemble.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.ensemble import Ensemble
from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import (
    MLB_DIFFERENTIAL_FEATURES,
    MLB_INTENSITY_FEATURES,
    dedupe_scoreboard,
)
from model_prediction.rebuild.models import MLBTwoHeadModel
from model_prediction.rebuild.validation import brier_score, ece, expanding_folds, log_loss
from model_prediction.rebuild.xgboost_stress import nested_xgboost_fold

HORIZON = "late"

# Task 5: the one shared feature-list definition (mlb_features.py), also
# used by train_mlb_rebuild_real_features.py and mlb_shadow_pipeline.py --
# not redefined here. Deduplicated (not a plain concatenation) since both
# lists now share the real `*_availability` indicator columns.
INTENSITY_FEATURES = MLB_INTENSITY_FEATURES
DIFFERENTIAL_FEATURES = MLB_DIFFERENTIAL_FEATURES
XGB_FEATURES = list(dict.fromkeys(INTENSITY_FEATURES + DIFFERENTIAL_FEATURES))


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

    # Task 4: the one authoritative historical dataset builder, shared with
    # train_mlb_rebuild_real_features.py, train_mlb_feature_ablation.py, and
    # mlb_shadow_pipeline.py's walk-forward retraining.
    dataset = build_mlb_historical_horizon_dataset("data/rebuild", start_date, end_date, HORIZON)
    features = dataset.features.sort("event_start_utc") if dataset.features.height else dataset.features
    starters_known = dataset.starters_known_games
    print(f"1. Feature rows: {dataset.matched_games} matched ({starters_known} with a point-in-time-valid "
          f"probable starter for both teams at horizon={HORIZON}, {dataset.matched_games - starters_known} "
          f"flagged starters_known=0); dataset_hash={dataset.dataset_hash[:12]}")

    if features.height < 30:
        print("Not enough matched games to compare model families meaningfully (need >=30). Stopping honestly.")
        sys.exit(0)

    # Task 8 fix: same real date-cluster-safe fold shape as
    # train_mlb_rebuild_real_features.py -- sized in real distinct
    # calendar dates, not game count, so no real calendar date's games
    # can land on opposite sides of a fold boundary. Kept textually
    # identical to that script's own fold construction (not imported as a
    # shared helper) since both build folds from the same real feature
    # table independently and must agree by construction, not by a
    # runtime dependency between the two scripts.
    game_dates = features["game_date"].to_list()
    n_unique_dates = len(set(game_dates))
    val_size_days = max(1, n_unique_dates // 6)
    test_size_days = max(1, n_unique_dates // 6)
    folds = expanding_folds(game_dates, n_splits=3, val_size=val_size_days, test_size=test_size_days, gap=1)
    print(f"2. Chronological folds: {len(folds)} ({n_unique_dates} real distinct dates, "
          f"val_size={val_size_days}d test_size={test_size_days}d gap=1d)")

    two_head_oof: list[float] = []
    xgb_oof: list[float] = []
    y_true: list[int] = []
    per_fold_report = []

    for fold in folds:
        train_df = features.filter(pl.col("game_date") <= fold.train_end)
        val_df = features.filter(
            (pl.col("game_date") >= fold.val_start) & (pl.col("game_date") <= fold.val_end)
        )
        if train_df.height < 10 or val_df.height < 3:
            continue

        # ── Model family 1: existing two-head architecture (control) ──
        two_head = MLBTwoHeadModel(seed=42)
        two_head.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        fold_two_head_probs = [
            two_head.predict_row(r["event_id"], r).home_win_prob for r in val_df.iter_rows(named=True)
        ]

        # ── Model family 2: XGBoost challenger, direct binary classifier ──
        # Deliberately a different, simpler architecture than the two-head
        # model (flat feature vector -> P(home win) directly, no joint
        # score simulation) -- an independent challenger per CLAUDE.md
        # Part 2 SS1, not a reimplementation of the same structure.
        #
        # Task 9 fix: nested_xgboost_fold() selects hyperparameters and the
        # early-stopping iteration by INNER chronological log loss only,
        # over a bounded declared grid -- the outer validation fold
        # (X_val/y_val_fold) is never passed to any XGBoost fit call.
        # Previously this called XGBoostChallenger.fit(..., eval_set=(X_val,
        # y_val_fold)) directly: the exact rows whose held-out performance
        # was then reported were also the rows early stopping was tuned
        # against -- a real leak CLAUDE.md's stop condition names
        # explicitly.
        X_train = train_df.select(XGB_FEATURES).to_numpy()
        y_train_arr = train_df.select(
            (pl.col("home_score") > pl.col("away_score")).cast(pl.Int8).alias("y")
        ).to_numpy().ravel()
        X_val = val_df.select(XGB_FEATURES).to_numpy()
        y_val_fold = _home_win_labels(val_df)

        nested_result = nested_xgboost_fold(X_train, y_train_arr, X_val, y_val_fold, fold_index=fold.fold_index)
        fold_xgb_probs = nested_result.outer_probs

        two_head_oof.extend(fold_two_head_probs)
        xgb_oof.extend(fold_xgb_probs)
        y_true.extend(y_val_fold)

        per_fold_report.append({
            "fold": fold.fold_index, "train_n": train_df.height, "val_n": val_df.height,
            "two_head": {
                "log_loss": log_loss(y_val_fold, fold_two_head_probs),
                "brier": brier_score(y_val_fold, fold_two_head_probs),
            },
            "xgboost": {
                "log_loss": nested_result.outer_log_loss,
                "brier": nested_result.outer_brier,
                # CLAUDE.md Task 9: persisted for every fold so a real
                # audit can verify the outer score never influenced these.
                "best_params": nested_result.best_params,
                "best_iteration": nested_result.best_iteration,
                "inner_log_loss": nested_result.inner_log_loss,
                "inner_train_n": nested_result.inner_train_n,
                "inner_val_n": nested_result.inner_val_n,
            },
        })
        print(f"  Fold {fold.fold_index}: train={train_df.height} val={val_df.height} "
              f"two_head_ll={per_fold_report[-1]['two_head']['log_loss']:.3f} "
              f"xgb_ll={per_fold_report[-1]['xgboost']['log_loss']:.3f} "
              f"(inner_ll={nested_result.inner_log_loss:.3f}, best_iter={nested_result.best_iteration})")

    if len(y_true) < 10:
        print("Too few real OOF predictions across folds to fit a meaningful ensemble. Stopping honestly.")
        sys.exit(0)

    # ── Real OOF ensemble (logistic stacking on logits) ──
    # Ensemble.fit() sees only out-of-fold predictions, never training
    # predictions -- both model families' OOF probs collected above are
    # genuinely out-of-fold (each fold's val predictions came from a model
    # fit only on that fold's train rows).
    ensemble = Ensemble(method="logistic_stacking")
    ensemble.fit({"two_head": two_head_oof, "xgboost": xgb_oof}, y_true)
    ensemble_probs = [
        ensemble.predict({"two_head": t, "xgboost": x})
        for t, x in zip(two_head_oof, xgb_oof, strict=True)
    ]

    summary = {
        "n_oof": len(y_true),
        "two_head": {"log_loss": log_loss(y_true, two_head_oof), "brier": brier_score(y_true, two_head_oof),
                     "ece": ece(y_true, two_head_oof)},
        "xgboost": {"log_loss": log_loss(y_true, xgb_oof), "brier": brier_score(y_true, xgb_oof),
                    "ece": ece(y_true, xgb_oof)},
        "ensemble": {"log_loss": log_loss(y_true, ensemble_probs), "brier": brier_score(y_true, ensemble_probs),
                     "ece": ece(y_true, ensemble_probs)},
        "ensemble_weights": ensemble.weights,
        "per_fold": per_fold_report,
    }
    print(f"\n3. OOF comparison ({len(y_true)} real out-of-fold predictions across {len(per_fold_report)} folds):")
    for name in ("two_head", "xgboost", "ensemble"):
        m = summary[name]
        print(f"   {name:10s}: log_loss={m['log_loss']:.4f} brier={m['brier']:.4f} ece={m['ece']:.4f}")
    print(f"   ensemble weights: {ensemble.weights}")
    print(
        "\n   Real, disclosed scope: this compares model families on chronological\n"
        "   OOF folds only -- it does NOT touch the already-consumed final test\n"
        "   (outputs/rebuild/test_consumption_registry.json: mlb_moneyline). No\n"
        "   promotion decision is made here; that requires a genuinely new,\n"
        "   never-inspected final test once more real backfill exists."
    )

    out_path = Path("outputs/rebuild/mlb_xgboost_ensemble_oof.json")
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n4. Results saved to {out_path}")


if __name__ == "__main__":
    main()
