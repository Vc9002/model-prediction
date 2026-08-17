"""WNBA v5 paired test (Step 8 item 1) -- drop defensive_trend_gap.

v4 = elo_probability + trend_gap + defensive_trend_gap. Prior session
evidence says defensive_trend_gap was HARMFUL. v5 challenger removes it,
one change at a time, evaluated on v4's own recorded split with a
date-cluster bootstrap on the locked holdout. Economics (threshold
replay) reported, not gating.
"""

from __future__ import annotations

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

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from model_prediction.validation import build_walk_forward_rows, chronological_split
from scripts.mlb_v9_calibration_xgb import _safe_metrics

ARTIFACT_PATH = PROJECT_ROOT / "config/models/wnba-elo-trend-lr-v4.json"
V4_FEATURES = ("elo_probability", "trend_gap", "defensive_trend_gap")
V5_FEATURES = ("elo_probability", "trend_gap")


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
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    training = artifact["training"]
    train_end = training["coefficient_fit"]["end"]
    val_end = training["threshold_selection"]["end"]
    hold_end = training["locked_holdout"]["end"]

    from datetime import date, timedelta

    rows_end = (date.fromisoformat(hold_end) + timedelta(days=1)).isoformat()
    store = FeatureStore(PROJECT_ROOT / "data")
    rows = build_walk_forward_rows(store, "wnba", end_date=rows_end)
    rows = [r for r in rows if all(float(getattr(r, f)) == float(getattr(r, f)) for f in V4_FEATURES)]
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

    v4 = LogisticRegression(max_iter=2_000, solver="lbfgs")
    v4.fit(_matrix(train, V4_FEATURES), [r.outcome for r in train])
    v4_probs = [float(p[1]) for p in v4.predict_proba(_matrix(holdout, V4_FEATURES))]

    v5 = LogisticRegression(max_iter=2_000, solver="lbfgs")
    v5.fit(_matrix(train, V5_FEATURES), [r.outcome for r in train])
    v5_probs = [float(p[1]) for p in v5.predict_proba(_matrix(holdout, V5_FEATURES))]

    v4_metrics = _safe_metrics(v4_probs, y_hold)
    v5_metrics = _safe_metrics(v5_probs, y_hold)
    bootstrap, p_better = _bootstrap_p_better(v4_probs, v5_probs, holdout_rows)

    threshold = float(artifact["market_models"]["moneyline"]["confidence_threshold"])
    for probs in (v4_probs, v5_probs):
        calls = [(p, r.outcome) for p, r in zip(probs, holdout, strict=True) if max(p, 1 - p) >= threshold]
        hits = sum(1 for p, outcome in calls if (p >= 0.5) == bool(outcome))
        if probs is v4_probs:
            v4_metrics["replay"] = {
                "calls": len(calls),
                "hit_rate": round(hits / len(calls), 4) if calls else None,
            }
        else:
            v5_metrics["replay"] = {
                "calls": len(calls),
                "hit_rate": round(hits / len(calls), 4) if calls else None,
            }

    report = {
        "splits": {"train": len(train), "validation": len(validation), "holdout": len(holdout)},
        "threshold": threshold,
        "v4": v4_metrics,
        "v5_no_defense": v5_metrics,
        "delta_brier": round(v5_metrics["brier"] - v4_metrics["brier"], 6),
        "bootstrap": bootstrap,
        "p_better": p_better,
    }
    out_dir = PROJECT_ROOT / "outputs/research/wnba_v5"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "paired_test.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    report["_file"] = str(out_path)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
