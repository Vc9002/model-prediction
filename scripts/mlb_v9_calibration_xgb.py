"""MLB v9 Steps 4+5 -- LR vs XGBoost on v8's exact feature set, then
calibration challengers (Identity / Platt / Isotonic / Temperature) on
the winner's out-of-fold probabilities.

Uses the v8 artifact's own recorded date boundaries (train <= 2025-07-22,
validation 2025-07-23..2026-04-10, locked holdout 2026-04-11..2026-07-29)
and the frozen point-in-time table so every model and calibrator sees the
SAME events, features, and folds. Calibrators are fit on VALIDATION
predictions only; the locked holdout is touched once, at the end.

Primary metrics (accuracy-first): Brier, LogLoss, ECE on the holdout,
with a date-cluster bootstrap for each challenger vs the LR-Identity
baseline. Threshold-replay economics are reported, never gating.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from model_prediction.calibration import (
    IsotonicCalibrator,
    TrainablePlattCalibrator,
    calibration_metrics,
)
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from model_prediction.validation import ValidationRow, chronological_split
from scripts.mlb_v9_ablation_matrix import CONTROL_FEATURES, FROZEN_TABLE, load_frozen_rows

ARTIFACT_PATH = Path("config/models/mlb-elo-trend-lr-v8.json")


def _matrix(rows: list[ValidationRow], features: tuple[str, ...]) -> np.ndarray:
    return np.asarray([[float(getattr(r, f)) for f in features] for r in rows])


def _temperature_calibrator(probs: list[float], outcomes: list[int]) -> callable:
    """Fit temperature T by grid search minimizing validation log-loss."""
    clipped = [min(1 - 1e-9, max(1e-9, p)) for p in probs]
    logits = [math.log(p / (1 - p)) for p in clipped]
    best_t, best_loss = 1.0, float("inf")
    for t in [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 1.7, 2.0]:
        loss = 0.0
        for z, y in zip(logits, outcomes, strict=True):
            p = 1.0 / (1.0 + math.exp(-z / t))
            p = min(1 - 1e-9, max(1e-9, p))
            loss += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        if loss < best_loss:
            best_loss, best_t = loss, t

    def transform(probability: float) -> float:
        p = min(1 - 1e-9, max(1e-9, probability))
        z = math.log(p / (1 - p))
        return 1.0 / (1.0 + math.exp(-z / best_t))

    return transform


def _safe_metrics(probs: list[float], outcomes: list[int]) -> dict:
    """calibration_metrics with extreme-probability protection.

    XGBoost outputs can sit within 1e-9 of 0/1; the library's internal
    logistic-calibration Newton iterations overflow on such inputs. Pre-clip
    to [1e-5, 1-1e-5] and fall back to direct Brier/LogLoss/ECE if the
    library still overflows (the slope is then reported as None).
    """
    clipped = [min(1 - 1e-5, max(1e-5, p)) for p in probs]
    try:
        metrics = calibration_metrics(clipped, outcomes)
        return {
            "brier": float(metrics["brier_score"]),
            "log_loss": float(metrics["log_loss"]),
            "ece": float(metrics["expected_calibration_error"]),
            "slope": metrics.get("calibration_slope"),
            "fallback": False,
        }
    except OverflowError:
        n = len(clipped)
        brier = sum((p - y) ** 2 for p, y in zip(clipped, outcomes, strict=True)) / n
        log_loss = (
            -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(clipped, outcomes, strict=True))
            / n
        )
        ece = 0.0
        for lower_index in range(10):
            lower, upper = lower_index / 10, (lower_index + 1) / 10
            members = [(p, y) for p, y in zip(clipped, outcomes, strict=True) if lower <= p < upper]
            if members:
                ece += (len(members) / n) * abs(mean(p for p, _ in members) - mean(y for _, y in members))
        return {
            "brier": round(brier, 6),
            "log_loss": round(log_loss, 6),
            "ece": round(ece, 6),
            "slope": None,
            "fallback": True,
        }


def _bootstrap_p_better(
    incumbent: list[float], candidate: list[float], rows: list, seed: int = 20260817
) -> tuple[dict, float]:
    bootstrap = _cluster_bootstrap_brier_delta(incumbent, candidate, rows, seed=seed)
    by_date: dict[str, list[float]] = defaultdict(list)
    for inc, cand, row in zip(incumbent, candidate, rows, strict=True):
        by_date[row.date].append((cand - row.outcome) ** 2 - (inc - row.outcome) ** 2)
    rng = random.Random(seed)
    dates_sorted = sorted(by_date)
    better = 0
    for _ in range(2000):
        sampled = [rng.choice(dates_sorted) for _ in dates_sorted]
        vals = [v for day in sampled for v in by_date[day]]
        if mean(vals) < 0:
            better += 1
    return bootstrap, round(better / 2000, 4)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xgb-rounds", type=int, default=300)
    args = parser.parse_args()

    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    training = artifact["training"]
    train_end = training["coefficient_fit"]["end"]
    val_end = training["threshold_selection"]["end"]

    all_rows = load_frozen_rows(FROZEN_TABLE)
    # v8's training contract: rows with unavailable features are dropped.
    rows = [
        r for r in all_rows if all(float(getattr(r, f)) == float(getattr(r, f)) for f in CONTROL_FEATURES)
    ]
    train, validation, holdout, _ = chronological_split(
        rows, train_end_date=train_end, validation_end_date=val_end
    )

    import xgboost as xgb
    from sklearn.linear_model import LogisticRegression

    x_train, y_train = _matrix(train, CONTROL_FEATURES), [r.outcome for r in train]
    x_val, y_val = _matrix(validation, CONTROL_FEATURES), [r.outcome for r in validation]
    x_hold, y_hold = _matrix(holdout, CONTROL_FEATURES), [r.outcome for r in holdout]

    # LR (v8's own model class + solver).
    lr = LogisticRegression(max_iter=2_000, solver="lbfgs")
    lr.fit(x_train, y_train)
    lr_val = [float(p[1]) for p in lr.predict_proba(x_val)]
    lr_hold = [float(p[1]) for p in lr.predict_proba(x_hold)]

    # XGBoost on the same events/features.
    xgb_model = xgb.XGBClassifier(
        n_estimators=args.xgb_rounds,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        eval_metric="logloss",
        early_stopping_rounds=30,
        random_state=20260817,
    )
    xgb_model.fit(
        x_train,
        y_train,
        eval_set=[(x_val, y_val)],
        verbose=False,
    )
    xgb_val = [float(p[1]) for p in xgb_model.predict_proba(x_val)]
    xgb_hold = [float(p[1]) for p in xgb_model.predict_proba(x_hold)]

    # Calibrators, each fit on VALIDATION predictions only.
    platt = TrainablePlattCalibrator.fit(lr_val, y_val, "mlb-elo-trend-lr-v8")
    isotonic = IsotonicCalibrator.fit(lr_val, y_val, "mlb-elo-trend-lr-v8")
    temperature = _temperature_calibrator(lr_val, y_val)

    class _Row:
        __slots__ = ("date", "outcome")

        def __init__(self, date: str, outcome: int) -> None:
            self.date = date
            self.outcome = outcome

    holdout_rows = [_Row(r.date, r.outcome) for r in holdout]

    variants: dict[str, list[float]] = {
        "lr_identity": lr_hold,
        "lr_platt": [platt.transform(p) for p in lr_hold],
        "lr_isotonic": [isotonic.transform(p) for p in lr_hold],
        "lr_temperature": [temperature(p) for p in lr_hold],
        "xgb_identity": xgb_hold,
        "xgb_isotonic": [IsotonicCalibrator.fit(xgb_val, y_val, "mlb-v9-xgb").transform(p) for p in xgb_hold],
    }

    baseline = variants["lr_identity"]
    report: dict = {"splits": {}, "variants": {}}
    report["splits"] = {
        "train": len(train),
        "validation": len(validation),
        "holdout": len(holdout),
        "boundaries": {"train_end": train_end, "val_end": val_end},
    }
    baseline_metrics = _safe_metrics(baseline, y_hold)
    for name, probs in variants.items():
        metrics = _safe_metrics(probs, y_hold)
        entry = {
            "brier": metrics["brier"],
            "log_loss": metrics["log_loss"],
            "ece": metrics["ece"],
            "calibration_slope": metrics["slope"],
            "metrics_fallback": metrics["fallback"],
        }
        if name != "lr_identity":
            bootstrap, p_better = _bootstrap_p_better(baseline, probs, holdout_rows)
            entry["vs_lr_identity"] = {
                "bootstrap": bootstrap,
                "p_better": p_better,
                "delta_brier": round(entry["brier"] - baseline_metrics["brier"], 6),
            }
        report["variants"][name] = entry

    out_dir = Path("outputs/research/mlb_v9_calibration_xgb")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "calibration_xgb.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    report["_file"] = str(out_path)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
