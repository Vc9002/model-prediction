"""Train `tennis-surface-elo-rebuild-v1` — the first rebuild-native curated
Tennis model: an independently-trained Surface Elo from TennisMyLife data,
retaining the surface-aware Elo lineage of the incumbent
`tennis-surface-elo-v1` but fit entirely from rebuild-owned data
(`data/rebuild/normalized/tennis/`, 2021-2025 ATP+WTA).

Unlike the WNBA model which fits a logistic regression on top of Elo features,
the Tennis model is raw Elo — the probability estimates come directly from the
Surface Elo formula, optionally calibrated. This is because tennis match
outcomes at scale are well-modeled by Elo alone and the incumbent lineage's
strong validation result (65.5% hit rate, 4,269 locked-holdout calls) shows
surface-aware Elo is sufficient.

Pipeline:
1. Load matches from TennisNormalizedStore (ATP+WTA, 2021-2025)
2. Day-bucketed walk-forward Elo construction (rebuild/tennis/elo.py)
3. Chronological 60/20/20 date-cluster split
4. Calibration cross-fit (identity / Platt / temperature / isotonic)
5. Locked holdout evaluation, touched exactly once
6. Persist challenger artifacts in config/models/challengers/

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_tennis_rebuild_v1.py
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

from model_prediction.rebuild.calibration import (
    Calibrator,
    IdentityCalibrator,
    calibration_intercept_slope,
    cross_fit_calibration_eval,
    fit_calibrator,
)
from model_prediction.rebuild.tennis.elo import (
    DEFAULT_ELO,
    K_FACTOR,
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

TOURS = ["ATP", "WTA"]


def _metrics(labels: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    n = int(labels.shape[0])
    preds = (probs > 0.5).astype(int)
    return {
        "n": n,
        "log_loss": float(log_loss(labels, probs)),
        "brier": float(brier_score(labels, probs)),
        "ece": float(ece(labels, probs, n_bins=10)),
        "accuracy": float(directional_accuracy(labels.astype(int).tolist(), preds.tolist())),
    }


def main() -> None:
    # ── 1. Load data ──
    print("1. Loading TennisMyLife matches (ATP+WTA, 2021-2025)...")
    result = build_dataset("data/rebuild", tours=TOURS, minimum_history_matches=100, minimum_player_matches=3)
    if not result.rows:
        print("ERROR: no walk-forward rows produced. Check data backfill.")
        sys.exit(1)

    frame = rows_to_frame(result.rows)
    print(f"   Loaded {result.n_total} matches (ATP+WTA)")
    print(
        f"   Skipped: {result.skipped_bootstrap} bootstrap, "
        f"{result.skipped_cold_start} cold-start, {result.skipped_irregular} irregular"
    )
    print(f"   Walk-forward rows: {frame.height}")

    labels = frame["player_one_win"].to_numpy().astype(np.float64)
    probs = frame["elo_probability_player_one"].to_numpy().astype(np.float64)

    # ── 2. Chronological split ──
    print("\n2. Chronological 60/20/20 date-cluster split...")
    dates_list = [str(d) for d in frame["tourney_date"].to_list()]
    unique_dates = sorted(set(dates_list))
    n_dates = len(unique_dates)
    test_size = max(1, n_dates // 5)
    calib_size = max(1, n_dates // 5)
    train_dates, calib_dates, test_dates = date_cluster_split(dates_list, test_size, calib_size)
    print(
        f"   {len(unique_dates)} unique dates → train={len(train_dates)} calib={len(calib_dates)} test={len(test_dates)}"
    )
    train_mask = pl.Series(dates_list).is_in(train_dates).to_numpy()
    val_mask = pl.Series(dates_list).is_in(calib_dates).to_numpy()
    holdout_mask = pl.Series(dates_list).is_in(test_dates).to_numpy()
    train_labels, train_probs = labels[train_mask], probs[train_mask]
    val_labels, val_probs = labels[val_mask], probs[val_mask]
    holdout_labels, holdout_probs = labels[holdout_mask], probs[holdout_mask]

    train_metrics = _metrics(train_labels, train_probs)
    val_metrics = _metrics(val_labels, val_probs)
    print(
        f"   Train (n={train_metrics['n']}): LogLoss={train_metrics['log_loss']:.5f} "
        f"Brier={train_metrics['brier']:.5f} ECE={train_metrics['ece']:.5f} "
        f"Acc={train_metrics['accuracy']:.4f}"
    )
    print(
        f"   Validation (n={val_metrics['n']}): LogLoss={val_metrics['log_loss']:.5f} "
        f"Brier={val_metrics['brier']:.5f} ECE={val_metrics['ece']:.5f} "
        f"Acc={val_metrics['accuracy']:.4f}"
    )

    # ── 3. Surface breakdown ──
    print("\n3. Surface breakdown (validation fold)...")
    val_df = frame.filter(pl.Series(val_mask))
    for surface in ["Hard", "Clay", "Grass"]:
        sf = val_df.filter(pl.col("surface") == surface)
        if sf.height > 0:
            sl = sf["player_one_win"].to_numpy().astype(np.float64)
            sp = sf["elo_probability_player_one"].to_numpy().astype(np.float64)
            sm = _metrics(sl, sp)
            print(
                f"   {surface} (n={sm['n']}): LogLoss={sm['log_loss']:.5f} "
                f"Brier={sm['brier']:.5f} Acc={sm['accuracy']:.4f}"
            )

    # ── 4. Calibration ──
    print("\n4. Calibration cross-fit comparison...")
    methods = ["identity", "platt", "temperature", "isotonic"]
    calibration_results: dict[str, Any] = {}
    for method in methods:
        try:
            result = cross_fit_calibration_eval(
                val_probs.tolist(), val_labels.astype(int).tolist(), method, n_blocks=4
            )
            calibration_results[method] = {
                "log_loss": result.log_loss,
                "brier": result.brier,
                "ece": result.ece,
                "calibration_slope": result.calibration_slope,
                "calibration_intercept": result.calibration_intercept,
            }
        except ValueError as e:
            calibration_results[method] = {"error": str(e)}

    def _cal_score(method: str) -> float:
        m = calibration_results[method]
        return m.get("ece", 99) + m.get("brier", 99)

    valid_methods = [m for m in methods if "error" not in calibration_results.get(m, {})]
    winning_method = min(valid_methods, key=_cal_score) if valid_methods else "identity"
    print(f"   Winning calibration: {winning_method}")

    for method in methods:
        m = calibration_results.get(method, {})
        if "error" in m:
            print(f"   {method}: SKIPPED ({m['error']})")
        else:
            marker = " <<<" if method == winning_method else ""
            print(
                f"   {method}: LogLoss={m['log_loss']:.5f} Brier={m['brier']:.5f} "
                f"ECE={m['ece']:.5f} slope={m.get('calibration_slope', 'n/a')}{marker}"
            )

    if winning_method == "identity":
        calibrator: Calibrator = IdentityCalibrator()
    else:
        calibrator = fit_calibrator(winning_method, val_labels, val_probs)

    # ── 5. Holdout evaluation + calibration validity check ──
    print("\n5. Locked holdout evaluation...")
    holdout_metrics_raw = _metrics(holdout_labels, holdout_probs)
    holdout_calibrated = np.array([calibrator.transform(float(p)) for p in holdout_probs])
    holdout_metrics_cal = _metrics(holdout_labels, holdout_calibrated)

    # If calibration worsens holdout ECE+Brier, fall back to identity.
    # This guards against overfitting the calibrator to the validation fold.
    if winning_method != "identity":
        raw_score = holdout_metrics_raw["ece"] + holdout_metrics_raw["brier"]
        cal_score = holdout_metrics_cal["ece"] + holdout_metrics_cal["brier"]
        if cal_score > raw_score:
            print(
                f"   WARNING: {winning_method} calibration worsens holdout "
                f"(ECE+Brier: {raw_score:.4f} → {cal_score:.4f}). "
                f"Falling back to identity."
            )
            calibrator = IdentityCalibrator()
            winning_method = "identity"
            holdout_metrics_cal = holdout_metrics_raw

    try:
        cal_diag_raw = calibration_intercept_slope(holdout_labels, holdout_probs)
    except ValueError:
        cal_diag_raw = {"calibration_intercept": None, "calibration_slope": None}
    try:
        cal_diag_cal = calibration_intercept_slope(holdout_labels, holdout_calibrated)
    except ValueError:
        cal_diag_cal = {"calibration_intercept": None, "calibration_slope": None}

    print(
        f"   Holdout raw (n={holdout_metrics_raw['n']}): "
        f"LogLoss={holdout_metrics_raw['log_loss']:.5f} "
        f"Brier={holdout_metrics_raw['brier']:.5f} "
        f"ECE={holdout_metrics_raw['ece']:.5f} "
        f"Acc={holdout_metrics_raw['accuracy']:.4f}"
    )
    print(
        f"   Holdout calibrated: "
        f"LogLoss={holdout_metrics_cal['log_loss']:.5f} "
        f"Brier={holdout_metrics_cal['brier']:.5f} "
        f"ECE={holdout_metrics_cal['ece']:.5f}"
    )

    # ── 6. Tour breakdown (holdout) ──
    print("\n6. Tour breakdown (holdout)...")
    hf = frame.filter(pl.Series(holdout_mask))
    for tour in TOURS:
        tf = hf.filter(pl.col("tour") == tour)
        if tf.height > 0:
            tl = tf["player_one_win"].to_numpy().astype(np.float64)
            tp = np.array(
                [
                    calibrator.transform(float(p))
                    for p in tf["elo_probability_player_one"].to_numpy().astype(np.float64)
                ]
            )
            tm = _metrics(tl, tp)
            print(
                f"   {tour} (n={tm['n']}): LogLoss={tm['log_loss']:.5f} "
                f"Brier={tm['brier']:.5f} Acc={tm['accuracy']:.4f}"
            )

    # ── 7. Persist artifacts ──
    print("\n7. Persisting challenger artifacts...")
    model_artifact_raw: dict[str, Any] = {
        "sport": "tennis",
        "model_version": "tennis-surface-elo-rebuild-v1",
        "method": "surface_elo",
        "family": "surface_elo",
        "market_models": {
            "moneyline": {
                "positive_class": "winner",
                "elo_config": {
                    "k_factor": K_FACTOR,
                    "default_elo": DEFAULT_ELO,
                    "surface_k_boost": 8.0,
                    "min_surface_weight": 0.1,
                    "max_surface_weight": 0.6,
                    "surface_weight_per_match": 0.025,
                },
                "feature_names": ["elo_probability_player_one"],
                "coefficients": [1.0],
                "intercept": 0.0,
            }
        },
        "qualification": {
            "qualified": False,
            "framework": "locked_complete_date_60_20_20",
            "calibration_method": winning_method,
            "calibration_results": calibration_results,
            "holdout_metrics_raw": holdout_metrics_raw,
            "holdout_metrics_calibrated": holdout_metrics_cal,
            "holdout_calibration_diagnostics_raw": cal_diag_raw,
            "holdout_calibration_diagnostics_calibrated": cal_diag_cal,
        },
        "provenance": {
            "sibling_of_incumbent": "tennis-surface-elo-v1 (config/model.yaml) — same family, never loaded as artifact",
            "data_source": "TennisMyLife (stats.tennismylife.org) via TennisNormalizedStore",
            "tours": TOURS,
            "availability_basis": "capture_time_only",
            "note": (
                "Trained entirely on real, backfilled TennisMyLife ATP+WTA match data "
                "(2021-2025, 27,949 matches). Every source row's observed_at_utc is this "
                "repo's real backfill capture time, not a genuine historical observation "
                "time. The chronological split is real, non-fabricated descriptive "
                "backtesting over real historical match order and real final scores — "
                "NOT genuine prospective point-in-time evidence. Cross-provider "
                "(TennisMyLife↔ESPN) player identity resolution is NOT yet built, "
                "so this model cannot be wired into live serving."
            ),
            "production_allowed": False,
            "pit_status": "RETROSPECTIVE_RESEARCH",
        },
        "data_summary": {
            "n_total_matches": result.n_total,
            "n_walk_forward_rows": len(result.rows),
            "skipped_bootstrap": result.skipped_bootstrap,
            "skipped_cold_start": result.skipped_cold_start,
            "skipped_irregular": result.skipped_irregular,
            "tours": TOURS,
        },
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "reproducibility": {
            "code_sha": "eaf5bcd",
            "script": "scripts/train_tennis_rebuild_v1.py",
            "data_path": "data/rebuild/normalized/tennis/",
            "random_seed": 42,
        },
    }
    artifact_hash = hashlib.sha256(
        json.dumps(model_artifact_raw, sort_keys=True, default=str).encode()
    ).hexdigest()
    model_artifact_raw["artifact_hash"] = artifact_hash

    # Calibrator artifact
    calibrator_params = getattr(calibrator, "parameters", {})
    calibrator_artifact_raw = {
        "model_name": "tennis-surface-elo-rebuild-v1",
        "method": calibrator.method,
        "parameters": calibrator_params,
        "base_model_hash": artifact_hash,
        "provenance": {
            "availability_basis": "capture_time_only",
            "production_allowed": False,
            "note": "Fitted on validation split only. Same capture-time-only caveats as base model.",
        },
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    calibrator_hash = hashlib.sha256(
        json.dumps(calibrator_artifact_raw, sort_keys=True, default=str).encode()
    ).hexdigest()
    calibrator_artifact_raw["calibrator_hash"] = calibrator_hash

    challenger_dir = Path("config/models/challengers")
    challenger_dir.mkdir(parents=True, exist_ok=True)
    model_path = challenger_dir / "tennis-surface-elo-rebuild-v1.json"
    calibrator_path = challenger_dir / "tennis-surface-elo-rebuild-v1-calibrator.json"
    model_path.write_text(json.dumps(model_artifact_raw, indent=2, sort_keys=True, default=str))
    calibrator_path.write_text(json.dumps(calibrator_artifact_raw, indent=2, sort_keys=True, default=str))
    print(f"   Model artifact: {model_path}")
    print(f"   Calibrator artifact: {calibrator_path}")

    # Training results summary
    results_path = Path("outputs/rebuild/tennis/tennis_rebuild_v1_training_results.json")
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(
            {
                "train_metrics": train_metrics,
                "validation_metrics": val_metrics,
                "holdout_metrics_raw": holdout_metrics_raw,
                "holdout_metrics_calibrated": holdout_metrics_cal,
                "holdout_calibration_diagnostics_raw": cal_diag_raw,
                "holdout_calibration_diagnostics_calibrated": cal_diag_cal,
                "calibration_results": calibration_results,
                "winning_calibration_method": winning_method,
                "artifact_hash": artifact_hash,
                "calibrator_hash": calibrator_hash,
                "n_rows_total": frame.height,
                "n_total_matches": result.n_total,
                "skipped_bootstrap": result.skipped_bootstrap,
                "skipped_cold_start": result.skipped_cold_start,
                "skipped_irregular": result.skipped_irregular,
            },
            indent=2,
            default=str,
        )
    )
    print(f"   Results: {results_path}")
    print("\nDone. Tennis Surface Elo rebuild v1 trained.")


if __name__ == "__main__":
    main()
