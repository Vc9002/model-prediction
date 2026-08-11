"""Train `nfl-elo-trend-lr-rebuild-v1` — rebuild-native NFL model with
calibration-first approach. The incumbent `nfl-elo-trend-lr-v4` has
historically poor calibration (ECE 0.1009), so this rebuild prioritizes
calibration comparison before adding model complexity.

2-feature model: elo_probability + trend_gap (no defensive_trend_gap —
the audit confirmed it's unstable/noisy, and the NFL incumbent's ECE
is the real problem, not a missing third feature).

Pipeline:
1. Load games from NFLNormalizedStore (2021-2025, 1,424 games)
2. Week-bucketed walk-forward Elo/trend construction (rebuild/nfl/elo.py)
3. Chronological 60/20/20 split
4. Logistic regression fit (train only)
5. Calibration cross-fit comparison (validation only)
6. Locked holdout evaluation

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_nfl_rebuild_v1.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression

from model_prediction.rebuild.calibration import (
    Calibrator,
    IdentityCalibrator,
    calibration_intercept_slope,
    cross_fit_calibration_eval,
    fit_calibrator,
)
from model_prediction.rebuild.nfl.elo import (
    DEFAULT_ELO,
    NFL_ELO_CONFIG,
    WalkForwardRow,
    build_dataset,
    rows_to_frame,
)
from model_prediction.rebuild.validation import (
    brier_score,
    date_cluster_split,
    directional_accuracy,
    ece,
    log_loss,
)

SEASONS = [2021, 2022, 2023, 2024, 2025]
FEATURES = ["elo_probability", "trend_gap"]


def _metrics(labels: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    n = int(labels.shape[0])
    preds = (probs > 0.5).astype(int)
    return {
        "n": n,
        "log_loss": float(log_loss(labels, probs)),
        "brier": float(brier_score(labels, probs)),
        "ece": float(ece(labels, probs, n_bins=10)),
        "accuracy": float(directional_accuracy(
            labels.astype(int).tolist(), preds.tolist()
        )),
    }


def _split_by_dates(frame: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Chronological 60/20/20 date-cluster split."""
    dates_list = [str(d) for d in frame["event_start_utc"].to_list()]
    unique_dates = sorted(set(dates_list))
    n_dates = len(unique_dates)
    test_size = max(1, n_dates // 5)
    calib_size = max(1, n_dates // 5)
    train_dates, calib_dates, test_dates = date_cluster_split(
        dates_list, test_size, calib_size
    )
    train_mask = pl.Series(dates_list).is_in(train_dates).to_numpy()
    val_mask = pl.Series(dates_list).is_in(calib_dates).to_numpy()
    holdout_mask = pl.Series(dates_list).is_in(test_dates).to_numpy()
    return train_mask, val_mask, holdout_mask, calib_dates, test_dates


def main() -> None:
    # ── 1. Load data ──
    print("1. Loading NFL games (2021-2025)...")
    result = build_dataset("data/rebuild", seasons=SEASONS,
                           minimum_history_games=50, minimum_team_games=3)
    if not result.rows:
        print("ERROR: no walk-forward rows. Check data backfill.")
        sys.exit(1)

    frame = rows_to_frame(result.rows)
    print(f"   Total games: {result.n_total}")
    print(f"   Walk-forward rows: {frame.height}")
    print(f"   Skipped: {result.skipped_bootstrap} bootstrap, "
          f"{result.skipped_cold_start} cold-start")

    # ── 2. Build feature matrix ──
    print("\n2. Building feature matrix...")
    X = np.column_stack([
        frame["elo_probability"].to_numpy().astype(np.float64),
        frame["trend_gap"].to_numpy().astype(np.float64),
    ])
    y = frame["home_win"].to_numpy().astype(int)

    # ── 3. Chronological split ──
    print("\n3. Chronological 60/20/20 split...")
    train_mask, val_mask, holdout_mask, calib_dates, test_dates = _split_by_dates(frame)
    X_train, y_train = X[train_mask], y[train_mask]
    X_val, y_val = X[val_mask], y[val_mask]
    X_holdout, y_holdout = X[holdout_mask], y[holdout_mask]

    print(f"   Train: {X_train.shape[0]} rows")
    print(f"   Validation: {X_val.shape[0]} rows")
    print(f"   Holdout: {X_holdout.shape[0]} rows")

    # ── 4. Fit logistic regression ──
    print("\n4. Fitting logistic regression (train only)...")
    lr = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
    lr.fit(X_train, y_train)

    train_probs = lr.predict_proba(X_train)[:, 1]
    val_probs = lr.predict_proba(X_val)[:, 1]
    holdout_probs = lr.predict_proba(X_holdout)[:, 1]

    train_metrics = _metrics(y_train, train_probs)
    val_metrics = _metrics(y_val, val_probs)
    raw_holdout_metrics = _metrics(y_holdout, holdout_probs)

    print(f"   Train: LogLoss={train_metrics['log_loss']:.5f} "
          f"Brier={train_metrics['brier']:.5f} ECE={train_metrics['ece']:.5f} "
          f"Acc={train_metrics['accuracy']:.4f}")
    print(f"   Validation: LogLoss={val_metrics['log_loss']:.5f} "
          f"Brier={val_metrics['brier']:.5f} ECE={val_metrics['ece']:.5f} "
          f"Acc={val_metrics['accuracy']:.4f}")

    coefficients = dict(zip(FEATURES, lr.coef_[0].tolist()))
    intercept = float(lr.intercept_[0])
    print(f"   Coefficients: {coefficients}")
    print(f"   Intercept: {intercept:.6f}")

    # ── 5. Calibration comparison (validation only) ──
    print("\n5. Calibration cross-fit comparison (validation fold)...")
    methods = ["identity", "platt", "temperature", "isotonic"]
    cal_results: dict[str, Any] = {}
    for method in methods:
        try:
            r = cross_fit_calibration_eval(
                val_probs.tolist(), y_val.tolist(), method, n_blocks=4
            )
            cal_results[method] = {
                "log_loss": r.log_loss,
                "brier": r.brier,
                "ece": r.ece,
                "calibration_slope": r.calibration_slope,
                "calibration_intercept": r.calibration_intercept,
            }
        except ValueError:
            cal_results[method] = {"error": "single-class labels"}

    # Select best by LogLoss + ECE (LogLoss is primary, ECE is tiebreaker)
    def _score(m: str) -> float:
        cm = cal_results[m]
        return cm.get("log_loss", 99) + cm.get("ece", 99) * 0.5

    winning_method = min(
        [m for m in methods if "error" not in cal_results.get(m, {})],
        key=_score,
        default="identity",
    )
    print(f"   Winning calibration: {winning_method}")

    for method in methods:
        m = cal_results.get(method, {})
        if "error" in m:
            print(f"   {method}: SKIPPED ({m['error']})")
        else:
            marker = " <<<" if method == winning_method else ""
            print(f"   {method}: LogLoss={m['log_loss']:.5f} Brier={m['brier']:.5f} "
                  f"ECE={m['ece']:.5f} slope={m.get('calibration_slope','n/a')}{marker}")

    # Fit winning calibrator
    if winning_method == "identity":
        calibrator: Calibrator = IdentityCalibrator()
    else:
        calibrator = fit_calibrator(winning_method, y_val, val_probs)

    # ── 6. Holdout evaluation ──
    print("\n6. Locked holdout evaluation...")
    holdout_calibrated = np.array([
        calibrator.transform(float(p)) for p in holdout_probs
    ])
    holdout_metrics_cal = _metrics(y_holdout, holdout_calibrated)

    print(f"   Raw: LogLoss={raw_holdout_metrics['log_loss']:.5f} "
          f"Brier={raw_holdout_metrics['brier']:.5f} "
          f"ECE={raw_holdout_metrics['ece']:.5f} "
          f"Acc={raw_holdout_metrics['accuracy']:.4f}")
    print(f"   Calibrated: LogLoss={holdout_metrics_cal['log_loss']:.5f} "
          f"Brier={holdout_metrics_cal['brier']:.5f} "
          f"ECE={holdout_metrics_cal['ece']:.5f} "
          f"Acc={holdout_metrics_cal['accuracy']:.4f}")

    try:
        cal_diag = calibration_intercept_slope(y_holdout, holdout_calibrated)
    except ValueError:
        cal_diag = {"calibration_intercept": None, "calibration_slope": None}

    # ── 7. Persist artifacts ──
    print("\n7. Persisting challenger artifacts...")
    model_artifact_raw: dict[str, Any] = {
        "sport": "nfl",
        "model_version": "nfl-elo-trend-lr-rebuild-v1",
        "method": "logistic_regression",
        "family": "elo_trend_logistic_regression",
        "market_models": {
            "moneyline": {
                "positive_class": "home",
                "feature_names": FEATURES,
                "coefficients": lr.coef_[0].tolist(),
                "intercept": intercept,
                "elo_config": NFL_ELO_CONFIG,
            }
        },
        "qualification": {
            "qualified": False,
            "framework": "locked_complete_date_60_20_20",
            "calibration_method": winning_method,
            "calibration_comparison": cal_results,
            "holdout_metrics_raw": raw_holdout_metrics,
            "holdout_metrics_calibrated": holdout_metrics_cal,
            "holdout_calibration_diagnostics": cal_diag,
        },
        "provenance": {
            "sibling_of_incumbent": "nfl-elo-trend-lr-v4 — same family, never loaded as artifact",
            "data_source": "nflverse schedules via NFLNormalizedStore",
            "seasons": SEASONS,
            "availability_basis": "capture_time_only_mutable_release",
            "note": (
                "Trained on nflverse schedule data (2021-2025, 1,424 games). "
                "2-feature model only (elo_probability + trend_gap) — EPA/CPOE/"
                "pressure/QB state features remain BLOCKED pending calendar-time "
                "daily capture, per the audit's explicit PIT restriction. "
                "Calibration-first approach chosen because the incumbent's ECE is "
                "historically poor (0.1009)."
            ),
            "production_allowed": False,
        },
        "data_summary": {
            "n_total_games": result.n_total,
            "n_walk_forward_rows": len(result.rows),
            "skipped_bootstrap": result.skipped_bootstrap,
            "skipped_cold_start": result.skipped_cold_start,
            "seasons": SEASONS,
        },
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    artifact_hash = hashlib.sha256(
        json.dumps(model_artifact_raw, sort_keys=True, default=str).encode()
    ).hexdigest()
    model_artifact_raw["artifact_hash"] = artifact_hash

    calibrator_params = getattr(calibrator, "parameters", {})
    calibrator_artifact_raw = {
        "model_name": "nfl-elo-trend-lr-rebuild-v1",
        "method": calibrator.method,
        "parameters": calibrator_params,
        "base_model_hash": artifact_hash,
        "provenance": {
            "note": "Fitted on validation split only.",
            "production_allowed": False,
        },
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    calibrator_hash = hashlib.sha256(
        json.dumps(calibrator_artifact_raw, sort_keys=True, default=str).encode()
    ).hexdigest()
    calibrator_artifact_raw["calibrator_hash"] = calibrator_hash

    challenger_dir = Path("config/models/challengers")
    challenger_dir.mkdir(parents=True, exist_ok=True)
    (challenger_dir / "nfl-elo-trend-lr-rebuild-v1.json").write_text(
        json.dumps(model_artifact_raw, indent=2, sort_keys=True, default=str)
    )
    (challenger_dir / "nfl-elo-trend-lr-rebuild-v1-calibrator.json").write_text(
        json.dumps(calibrator_artifact_raw, indent=2, sort_keys=True, default=str)
    )

    results_path = Path("outputs/rebuild/nfl/nfl_rebuild_v1_training_results.json")
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps({
        "train_metrics": train_metrics,
        "validation_metrics": val_metrics,
        "holdout_metrics_raw": raw_holdout_metrics,
        "holdout_metrics_calibrated": holdout_metrics_cal,
        "calibration_comparison": cal_results,
        "winning_calibration_method": winning_method,
        "coefficients": coefficients,
        "intercept": intercept,
        "artifact_hash": artifact_hash,
        "calibrator_hash": calibrator_hash,
        "n_rows_total": frame.height,
    }, indent=2, default=str))

    print(f"   Artifacts saved to config/models/challengers/")
    print(f"   Results saved to {results_path}")
    print("\nDone. NFL Elo+Trend LR rebuild v1 trained.")


if __name__ == "__main__":
    main()
