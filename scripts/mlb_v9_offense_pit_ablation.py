"""MLB v9 Offense PIT Ablation (Standardized on Immutable Parquet Table).

Evaluates projected offense variants against the v9 baseline on the immutable
frozen feature matrix using the standardized StandardScaler + LogisticRegression pipeline.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/mlb_v9_offense_pit_ablation.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import polars as pl
from mlb_evaluator import (
    V9_MANIFEST_PATH,
    V9_PARQUET_PATH,
    _date_cluster_bootstrap_paired,
    _scores,
    predict_model,
    v9_research_fit,
    verify_dataset_contract,
)

BASE_FEATURES = [
    "elo_probability",
    "trend_gap",
    "park_factor",
    "weather_factor",
    "starter_era_gap",
    "bullpen_weakness_gap",
]

OFFENSE_FEATURES = [
    "projected_offense_quality_gap",
    "projected_offense_kbb_gap",
    "projected_offense_power_gap",
]


def main() -> int:
    print("Verifying immutable dataset contract ...")
    manifest, df = verify_dataset_contract(V9_MANIFEST_PATH, V9_PARQUET_PATH)
    print(f"  Dataset SHA256: {manifest['dataset_sha256'][:12]}...")

    df_train = df.filter(pl.col("split") == "train")
    df_holdout = df.filter(pl.col("split") == "research_test")

    print(f"  Train rows: {len(df_train)}, Research Test rows: {len(df_holdout)}")

    # 1. Base Model
    print("\nFitting Base Model (v8 features with standardized v9 pipeline) ...")
    base_model = v9_research_fit(df_train, BASE_FEATURES)
    p_base = predict_model(base_model, df_holdout, BASE_FEATURES)

    # 2. Candidate Model (+ Projected Offense)
    cand_features = BASE_FEATURES + [f for f in OFFENSE_FEATURES if f in df.columns]
    print(f"Fitting Candidate Model (+ {len(cand_features) - len(BASE_FEATURES)} offense features) ...")
    cand_model = v9_research_fit(df_train, cand_features)
    p_cand = predict_model(cand_model, df_holdout, cand_features)

    y_test = df_holdout["home_win"].to_list()
    dates_test = df_holdout["date_et"].to_list()

    base_scores = _scores(p_base, y_test)
    cand_scores = _scores(p_cand, y_test)

    ll_boot = _date_cluster_bootstrap_paired(dates_test, p_base, p_cand, y_test, "log_loss")
    br_boot = _date_cluster_bootstrap_paired(dates_test, p_base, p_cand, y_test, "brier")

    report = {
        "dataset_sha256": manifest["dataset_sha256"],
        "base_scores": base_scores,
        "candidate_scores": cand_scores,
        "delta_log_loss": round(cand_scores["log_loss"] - base_scores["log_loss"], 6),
        "delta_brier": round(cand_scores["brier"] - base_scores["brier"], 6),
        "log_loss_bootstrap": ll_boot,
        "brier_bootstrap": br_boot,
    }

    print("\n" + "=" * 72)
    print("MLB v9 Projected Offense Ablation Results (Immutable Table)")
    print("=" * 72)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
