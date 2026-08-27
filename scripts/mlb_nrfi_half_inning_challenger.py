"""MLB NRFI challengers: two-half-inning decomposition + umpire features.

Matches the incumbent evaluation exactly (60/20/20 chronological split on
the same snapshot ledger as scripts/mlb_nrfi_first_inning_research.py) and
compares, on the same locked test rows:

- incumbent single-classifier (reproduction gate: holdout logloss must
  reproduce the 2026-08-26 shipped value 0.6910 before anything else);
- two-half-inning model — P(NRFI) = P(away=0) × P(home=0) from two
  logistic regressions over the same feature block (the plan's
  challenger 2; independence is the documented approximation);
- both models with the two plate-umpire features appended
  (shrunk umpire first-inning run rate and YRFI rate — plan item E;
  officials are in 6,692/6,698 snapshots with role tags).

Research-only: no promotion, no ledger writes.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.calibration import calibration_metrics
from model_prediction.domain import parse_utc
from model_prediction.models.mlb_first_inning import (
    DEFAULT_SNAPSHOT_PATH,
    FEATURE_NAMES,
    UMPIRE_FEATURE_NAMES,
    MLBFirstInningModel,
    MLBHalfInningModel,
    build_first_inning_ledger,
    compute_first_inning_priors,
)

# The shipped incumbent number from the 2026-08-26 NRFI session (handoff:
# holdout 1,337 games, logloss 0.6910 vs incumbent 0.6945 vs proxy 0.6950
# — 0.6910 is the improved model's holdout logloss).
SHIPPED_HOLDOUT_LOGLOSS = 0.6910
REPRODUCTION_TOLERANCE = 0.002

TRAIN_FRAC = 0.60
VAL_FRAC = 0.20


def _chronological_split(rows, train_frac: float = TRAIN_FRAC, val_frac: float = VAL_FRAC):
    """Fractional chronological split (same mechanics as the research
    script that produced the shipped baseline)."""
    n = len(rows)
    train_n = int(n * train_frac)
    val_n = int(n * val_frac)
    return rows[:train_n], rows[train_n : train_n + val_n], rows[train_n + val_n :]


def _logloss(predictions: list[float], outcomes: list[int]) -> float:
    clipped = [min(1 - 1e-12, max(1e-12, p)) for p in predictions]
    return -sum(
        y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(clipped, outcomes, strict=True)
    ) / len(clipped)


def _row_logloss(p: float, y: int) -> float:
    pc = min(1 - 1e-12, max(1e-12, p))
    return -(y * math.log(pc) + (1 - y) * math.log(1 - pc))


def _paired_logloss_delta_ci(
    base_preds: list[float],
    candidate_preds: list[float],
    outcomes: list[int],
    dates: list[str],
    *,
    samples: int = 2000,
    seed: int = 20260827,
) -> dict:
    """Date-clustered bootstrap on the mean per-row logloss delta
    (candidate minus base; negative = candidate better)."""
    per_row = [
        _row_logloss(cp, y) - _row_logloss(bp, y)
        for cp, bp, y in zip(candidate_preds, base_preds, outcomes, strict=True)
    ]
    by_date: dict[str, list[float]] = {}
    for d, delta in zip(dates, per_row, strict=True):
        by_date.setdefault(d, []).append(delta)
    day_keys = sorted(by_date)
    gen = random.Random(seed)
    boot = []
    for _ in range(samples):
        sampled = [gen.choice(day_keys) for _ in day_keys]
        total = sum(delta for day in sampled for delta in by_date[day])
        n_rows = sum(len(by_date[day]) for day in sampled)
        boot.append(total / n_rows if n_rows else 0.0)
    boot.sort()
    return {
        "point_estimate": round(sum(per_row) / len(per_row), 6),
        "ci_95_low": round(boot[int(samples * 0.025)], 6),
        "ci_95_high": round(boot[int(samples * 0.975)], 6),
        "resamples": samples,
    }


def _report_variant(
    label: str,
    preds_nrfi: list[float],
    test_rows,
) -> dict:
    outcomes = [r.nrfi for r in test_rows]
    return {
        "label": label,
        "n_test": len(test_rows),
        "logloss": round(_logloss(preds_nrfi, outcomes), 6),
        "brier": round(
            sum((p - y) ** 2 for p, y in zip(preds_nrfi, outcomes, strict=True)) / len(outcomes), 6
        ),
        "calibration_ece": calibration_metrics(preds_nrfi, outcomes).get("expected_calibration_error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", default=str(DEFAULT_SNAPSHOT_PATH))
    args = parser.parse_args()

    # Mirror the research script's exact flow: split the full-prior ledger
    # to find train_end, freeze the priors on the train window, rebuild,
    # re-split. Reproducing the shipped 0.6910 depends on it.
    probe_rows = build_first_inning_ledger(args.snapshots)
    probe_train, _probe_val, _probe_test = _chronological_split(probe_rows)
    train_end = probe_train[-1].game_start_utc
    priors = compute_first_inning_priors(args.snapshots, end_utc=parse_utc(train_end))

    base_rows = build_first_inning_ledger(args.snapshots, priors=priors)
    ump_rows = build_first_inning_ledger(args.snapshots, priors=priors, include_umpires=True)

    train, val, test = _chronological_split(base_rows)
    test_ids = [r.game_start_utc for r in test]
    ump_by_id = {r.game_start_utc: r for r in ump_rows}
    ump_test = [ump_by_id[i] for i in test_ids]

    outcomes = [r.nrfi for r in test]
    dates = [r.game_start_utc[:10] for r in test]

    # Reproduction gate: incumbent exact configuration on base features.
    inc = MLBFirstInningModel().fit(train)
    inc_preds = [inc.predict_p_nrfi(r) for r in test]
    inc_logloss = _logloss(inc_preds, outcomes)
    drift = abs(inc_logloss - SHIPPED_HOLDOUT_LOGLOSS)

    half = MLBHalfInningModel().fit(train)
    half_preds = [half.predict_p_nrfi(r) for r in test]

    ump_train = [ump_by_id[r.game_start_utc] for r in train]
    inc_u = MLBFirstInningModel(feature_names=list(FEATURE_NAMES) + list(UMPIRE_FEATURE_NAMES))
    inc_u.fit(ump_train)
    inc_u_preds = [inc_u.predict_p_nrfi(r) for r in ump_test]

    half_u = MLBHalfInningModel(feature_names=list(FEATURE_NAMES) + list(UMPIRE_FEATURE_NAMES))
    half_u.fit(ump_train)
    half_u_preds = [half_u.predict_p_nrfi(r) for r in ump_test]

    report = {
        "n_snapshots": len(base_rows),
        "split": {"train": len(train), "validation": len(val), "test": len(test)},
        "reproduction_gate": {
            "holdout_logloss": round(inc_logloss, 6),
            "shipped": SHIPPED_HOLDOUT_LOGLOSS,
            "drift": round(drift, 6),
            "gate": "PASS" if drift <= REPRODUCTION_TOLERANCE else "DRIFT — comparison void",
        },
        "variants": [
            _report_variant("incumbent", inc_preds, test),
            _report_variant("half_inning", half_preds, test),
            _report_variant("incumbent_umpires", inc_u_preds, ump_test),
            _report_variant("half_inning_umpires", half_u_preds, ump_test),
        ],
        "logloss_delta_vs_incumbent": {
            "half_inning": _paired_logloss_delta_ci(inc_preds, half_preds, outcomes, dates),
            "incumbent_umpires": _paired_logloss_delta_ci(inc_preds, inc_u_preds, outcomes, dates),
            "half_inning_umpires": _paired_logloss_delta_ci(inc_preds, half_u_preds, outcomes, dates),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
