"""MLB v9 Phase 3 ablation — does ``offense_pit_gap`` beat the v9 control?

Isolating-ladder style comparison (see the 2026-08-18 ``ec60f48`` commit and
``docs/MODEL_IMPROVEMENTS.md`` section 8): exactly one term changes between
``elo_trend_park_weather_starter_era_bullpen_control`` and
``..._offense_pit`` (the batter PIT priors composite added on top). Uses the
same chronological 60/20/20 split and 2000-resample date-cluster paired
bootstrap as the prior ladder rungs, on log loss (the metric the prior
ladder's own summary table reported as "dLL").

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/mlb_v9_offense_pit_ablation.py
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from model_prediction.validation import (
    FEATURE_VARIANTS,
    ValidationRow,
    _fit,
    _predict,
    build_walk_forward_rows,
    chronological_split,
)

SPORT = "mlb"
CONTROL = "elo_trend_park_weather_starter_era_bullpen_control"
CANDIDATE = "elo_trend_park_weather_starter_era_bullpen_offense_pit"


def _log_loss_terms(probabilities: Sequence[float], rows: Sequence[ValidationRow]) -> list[float]:
    terms = []
    for probability, row in zip(probabilities, rows, strict=True):
        clipped = min(1 - 1e-9, max(1e-9, probability))
        outcome = row.outcome
        terms.append(-(outcome * math.log(clipped) + (1 - outcome) * math.log(1 - clipped)))
    return terms


def _cluster_bootstrap_log_loss_delta(
    incumbent_probabilities: Sequence[float],
    candidate_probabilities: Sequence[float],
    rows: Sequence[ValidationRow],
    *,
    seed: int,
    n_resamples: int = 2_000,
) -> dict:
    incumbent_terms = _log_loss_terms(incumbent_probabilities, rows)
    candidate_terms = _log_loss_terms(candidate_probabilities, rows)
    by_date: dict[str, list[float]] = defaultdict(list)
    for incumbent_term, candidate_term, row in zip(incumbent_terms, candidate_terms, rows, strict=True):
        by_date[row.date].append(candidate_term - incumbent_term)
    dates = sorted(by_date)
    observed = mean(value for day in dates for value in by_date[day])
    rng = random.Random(seed)
    samples = []
    for _ in range(n_resamples):
        sampled_days = [rng.choice(dates) for _ in dates]
        values = [value for day in sampled_days for value in by_date[day]]
        samples.append(mean(values))
    samples.sort()
    p_better = sum(1 for value in samples if value < 0) / n_resamples
    low_index = int(0.025 * (len(samples) - 1))
    high_index = int(0.975 * (len(samples) - 1))
    return {
        "metric": "candidate_log_loss_minus_incumbent",
        "dates": len(dates),
        "resamples": n_resamples,
        "point_estimate": round(observed, 8),
        "ci_95_low": round(samples[low_index], 8),
        "ci_95_high": round(samples[high_index], 8),
        "p_better": round(p_better, 4),
    }


def main() -> int:
    data_root = PROJECT_ROOT / "data"
    store = FeatureStore(data_root)

    print("Building walk-forward rows ...")
    rows = build_walk_forward_rows(store, SPORT)
    print(f"  {len(rows)} rows from {len({row.date for row in rows})} dates")

    train, validation, holdout, _split_meta = chronological_split(rows)
    print(f"  train: {len(train)}, validation: {len(validation)}, holdout: {len(holdout)}")

    control_features = FEATURE_VARIANTS[CONTROL]
    candidate_features = FEATURE_VARIANTS[CANDIDATE]
    added = set(candidate_features) - set(control_features)
    print(f"\ncontrol:   {control_features}")
    print(f"candidate: {candidate_features}")
    print(f"added term(s): {sorted(added)}")

    control_model = _fit(train, control_features)
    candidate_model = _fit(train, candidate_features)
    control_holdout = _predict(control_model, holdout, control_features)
    candidate_holdout = _predict(candidate_model, holdout, candidate_features)

    available = [row for row in holdout if getattr(row, "offense_pit_available", False)]
    print(f"\nholdout rows: {len(holdout)}; offense_pit_available: {len(available)}")

    log_loss_delta = _cluster_bootstrap_log_loss_delta(
        control_holdout, candidate_holdout, holdout, seed=20260820
    )
    brier_delta = _cluster_bootstrap_brier_delta(control_holdout, candidate_holdout, holdout, seed=20260820)

    print("\n" + "=" * 72)
    print("offense_pit_gap vs control -- 2000-resample date-cluster paired bootstrap")
    print("=" * 72)
    print(json.dumps({"log_loss_delta": log_loss_delta, "brier_delta": brier_delta}, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
