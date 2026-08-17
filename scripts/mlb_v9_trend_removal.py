"""MLB v9 -- trend-removal candidate on v8's own split.

The E variant (remove trend_gap) is the only Step 3 feature change with
real directional evidence: it won 4/5 folds with an all-negative bootstrap
CI on the full frozen window and again on the coverage-restricted window.
This is the THIRD, independent test: v8's own recorded split (train <=
2025-07-22, validation 2025-07-23..2026-04-10, holdout 2026-04-11..2026-07-29),
comparing LR-without-trend vs LR-with (v8) on the locked holdout, with a
date-cluster bootstrap. Economics (threshold replay) reported, not gating.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sklearn.linear_model import LogisticRegression

from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from scripts.mlb_v9_ablation_matrix import CONTROL_FEATURES, FROZEN_TABLE, load_frozen_rows
from scripts.mlb_v9_calibration_xgb import _safe_metrics

ARTIFACT_PATH = Path("config/models/mlb-elo-trend-lr-v8.json")
NO_TREND_FEATURES = tuple(f for f in CONTROL_FEATURES if f != "trend_gap")


def _matrix(rows, features) -> np.ndarray:
    return np.asarray([[float(getattr(r, f)) for f in features] for r in rows])


def _bootstrap_p_better(incumbent, candidate, rows, seed=20260817):
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
    parser.parse_args()

    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    training = artifact["training"]
    train_end = training["coefficient_fit"]["end"]
    val_end = training["threshold_selection"]["end"]

    from model_prediction.validation import chronological_split

    all_rows = load_frozen_rows(FROZEN_TABLE)
    rows = [
        r for r in all_rows if all(float(getattr(r, f)) == float(getattr(r, f)) for f in CONTROL_FEATURES)
    ]
    train, validation, holdout, _ = chronological_split(
        rows, train_end_date=train_end, validation_end_date=val_end
    )

    class _Row:
        __slots__ = ("date", "outcome")

        def __init__(self, date: str, outcome: int) -> None:
            self.date = date
            self.outcome = outcome

    holdout_rows = [_Row(r.date, r.outcome) for r in holdout]
    y_hold = [r.outcome for r in holdout]

    v8_model = LogisticRegression(max_iter=2_000, solver="lbfgs")
    v8_model.fit(_matrix(train, CONTROL_FEATURES), [r.outcome for r in train])
    v8_probs = [float(p[1]) for p in v8_model.predict_proba(_matrix(holdout, CONTROL_FEATURES))]

    no_trend = LogisticRegression(max_iter=2_000, solver="lbfgs")
    no_trend.fit(_matrix(train, NO_TREND_FEATURES), [r.outcome for r in train])
    no_trend_probs = [float(p[1]) for p in no_trend.predict_proba(_matrix(holdout, NO_TREND_FEATURES))]

    v8_metrics = _safe_metrics(v8_probs, y_hold)
    nt_metrics = _safe_metrics(no_trend_probs, y_hold)
    bootstrap, p_better = _bootstrap_p_better(v8_probs, no_trend_probs, holdout_rows)

    # Threshold replay (economics, reported not gating): v8's shipped
    # threshold applied to both models' holdout probabilities.
    threshold = float(artifact["market_models"]["moneyline"]["confidence_threshold"])
    for probs in (v8_probs, no_trend_probs):
        calls = [(p, r.outcome) for p, r in zip(probs, holdout, strict=True) if max(p, 1 - p) >= threshold]
        hits = sum(1 for p, outcome in calls if (p >= 0.5) == bool(outcome))
        if probs is v8_probs:
            v8_metrics["replay"] = {
                "calls": len(calls),
                "hit_rate": round(hits / len(calls), 4) if calls else None,
            }
        else:
            nt_metrics["replay"] = {
                "calls": len(calls),
                "hit_rate": round(hits / len(calls), 4) if calls else None,
            }

    report = {
        "splits": {"train": len(train), "validation": len(validation), "holdout": len(holdout)},
        "threshold": threshold,
        "v8_with_trend": v8_metrics,
        "v9_no_trend": nt_metrics,
        "delta_brier": round(nt_metrics["brier"] - v8_metrics["brier"], 6),
        "bootstrap": bootstrap,
        "p_better": p_better,
    }
    out_dir = Path("outputs/research/mlb_v9_trend_removal")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "trend_removal_v8_split.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    report["_file"] = str(out_path)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
