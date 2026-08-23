"""MLB v9 Model Family Benchmark (Roadmap Steps 18-20).

Compares model architectures on the immutable MLB v9 Parquet dataset:
  1. Baseline Constant Home Win Rate
  2. Standardized Logistic Regression (v8 Incumbent Features)
  3. Standardized Logistic Regression (v9 Full Features: FIP + K-BB + Bullpen Fatigue)
  4. Unconstrained XGBoost Classifier
  5. Monotonic XGBoost Classifier (Domain Constraints)
  6. Beta / Platt Calibrated Monotonic XGBoost

Evaluates strictly on chronological splits:
  - Train: 3,814 games (2021-2023)
  - Validation: 1,082 games (2024, hyperparameter tuning & early stopping)
  - Research Test: 1,742 games (2025-2026, prospective holdout)

Inference uses date-clustered paired bootstrap (2,000 resamples) for Log Loss & Brier Score.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import numpy as np
import polars as pl
import xgboost as xgb
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from model_prediction.calibration import calibration_metrics
from model_prediction.config import PROJECT_ROOT

PARQUET_PATH = PROJECT_ROOT / "outputs/research/mlb_v9/tables/mlb_v9_feature_table_v1.parquet"
OUTPUT_PATH = PROJECT_ROOT / "outputs/research/mlb_v9/model_family_benchmark_results.json"

BOOTSTRAP_SEED = 20260823
N_BOOTSTRAP = 2000

V8_FEATURES = [
    "elo_probability",
    "trend_gap",
    "park_factor",
    "weather_factor",
    "starter_era_gap",
    "bullpen_weakness_gap",
]

V9_FEATURES = [
    "elo_probability",
    "trend_gap",
    "defensive_trend_gap",
    "park_factor",
    "weather_factor",
    "starter_fip_gap",
    "starter_kbb_gap",
    "bullpen_weakness_gap",
    "bullpen_fatigue_gap",
    "rest_disparity",
    "games_last_7_gap",
]

MONOTONIC_CONSTRAINTS_V9 = {
    "elo_probability": 1,
    "trend_gap": 1,
    "defensive_trend_gap": 1,
    "park_factor": 1,
    "weather_factor": 0,
    "starter_fip_gap": -1,
    "starter_kbb_gap": 1,
    "bullpen_weakness_gap": -1,
    "bullpen_fatigue_gap": -1,
    "rest_disparity": 1,
    "games_last_7_gap": -1,
}


def _calc_scores(probabilities: list[float] | np.ndarray, outcomes: list[int] | np.ndarray) -> dict[str, Any]:
    probs = np.clip(np.asarray(probabilities, dtype=float), 1e-12, 1 - 1e-12)
    y = np.asarray(outcomes, dtype=int)
    log_loss = float(-np.mean(y * np.log(probs) + (1 - y) * np.log(1 - probs)))
    brier = float(np.mean((probs - y) ** 2))
    accuracy = float(np.mean((probs >= 0.5) == y))
    auc = float(roc_auc_score(y, probs)) if len(set(y)) > 1 else 0.5
    calib = calibration_metrics(probs.tolist(), y.tolist())
    return {
        "log_loss": round(log_loss, 6),
        "brier": round(brier, 6),
        "accuracy": round(accuracy, 4),
        "auc": round(auc, 4),
        "ece": round(calib.get("ece", 0.0), 6),
        "slope": round(calib.get("slope", 1.0), 4),
        "intercept": round(calib.get("intercept", 0.0), 4),
    }


def _date_cluster_bootstrap(
    dates: list[str],
    p_base: list[float] | np.ndarray,
    p_cand: list[float] | np.ndarray,
    y: list[int] | np.ndarray,
    seed: int = BOOTSTRAP_SEED,
    n_bootstrap: int = N_BOOTSTRAP,
) -> dict[str, Any]:
    by_day: dict[str, list[int]] = defaultdict(list)
    for i, day in enumerate(dates):
        by_day[day].append(i)
    clusters = list(by_day.values())
    n_clusters = len(clusters)
    rng = np.random.default_rng(seed)

    base_arr = np.clip(np.asarray(p_base, dtype=float), 1e-12, 1 - 1e-12)
    cand_arr = np.clip(np.asarray(p_cand, dtype=float), 1e-12, 1 - 1e-12)
    y_arr = np.asarray(y, dtype=int)

    obs_ll_delta = float(
        -np.mean(y_arr * np.log(cand_arr) + (1 - y_arr) * np.log(1 - cand_arr))
        - (-np.mean(y_arr * np.log(base_arr) + (1 - y_arr) * np.log(1 - base_arr)))
    )
    obs_br_delta = float(np.mean((cand_arr - y_arr) ** 2) - np.mean((base_arr - y_arr) ** 2))

    ll_better_count = 0
    br_better_count = 0
    boot_ll_deltas = []
    boot_br_deltas = []

    for _ in range(n_bootstrap):
        sampled_c_idx = rng.choice(n_clusters, size=n_clusters, replace=True)
        idx = [i for c in sampled_c_idx for i in clusters[c]]
        if not idx:
            continue
        y_b = y_arr[idx]
        p_base_b = base_arr[idx]
        p_cand_b = cand_arr[idx]

        ll_cand = -np.mean(y_b * np.log(p_cand_b) + (1 - y_b) * np.log(1 - p_cand_b))
        ll_base = -np.mean(y_b * np.log(p_base_b) + (1 - y_b) * np.log(1 - p_base_b))
        dll = float(ll_cand - ll_base)

        br_cand = np.mean((p_cand_b - y_b) ** 2)
        br_base = np.mean((p_base_b - y_b) ** 2)
        dbr = float(br_cand - br_base)

        boot_ll_deltas.append(dll)
        boot_br_deltas.append(dbr)

        if dll < 0:
            ll_better_count += 1
        if dbr < 0:
            br_better_count += 1

    return {
        "delta_log_loss": round(obs_ll_delta, 6),
        "P_log_loss_better": round(ll_better_count / n_bootstrap, 4),
        "delta_brier": round(obs_br_delta, 6),
        "P_brier_better": round(br_better_count / n_bootstrap, 4),
        "ci_95_log_loss": [
            round(float(np.percentile(boot_ll_deltas, 2.5)), 6),
            round(float(np.percentile(boot_ll_deltas, 97.5)), 6),
        ],
        "ci_95_brier": [
            round(float(np.percentile(boot_br_deltas, 2.5)), 6),
            round(float(np.percentile(boot_br_deltas, 97.5)), 6),
        ],
    }


def run_benchmark() -> dict[str, Any]:
    print(f"Loading MLB v9 feature dataset from {PARQUET_PATH}...")
    df = pl.read_parquet(PARQUET_PATH)

    df_train = df.filter(pl.col("split") == "train")
    df_val = df.filter(pl.col("split") == "validation")
    df_test = df.filter(pl.col("split") == "research_test")

    y_train = df_train["home_win"].to_numpy().astype(int)
    y_val = df_val["home_win"].to_numpy().astype(int)
    y_test = df_test["home_win"].to_numpy().astype(int)
    test_dates = (
        df_test["date_et"].to_list()
        if "date_et" in df_test.columns
        else [str(i) for i in range(len(df_test))]
    )

    print(f"Dataset split rows: train={len(df_train)}, validation={len(df_val)}, test={len(df_test)}")

    results: dict[str, Any] = {}

    # 1. Baseline: Constant Home Rate
    train_home_rate = float(np.mean(y_train))
    p_const = np.full(len(y_test), train_home_rate)
    scores_const = _calc_scores(p_const, y_test)
    results["Constant Home Baseline"] = {
        "metrics": scores_const,
        "train_home_rate": round(train_home_rate, 4),
    }

    # 2. Standardized Logistic Regression (v8 Features)
    imputer_v8 = SimpleImputer(strategy="median")
    scaler_v8 = StandardScaler()
    X_train_v8 = scaler_v8.fit_transform(imputer_v8.fit_transform(df_train.select(V8_FEATURES).to_numpy()))
    X_test_v8 = scaler_v8.transform(imputer_v8.transform(df_test.select(V8_FEATURES).to_numpy()))

    lr_v8 = LogisticRegression(max_iter=5000, solver="lbfgs", random_state=42)
    lr_v8.fit(X_train_v8, y_train)
    p_lr_v8 = lr_v8.predict_proba(X_test_v8)[:, 1]
    scores_lr_v8 = _calc_scores(p_lr_v8, y_test)
    boot_lr_v8 = _date_cluster_bootstrap(test_dates, p_const, p_lr_v8, y_test)
    results["Logistic Regression (v8 Features)"] = {
        "features": V8_FEATURES,
        "metrics": scores_lr_v8,
        "bootstrap_vs_constant": boot_lr_v8,
    }

    # 3. Standardized Logistic Regression (v9 Features: FIP + K-BB + Bullpen Fatigue)
    imputer_v9 = SimpleImputer(strategy="median")
    scaler_v9 = StandardScaler()
    X_train_v9 = scaler_v9.fit_transform(imputer_v9.fit_transform(df_train.select(V9_FEATURES).to_numpy()))
    X_val_v9 = scaler_v9.transform(imputer_v9.transform(df_val.select(V9_FEATURES).to_numpy()))
    X_test_v9 = scaler_v9.transform(imputer_v9.transform(df_test.select(V9_FEATURES).to_numpy()))

    lr_v9 = LogisticRegression(max_iter=5000, solver="lbfgs", random_state=42)
    lr_v9.fit(X_train_v9, y_train)
    p_lr_v9 = lr_v9.predict_proba(X_test_v9)[:, 1]
    scores_lr_v9 = _calc_scores(p_lr_v9, y_test)
    boot_lr_v9 = _date_cluster_bootstrap(test_dates, p_lr_v8, p_lr_v9, y_test)
    results["Logistic Regression (v9 Full Features)"] = {
        "features": V9_FEATURES,
        "metrics": scores_lr_v9,
        "bootstrap_vs_lr_v8": boot_lr_v9,
    }

    # 4. Unconstrained XGBoost Classifier
    xgb_unconstrained = xgb.XGBClassifier(
        n_estimators=150,
        max_depth=3,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.5,
        reg_lambda=1.0,
        random_state=42,
        eval_metric="logloss",
        early_stopping_rounds=20,
    )
    xgb_unconstrained.fit(
        X_train_v9,
        y_train,
        eval_set=[(X_val_v9, y_val)],
        verbose=False,
    )
    p_xgb_unc = xgb_unconstrained.predict_proba(X_test_v9)[:, 1]
    scores_xgb_unc = _calc_scores(p_xgb_unc, y_test)
    boot_xgb_unc = _date_cluster_bootstrap(test_dates, p_lr_v8, p_xgb_unc, y_test)
    results["XGBoost (Unconstrained)"] = {
        "best_iteration": int(xgb_unconstrained.best_iteration),
        "metrics": scores_xgb_unc,
        "bootstrap_vs_lr_v8": boot_xgb_unc,
    }

    # 5. Monotonic XGBoost Classifier
    constraints_tuple = tuple(MONOTONIC_CONSTRAINTS_V9[f] for f in V9_FEATURES)
    xgb_monotonic = xgb.XGBClassifier(
        n_estimators=150,
        max_depth=3,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.5,
        reg_lambda=1.0,
        monotone_constraints=constraints_tuple,
        random_state=42,
        eval_metric="logloss",
        early_stopping_rounds=20,
    )
    xgb_monotonic.fit(
        X_train_v9,
        y_train,
        eval_set=[(X_val_v9, y_val)],
        verbose=False,
    )
    p_xgb_mono = xgb_monotonic.predict_proba(X_test_v9)[:, 1]
    scores_xgb_mono = _calc_scores(p_xgb_mono, y_test)
    boot_xgb_mono = _date_cluster_bootstrap(test_dates, p_lr_v8, p_xgb_mono, y_test)
    results["XGBoost (Monotonic Constraints)"] = {
        "best_iteration": int(xgb_monotonic.best_iteration),
        "monotonic_constraints": MONOTONIC_CONSTRAINTS_V9,
        "metrics": scores_xgb_mono,
        "bootstrap_vs_lr_v8": boot_xgb_mono,
    }

    # 6. Calibrated Monotonic XGBoost (Platt Sigmoid on validation logits)
    p_val_mono = np.clip(xgb_monotonic.predict_proba(X_val_v9)[:, 1], 1e-6, 1 - 1e-6)
    logit_val_mono = np.log(p_val_mono / (1.0 - p_val_mono)).reshape(-1, 1)

    calibrator = LogisticRegression(max_iter=1000, solver="lbfgs")
    calibrator.fit(logit_val_mono, y_val)

    p_test_mono = np.clip(xgb_monotonic.predict_proba(X_test_v9)[:, 1], 1e-6, 1 - 1e-6)
    logit_test_mono = np.log(p_test_mono / (1.0 - p_test_mono)).reshape(-1, 1)
    p_cal_xgb_mono = calibrator.predict_proba(logit_test_mono)[:, 1]

    scores_cal_xgb_mono = _calc_scores(p_cal_xgb_mono, y_test)
    boot_cal_xgb_mono = _date_cluster_bootstrap(test_dates, p_lr_v8, p_cal_xgb_mono, y_test)
    results["Calibrated Monotonic XGBoost (Platt)"] = {
        "metrics": scores_cal_xgb_mono,
        "bootstrap_vs_lr_v8": boot_cal_xgb_mono,
    }

    # Save output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nBenchmark results saved to {OUTPUT_PATH}")

    # Print summary table
    print("\n" + "=" * 115)
    header = f"{'MODEL ARCHITECTURE':<38} | {'LOG LOSS':<8} | {'BRIER':<8} | {'ACC':<6} | {'AUC':<6} | {'ECE':<8} | {'SLOPE':<6} | {'P(BETTER)'}"
    print(header)
    print("-" * 115)
    for name, res in results.items():
        m = res["metrics"]
        p_better = res.get("bootstrap_vs_lr_v8", {}).get("P_log_loss_better", "N/A")
        p_better_str = f"{p_better * 100:.1f}%" if isinstance(p_better, float) else str(p_better)
        row = f"{name:<38} | {m['log_loss']:<8.4f} | {m['brier']:<8.4f} | {m['accuracy']:<6.3f} | {m['auc']:<6.3f} | {m['ece']:<8.4f} | {m['slope']:<6.3f} | {p_better_str}"
        print(row)
    print("=" * 115 + "\n")

    return results


if __name__ == "__main__":
    run_benchmark()
