"""Train `wnba-elo-trend-lr-rebuild-v1` -- the first real rebuild-native
curated WNBA model: an independently-trained sibling of the incumbent
`wnba-elo-trend-lr-v4` (same family, Elo + trend, logistic regression),
fit entirely from rebuild-owned data
(`data/rebuild/normalized/wnba/{games,team_box}`, 2022-2025), never loading
the incumbent artifact or its rating state
(`docs/model_audit/ARCHITECTURE_CORRECTION.md`).

Pipeline: real backfilled games -> day-bucketed walk-forward Elo/trend
(`rebuild/wnba/elo_trend.py`, PIT-proven in
`tests/rebuild/test_wnba_elo_trend.py`) -> chronological 60/20/20 date-
cluster split (`rebuild/validation.py::date_cluster_split`) -> logistic
regression fit on train only, feature-set (with/without
`defensive_trend_gap`) decided on validation only, calibration method
decided on validation only via `rebuild/calibration.py`'s chronological
cross-fit evaluator -> locked holdout touched exactly once, at the very
end.

Two caveats stated here AND in every downstream artifact/doc (never
silently dropped, per
`docs/model_audit/models/WNBA_REBUILD_DATA_FOUNDATION.md`):

1. This is single-vintage, capture-time-only real historical data (every
   row's `observed_at_utc` is this repo's 2026-08 backfill time, not a real
   historical observation time). The "chronological split" below is
   real, non-fabricated descriptive backtesting over real historical game
   order and real final scores -- it is NOT genuine prospective
   point-in-time evidence, and must never be described as such.
2. The underlying SportsDataverse data has an unresolved commercial-use-
   rights status. This model is research/shadow-only regardless of
   statistical performance.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/train_wnba_rebuild_v1.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression

from model_prediction.rebuild.calibration import (
    calibration_intercept_slope,
    cross_fit_calibration_eval,
    fit_calibrator,
)
from model_prediction.rebuild.validation import (
    brier_score,
    calibration_curve,
    date_cluster_split,
    directional_accuracy,
    ece,
    log_loss,
)
from model_prediction.rebuild.wnba.elo_trend import DEFAULT_ELO, WNBA_ELO_CONFIG, build_dataset, rows_to_frame

SEASONS = [2022, 2023, 2024, 2025]
FULL_FEATURES = ["elo_probability", "trend_gap", "defensive_trend_gap"]
REDUCED_FEATURES = ["elo_probability", "trend_gap"]
MINIMUM_HISTORY_GAMES = 30
MINIMUM_TEAM_GAMES = 3


def _fit_logreg(train: pl.DataFrame, features: list[str]) -> LogisticRegression:
    X = train.select(features).to_numpy()
    y = train["home_win"].to_numpy()
    model = LogisticRegression(penalty=None, solver="lbfgs", max_iter=2000)
    model.fit(X, y)
    return model


def _predict(model: LogisticRegression, frame: pl.DataFrame, features: list[str]) -> list[float]:
    X = frame.select(features).to_numpy()
    return [float(p) for p in model.predict_proba(X)[:, 1]]


def _metrics(labels: list[int], probs: list[float]) -> dict[str, float]:
    preds = [1 if p >= 0.5 else 0 for p in probs]
    return {
        "n": len(labels),
        "log_loss": log_loss(labels, probs),
        "brier": brier_score(labels, probs),
        "ece": ece(labels, probs),
        "accuracy": directional_accuracy(labels, preds),
    }


def _calibration_diagnostics(labels: list[int], probs: list[float], n_bins: int = 5) -> dict[str, Any]:
    intercept, slope = calibration_intercept_slope(probs, labels)
    curve = calibration_curve(labels, probs, n_bins=n_bins)
    buckets = [
        {
            "lower": round(i / n_bins, 3),
            "upper": round((i + 1) / n_bins, 3),
            "mean_p": round(center, 4),
            "hit_rate": (None if math.isnan(actual) else round(actual, 4)),
            "count": count,
        }
        for i, (center, actual, count) in enumerate(
            zip(curve["bin_centers"], curve["actual_fraction"], curve["counts"], strict=True)
        )
        if count > 0
    ]
    return {"calibration_intercept": intercept, "calibration_slope": slope, "reliability_buckets": buckets}


def main() -> None:
    print(f"1. Loading real backfilled WNBA games for seasons {SEASONS} ...")
    result = build_dataset(
        "data/rebuild",
        SEASONS,
        minimum_history_games=MINIMUM_HISTORY_GAMES,
        minimum_team_games=MINIMUM_TEAM_GAMES,
    )
    frame = rows_to_frame(result.rows).sort("event_start_utc")
    print(
        f"   {frame.height} real walk-forward rows "
        f"(skipped_bootstrap={result.skipped_bootstrap}, skipped_cold_start_team={result.skipped_cold_start_team})"
    )
    if frame.height < 200:
        print("Not enough real rows to train meaningfully. Stopping honestly, not faking a result.")
        sys.exit(0)

    dates = frame["sports_event_date"].to_list()
    n_unique_dates = len(set(dates))
    test_size_dates = max(1, round(n_unique_dates * 0.20))
    calib_size_dates = max(1, round(n_unique_dates * 0.20))
    train_dates, val_dates, holdout_dates = date_cluster_split(
        dates, test_size=test_size_dates, calib_size=calib_size_dates
    )

    train_df = frame.filter(pl.col("sports_event_date").is_in(train_dates))
    val_df = frame.filter(pl.col("sports_event_date").is_in(val_dates))
    holdout_df = frame.filter(pl.col("sports_event_date").is_in(holdout_dates))
    print(f"2. Chronological 60/20/20 date-cluster split ({n_unique_dates} real distinct WNBA slate dates):")
    print(
        f"   train:   {train_df.height} rows, {len(train_dates)} dates, "
        f"[{train_df['sports_event_date'].min()}, {train_df['sports_event_date'].max()}]"
    )
    print(
        f"   valid:   {val_df.height} rows, {len(val_dates)} dates, "
        f"[{val_df['sports_event_date'].min()}, {val_df['sports_event_date'].max()}]"
    )
    print(
        f"   holdout: {holdout_df.height} rows, {len(holdout_dates)} dates, "
        f"[{holdout_df['sports_event_date'].min()}, {holdout_df['sports_event_date'].max()}] "
        f"(LOCKED -- touched exactly once, below)"
    )

    # ── Feature-set decision: on validation only, never holdout ──────────
    print(
        "3. Feature-set comparison (defensive_trend_gap in vs. out), fit on train, scored on validation only:"
    )
    val_labels = val_df["home_win"].to_list()
    feature_set_results: dict[str, dict[str, float]] = {}
    fitted_models: dict[str, LogisticRegression] = {}
    for name, features in (("full_3_feature", FULL_FEATURES), ("reduced_2_feature", REDUCED_FEATURES)):
        model = _fit_logreg(train_df, features)
        fitted_models[name] = model
        val_probs = _predict(model, val_df, features)
        metrics = _metrics(val_labels, val_probs)
        feature_set_results[name] = metrics
        coefs = dict(zip(features, model.coef_[0].tolist(), strict=True))
        print(f"   {name}: coef={coefs} intercept={model.intercept_[0]:.6f}")
        print(
            f"   {name}: validation brier={metrics['brier']:.5f} log_loss={metrics['log_loss']:.5f} "
            f"acc={metrics['accuracy']:.4f} ece={metrics['ece']:.4f}"
        )

    full_brier = feature_set_results["full_3_feature"]["brier"]
    reduced_brier = feature_set_results["reduced_2_feature"]["brier"]
    brier_delta = full_brier - reduced_brier  # negative = 3-feature better

    # Fold-wise audit (scripts/audit_wnba_defensive_trend.py) showed
    # defensive_trend_gap is harmful on 4/5 folds with unstable sign.
    # The decision rule now requires clear improvement (ΔBrier ≤ −0.002)
    # to retain it. Tiny single-split improvements (~0.00019) are noise.
    if brier_delta <= -0.002:
        winning_name, winning_features = "full_3_feature", FULL_FEATURES
        decision = (
            f"defensive_trend_gap provides clear Brier improvement "
            f"({brier_delta:+.5f} ≤ -0.002) -> KEEP, ship the 3-feature model."
        )
    else:
        winning_name, winning_features = "reduced_2_feature", REDUCED_FEATURES
        decision = (
            f"defensive_trend_gap does not provide clear Brier improvement "
            f"({brier_delta:+.5f} > -0.002); fold-wise audit (1/5 folds won, "
            f"sign-unstable) independently confirms -> DROP, ship the 2-feature model."
        )
    print(f"   DECISION: {decision}")

    final_model = fitted_models[winning_name]
    final_features = winning_features
    coefficients = dict(zip(final_features, final_model.coef_[0].tolist(), strict=True))
    intercept = float(final_model.intercept_[0])
    print(f"4. Final model family: {winning_name} ({final_features})")
    print(f"   coefficients={coefficients} intercept={intercept:.6f}")

    # ── Honest train/validation/holdout metrics for the FINAL feature set,
    # raw (uncalibrated) probabilities -- holdout evaluated exactly once,
    # below, after calibration method is also locked in on validation. ────
    train_labels = train_df["home_win"].to_list()
    train_probs = _predict(final_model, train_df, final_features)
    val_probs_final = _predict(final_model, val_df, final_features)
    train_metrics = _metrics(train_labels, train_probs)
    val_metrics = _metrics(val_labels, val_probs_final)
    print(f"5. Raw (uncalibrated) metrics for the FINAL {winning_name} model:")
    print(
        f"   train (in-sample, n={train_metrics['n']}): brier={train_metrics['brier']:.5f} "
        f"log_loss={train_metrics['log_loss']:.5f} acc={train_metrics['accuracy']:.4f}"
    )
    print(
        f"   validation (OOS, n={val_metrics['n']}): brier={val_metrics['brier']:.5f} "
        f"log_loss={val_metrics['log_loss']:.5f} acc={val_metrics['accuracy']:.4f}"
    )

    # ── Calibration method selection: chronological cross-fit on
    # validation only (never holdout), per rebuild/calibration.py. ───────
    print("6. Calibration comparison (chronological cross-fit on validation, n_blocks=4):")
    calibration_comparison: dict[str, dict[str, float | None]] = {}
    for method in ("identity", "platt", "temperature", "isotonic"):
        cf = cross_fit_calibration_eval(val_probs_final, val_labels, method, n_blocks=4)
        calibration_comparison[method] = {
            "n_eval_total": cf.n_eval_total,
            "log_loss": cf.log_loss,
            "brier": cf.brier,
            "ece": cf.ece,
            "calibration_intercept": cf.calibration_intercept,
            "calibration_slope": cf.calibration_slope,
        }
        print(f"   {method}: n={cf.n_eval_total} brier={cf.brier} log_loss={cf.log_loss} ece={cf.ece}")

    scored = {m: v for m, v in calibration_comparison.items() if v["brier"] is not None}
    winning_method = min(scored, key=lambda m: scored[m]["brier"]) if scored else "identity"
    print(f"   WINNING calibration method (by cross-fit validation Brier): {winning_method}")

    # Fit the winning calibrator on the ENTIRE validation set (still
    # disjoint from holdout) -- this becomes the persisted calibrator.
    calibrator = fit_calibrator(winning_method, val_probs_final, val_labels)

    # ── Locked holdout: touched exactly once, right here. ────────────────
    holdout_labels = holdout_df["home_win"].to_list()
    holdout_probs_raw = _predict(final_model, holdout_df, final_features)
    holdout_probs_calibrated = [calibrator.transform(p) for p in holdout_probs_raw]
    holdout_metrics_raw = _metrics(holdout_labels, holdout_probs_raw)
    holdout_metrics_calibrated = _metrics(holdout_labels, holdout_probs_calibrated)
    holdout_calibration_diag_raw = _calibration_diagnostics(holdout_labels, holdout_probs_raw)
    holdout_calibration_diag_calibrated = _calibration_diagnostics(holdout_labels, holdout_probs_calibrated)
    print(f"7. LOCKED HOLDOUT (touched once, n={holdout_metrics_raw['n']}):")
    print(
        f"   raw:        brier={holdout_metrics_raw['brier']:.5f} log_loss={holdout_metrics_raw['log_loss']:.5f} "
        f"acc={holdout_metrics_raw['accuracy']:.4f} ece={holdout_metrics_raw['ece']:.4f} "
        f"cal_slope={holdout_calibration_diag_raw['calibration_slope']:.4f} "
        f"cal_intercept={holdout_calibration_diag_raw['calibration_intercept']:.4f}"
    )
    print(
        f"   calibrated: brier={holdout_metrics_calibrated['brier']:.5f} log_loss={holdout_metrics_calibrated['log_loss']:.5f} "
        f"acc={holdout_metrics_calibrated['accuracy']:.4f} ece={holdout_metrics_calibrated['ece']:.4f} "
        f"cal_slope={holdout_calibration_diag_calibrated['calibration_slope']:.4f} "
        f"cal_intercept={holdout_calibration_diag_calibrated['calibration_intercept']:.4f}"
    )

    home_win_rate_overall = float(np.mean(frame["home_win"].to_numpy()))
    print(
        f"8. Context: overall real home-win rate across all {frame.height} rows = {home_win_rate_overall:.4f}"
    )

    # ── Persist artifacts under config/models/challengers/ ONLY. ─────────
    model_artifact_raw = {
        "model_version": "wnba-elo-trend-lr-rebuild-v1",
        "sport": "wnba",
        "method": "logistic_regression",
        "market_models": {
            "moneyline": {
                "feature_names": final_features,
                "coefficients": [coefficients[f] for f in final_features],
                "intercept": intercept,
                "positive_class": "home",
            }
        },
        "elo_config": WNBA_ELO_CONFIG,
        "elo_default_rating": DEFAULT_ELO,
        "trend_half_lives": [3.0, 25.0],
        "feature_set_comparison": feature_set_results,
        "feature_set_decision": decision,
        "training": {
            "framework": "chronological_60_20_20_date_cluster_split",
            "locked_holdout": True,
            "walk_forward_features": True,
            "minimum_history_games": MINIMUM_HISTORY_GAMES,
            "minimum_team_games": MINIMUM_TEAM_GAMES,
            "seasons": SEASONS,
            "train": {
                "observations": train_df.height,
                "start": str(train_df["sports_event_date"].min()),
                "end": str(train_df["sports_event_date"].max()),
                "unique_dates": len(train_dates),
            },
            "validation": {
                "observations": val_df.height,
                "start": str(val_df["sports_event_date"].min()),
                "end": str(val_df["sports_event_date"].max()),
                "unique_dates": len(val_dates),
            },
            "locked_holdout_range": {
                "observations": holdout_df.height,
                "start": str(holdout_df["sports_event_date"].min()),
                "end": str(holdout_df["sports_event_date"].max()),
                "unique_dates": len(holdout_dates),
            },
            "market_inputs_used": False,
        },
        "qualification": {
            "framework": "locked_complete_date_60_20_20",
            "locked_holdout": True,
            "train_metrics_in_sample": train_metrics,
            "validation_metrics": val_metrics,
            "holdout_metrics_raw": holdout_metrics_raw,
            "holdout_metrics_calibrated": holdout_metrics_calibrated,
            "holdout_calibration_diagnostics_raw": holdout_calibration_diag_raw,
            "holdout_calibration_diagnostics_calibrated": holdout_calibration_diag_calibrated,
            "calibration_method": winning_method,
            "calibration_comparison": calibration_comparison,
            "overall_home_win_rate": home_win_rate_overall,
            "qualified": False,
            "status": "research_shadow_only",
        },
        "provenance": {
            "availability_basis": "capture_time_only",
            "commercial_use_status": "unresolved",
            "production_allowed": False,
            "note": (
                "Trained entirely on real, backfilled SportsDataverse WNBA schedule/team_box data "
                "(2022-2025). Every source row's observed_at_utc is this repo's real backfill capture "
                "time (2026-08), not a genuine historical observation time -- the chronological "
                "60/20/20 split above is real, non-fabricated DESCRIPTIVE BACKTESTING over real "
                "historical game order and real final scores, NOT genuine prospective point-in-time "
                "evidence. The underlying SportsDataverse data additionally has an UNRESOLVED "
                "commercial-use-rights status -- this model is research/shadow-only regardless of "
                "how well it performs and must never be routed toward anything claiming production "
                "clearance. See docs/model_audit/models/WNBA_ELO_TREND_LR_REBUILD_V1.md."
            ),
            "sibling_of_incumbent": "wnba-elo-trend-lr-v4 (config/models/wnba-elo-trend-lr-v4.json) -- "
            "same family, never loaded as candidate, referenced only as a design guide",
            "data_root": "data/rebuild/normalized/wnba",
            "seasons": SEASONS,
            "dropped_incomplete_games": "see docs/model_audit/models/WNBA_ELO_TREND_LR_REBUILD_V1.md",
        },
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    artifact_hash = hashlib.sha256(
        json.dumps(model_artifact_raw, sort_keys=True, default=str).encode()
    ).hexdigest()
    model_artifact_raw["artifact_hash"] = artifact_hash

    calibrator_artifact_raw = {
        "model_name": "wnba-elo-trend-lr-rebuild-v1",
        "method": calibrator.method,
        "parameters": calibrator.parameters,
        "base_model_hash": artifact_hash,
        "training_range": {
            "start": str(val_df["sports_event_date"].min()),
            "end": str(val_df["sports_event_date"].max()),
        },
        "n_training_oof": val_df.height,
        "calibration_comparison": calibration_comparison,
        "provenance": {
            "availability_basis": "capture_time_only",
            "commercial_use_status": "unresolved",
            "production_allowed": False,
            "note": "Fitted on the validation split only (disjoint from both train and the locked holdout). "
            "Same capture-time-only / unresolved-commercial-rights caveats as the base model artifact apply.",
        },
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    calibrator_hash = hashlib.sha256(
        json.dumps(calibrator_artifact_raw, sort_keys=True, default=str).encode()
    ).hexdigest()
    calibrator_artifact_raw["calibrator_hash"] = calibrator_hash

    challenger_dir = Path("config/models/challengers")
    challenger_dir.mkdir(parents=True, exist_ok=True)
    model_path = challenger_dir / "wnba-elo-trend-lr-rebuild-v1.json"
    calibrator_path = challenger_dir / "wnba-elo-trend-lr-rebuild-v1-calibrator.json"
    model_path.write_text(json.dumps(model_artifact_raw, indent=2, sort_keys=True, default=str))
    calibrator_path.write_text(json.dumps(calibrator_artifact_raw, indent=2, sort_keys=True, default=str))
    print(f"9. Model artifact saved to {model_path}")
    print(f"   Calibrator artifact saved to {calibrator_path}")

    results_path = Path("outputs/rebuild/wnba/wnba_rebuild_v1_training_results.json")
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(
            {
                "feature_set_results": feature_set_results,
                "feature_set_decision": decision,
                "final_feature_set": winning_name,
                "final_features": final_features,
                "coefficients": coefficients,
                "intercept": intercept,
                "train_metrics": train_metrics,
                "validation_metrics": val_metrics,
                "holdout_metrics_raw": holdout_metrics_raw,
                "holdout_metrics_calibrated": holdout_metrics_calibrated,
                "holdout_calibration_diagnostics_raw": holdout_calibration_diag_raw,
                "holdout_calibration_diagnostics_calibrated": holdout_calibration_diag_calibrated,
                "calibration_comparison": calibration_comparison,
                "winning_calibration_method": winning_method,
                "artifact_hash": artifact_hash,
                "calibrator_hash": calibrator_hash,
                "n_rows_total": frame.height,
                "skipped_bootstrap": result.skipped_bootstrap,
                "skipped_cold_start_team": result.skipped_cold_start_team,
            },
            indent=2,
            default=str,
        )
    )
    print(f"10. Results saved to {results_path}")


if __name__ == "__main__":
    main()
