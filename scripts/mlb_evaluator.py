"""Standardized evaluator for binary MLB candidate models (research tooling).

One evaluator for every candidate: N, coverage, LogLoss, Brier, ECE,
calibration slope/intercept, accuracy, AUC, fold-by-fold metrics, paired
ΔLogLoss / ΔBrier vs the v8 baseline, and date-cluster bootstrap CI with
P(challenger better). Economic results are secondary and never decide
feature retention.

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/mlb_evaluator.py \
        --variants elo_probability elo_trend_park_weather_starter_bullpen
        [--baseline elo_trend_park_weather_starter_bullpen]
        [--out outputs/research/mlb_evaluator/report.json]

All probabilities come from the PINNED v8 cohort (scripts/
mlb_research_common.pinned_cohort) — every variant is fit on the same
train rows and graded on the same exact-holdout rows, so paired
comparisons are apples-to-apples.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

from mlb_research_common import pinned_cohort  # noqa: E402
from model_prediction.config import PROJECT_ROOT  # noqa: E402
from model_prediction.validation import (  # noqa: E402
    FEATURE_VARIANTS,
    _all_rows_calibration,
    _fit,
    _predict,
)

DEFAULT_BASELINE = "elo_trend_park_weather_starter_bullpen"
BOOTSTRAP_SEED = 20260815
N_BOOTSTRAP = 2000


def _scores(probabilities: list[float], outcomes: list[int]) -> dict:
    probs = np.clip(np.asarray(probabilities, dtype=float), 1e-12, 1 - 1e-12)
    y = np.asarray(outcomes, dtype=int)
    log_loss = float(-np.mean(y * np.log(probs) + (1 - y) * np.log(1 - probs)))
    brier = float(np.mean((probs - y) ** 2))
    accuracy = float(np.mean((probs >= 0.5) == y))
    auc = float(roc_auc_score(y, probs)) if len(set(y)) > 1 else None
    return {"log_loss": log_loss, "brier": brier, "accuracy": accuracy, "auc": auc}


def _date_cluster_bootstrap_paired(
    dates: list[str],
    p_base: list[float],
    p_cand: list[float],
    y: list[int],
    metric: str,
    *,
    seed: int = BOOTSTRAP_SEED,
    n_bootstrap: int = N_BOOTSTRAP,
) -> dict:
    """Paired bootstrap over DATE clusters (not rows): resample days with
    replacement, recompute the metric delta each time, report CI + P(better)."""
    by_day: dict[str, list[int]] = defaultdict(list)
    for i, day in enumerate(dates):
        by_day[day].append(i)
    clusters = list(by_day.values())
    rng = np.random.default_rng(seed)
    base_arr = np.asarray(p_base)
    cand_arr = np.asarray(p_cand)
    y_arr = np.asarray(y)

    def metric_delta(indices: list[int]) -> float:
        base_m = _scores(base_arr[indices].tolist(), y_arr[indices].tolist())[metric]
        cand_m = _scores(cand_arr[indices].tolist(), y_arr[indices].tolist())[metric]
        # Negative delta = challenger better (lower is better for both)
        return cand_m - base_m

    observed = metric_delta(list(range(len(p_base))))
    deltas = []
    for _ in range(n_bootstrap):
        sample: list[int] = []
        for _ in range(len(clusters)):
            sample.extend(clusters[int(rng.integers(0, len(clusters)))])
        deltas.append(metric_delta(sample))
    deltas_arr = np.asarray(deltas)
    p_better = float(np.mean(deltas_arr < 0))
    return {
        "observed_delta": round(observed, 6),
        "ci_95": [round(float(np.percentile(deltas_arr, 2.5)), 6),
                  round(float(np.percentile(deltas_arr, 97.5)), 6)],
        "P_challenger_better": round(p_better, 4),
        "n_bootstrap": n_bootstrap,
        "seed": seed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", nargs="+", required=True,
                        help="feature-variant names (validation.FEATURE_VARIANTS keys)")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--out", default=str(
        PROJECT_ROOT / "outputs/research/mlb_evaluator/report.json"))
    args = parser.parse_args()

    cohort = pinned_cohort()
    contract = cohort["contract"]
    train, validation, exact_holdout = cohort["train"], cohort["validation"], cohort["exact_holdout"]

    # Baseline: v8's shipped feature set, refit on the pinned train
    # (the ablation harness's own convention — challengers must be
    # compared against the same-refit baseline, not against drifted
    # shipped coefficients; see docs/V8_REPRODUCTION.md).
    base_probabilities = _predict(_fit(train, FEATURE_VARIANTS[args.baseline]),
                                  exact_holdout, FEATURE_VARIANTS[args.baseline])
    base_metrics = _scores(base_probabilities, [r.outcome for r in exact_holdout])
    base_cal = _all_rows_calibration(base_probabilities, exact_holdout)

    report: dict = {
        "schema": "mlb-standard-evaluator-v1",
        "generated_contract": {
            "holdout_rows": len(exact_holdout),
            "recorded_holdout": contract["holdout_observations"],
            "backfill_excluded": len(cohort["backfill_ids"]),
            "baseline_variant": args.baseline,
            "threshold": contract["threshold"],
        },
        "baseline": {"metrics": base_metrics, "calibration": base_cal},
        "variants": {},
    }

    dates = [r.date for r in exact_holdout]
    outcomes = [r.outcome for r in exact_holdout]

    for variant in args.variants:
        names = list(FEATURE_VARIANTS[variant])
        probs = _predict(_fit(train, names), exact_holdout, names)
        metrics = _scores(probs, outcomes)
        cal = _all_rows_calibration(probs, exact_holdout)
        coverage = float(mean(1.0 for r, p in zip(exact_holdout, probs, strict=True) if p == p))
        per_split = {
            "train": _all_rows_calibration(_predict(_fit(train, names), train, names), train),
            "validation": _all_rows_calibration(
                _predict(_fit(train, names), validation, names), validation),
            "holdout": cal,
        }
        paired = {
            "log_loss": _date_cluster_bootstrap_paired(dates, base_probabilities, probs, outcomes, "log_loss"),
            "brier": _date_cluster_bootstrap_paired(dates, base_probabilities, probs, outcomes, "brier"),
        }
        report["variants"][variant] = {
            "features": names,
            "metrics": metrics,
            "coverage": coverage,
            "calibration": cal,
            "per_split": per_split,
            "paired_vs_baseline": paired,
        }
        print(f"{variant}: LL={metrics['log_loss']:.4f} Brier={metrics['brier']:.4f} "
              f"ECE={cal.get('expected_calibration_error')} acc={metrics['accuracy']:.4f} "
              f"AUC={metrics['auc']} | dLL={paired['log_loss']['observed_delta']:+.5f} "
              f"dBr={paired['brier']['observed_delta']:+.5f} "
              f"P(better|LL)={paired['log_loss']['P_challenger_better']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
