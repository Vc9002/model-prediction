"""Real feature-group ablation for the MLB rebuild feature set (CLAUDE.md
Part 2 SS11).

Real gap this closes: FeatureAblationRunner (ablation.py) was a complete,
real implementation with zero real callers anywhere in this codebase
(grep-verified) -- and FEATURE_GROUPS_MLB referenced legacy feature names
that don't exist in the real rebuild schema at all, so even a first real
call would have silently produced a vacuous result for every group. Fixed
in the same commit as this script (see ablation.py's own comment).

Uses XGBoostChallenger as the train_fn/predict_fn plugged into
FeatureAblationRunner -- its fit(X, y)/predict(X) signature matches the
ablation harness's generic callable contract directly, and it's already
real, tested, and CLAUDE.md-compliant (see xgboost_stress.py).

Deliberately excludes the already-consumed final test rows from the
ablation input entirely (same test_size slice
train_mlb_rebuild_real_features.py reserves) -- feature-group selection
must not be informed by the held-out set CLAUDE.md's own rule protects
("Do not inspect the final test while selecting: features...").

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_mlb_feature_ablation.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.ablation import FEATURE_GROUPS_MLB, FeatureAblationRunner
from model_prediction.rebuild.horizon_builder import build_mlb_historical_horizon_dataset
from model_prediction.rebuild.mlb_features import dedupe_scoreboard
from model_prediction.rebuild.xgboost_stress import XGBoostChallenger

HORIZON = "late"


def _train_fn(X, y):
    model = XGBoostChallenger(seed=42)
    model.fit(X, y)
    return model


def _predict_fn(model, X):
    return model.predict(X).tolist()


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
    # train_mlb_rebuild_real_features.py, train_mlb_xgboost_ensemble.py, and
    # mlb_shadow_pipeline.py's walk-forward retraining.
    dataset = build_mlb_historical_horizon_dataset("data/rebuild", start_date, end_date, HORIZON)
    features = dataset.features.sort("event_start_utc") if dataset.features.height else dataset.features
    starters_known = dataset.starters_known_games
    print(
        f"1. Feature rows: {dataset.matched_games} matched ({starters_known} with a point-in-time-valid "
        f"probable starter for both teams at horizon={HORIZON}, {dataset.matched_games - starters_known} "
        f"flagged starters_known=0); dataset_hash={dataset.dataset_hash[:12]}"
    )

    if features.height < 30:
        print("Not enough matched games to run a meaningful ablation (need >=30). Stopping honestly.")
        sys.exit(0)

    n = features.height
    # Same reserved final-test slice train_mlb_rebuild_real_features.py
    # protects -- excluded here entirely, not just unused, so feature
    # selection genuinely cannot be informed by it.
    test_size = max(10, n // 6)
    ablation_input = features[: n - test_size].with_columns(
        (pl.col("home_score") > pl.col("away_score")).cast(pl.Int8).alias("home_win")
    )
    print(f"2. Ablation input: {ablation_input.height} games (reserved final-test {test_size} rows excluded)")

    runner = FeatureAblationRunner(FEATURE_GROUPS_MLB)
    isolated = runner.run_isolated(
        ablation_input,
        target_col="home_win",
        train_fn=_train_fn,
        predict_fn=_predict_fn,
        date_col="event_start_utc",
    )
    print("\n3. Isolated ablation (remove one group at a time):")
    for r in isolated:
        print(
            f"   {r.group:20s} delta_log_loss={r.delta_log_loss:+.4f} "
            f"delta_brier={r.delta_brier:+.4f} coverage={r.coverage_impact:.2f} verdict={r.verdict}"
        )

    cumulative = runner.run_cumulative(
        ablation_input,
        target_col="home_win",
        train_fn=_train_fn,
        predict_fn=_predict_fn,
        date_col="event_start_utc",
    )
    print("\n4. Cumulative ablation (add groups in order of isolated importance):")
    for r in cumulative:
        print(f"   {r.group:30s} log_loss={r.ablated_log_loss:.4f}")

    report = runner.report()
    report["n_games"] = ablation_input.height
    report["final_test_excluded"] = test_size
    out_path = Path("outputs/rebuild/mlb_feature_ablation.json")
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n5. Report saved to {out_path}")
    print(
        "\n   Real, disclosed scope: this ablation input is small "
        f"(n={ablation_input.height}) -- per-group deltas are directional\n"
        "   signal, not a statistically robust ranking. More real backfill\n"
        "   is the fix, not further feature engineering."
    )


if __name__ == "__main__":
    main()
