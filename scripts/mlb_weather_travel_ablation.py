"""Ablation: real MLB weather + travel context vs the incumbent constants.

Harness follows the ablation-reproduction-gate discipline:

1. The incumbent configuration (weather=1.0, travel=0.0 constants) runs as
   a named CONTROL variant inside the same harness, on the same rows, as
   the candidate -- never a remembered number.
2. The control is checked against the incumbent's pre-wiring shipped
   numbers (snapshotted 2026-08-27 before any wiring: train_rows=4697,
   test_rows=2013, MAE 3.584296, RMSE 4.487904) and the run ABORTS loudly
   if it cannot reproduce them.
3. Per-row parity: the control's weather/travel columns are asserted to be
   the exact incumbent constants on every row (the changed columns' stored
   vs reproduced comparison; the other columns are shared code).
4. The verdict consumes the paired bootstrap CI of the per-row MAE gain:
   the candidate must clear a minimum-effect threshold (0.005 MAE) on the
   CI's LOWER bound, not merely on the point estimate.

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/mlb_weather_travel_ablation.py \
        [--out outputs/latest/mlb-weather-travel-ablation.json]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.features.base import FeatureStore
from model_prediction.total_score import build_total_score_rows

# Incumbent numbers snapshotted 2026-08-27 BEFORE the weather/travel wiring
# (validate_total_score_model, mlb, FeatureStore('data'), 70/30 split):
SHIPPED = {
    "train_rows": 4697,
    "test_rows": 2013,
    "mae": 3.584296,
    "rmse": 4.487904,
    "mean_error": -0.017693,
}
REPRO_TOLERANCE = 1e-9  # same code path + same seed => deterministic equality
MIN_EFFECT_MAE_GAIN = 0.005  # candidate must beat control by at least this on the CI lower bound


def _split(rows):
    split = int(len(rows) * 0.7)
    return rows[:split], rows[split:]


def _fit_predict(train_rows, test_rows):
    import numpy as np
    from sklearn.linear_model import Ridge

    x_train = [list(r.features) for r in train_rows]
    y_train = [r.actual_total for r in train_rows]
    x_test = [list(r.features) for r in test_rows]
    model = Ridge(alpha=1.0, random_state=42)
    model.fit(np.asarray(x_train), y_train)
    return model.predict(np.asarray(x_test)).tolist(), model


def _mae(preds, rows):
    return sum(abs(p - r.actual_total) for p, r in zip(preds, rows, strict=True)) / len(rows)


def _rmse(preds, rows):
    return math.sqrt(sum((p - r.actual_total) ** 2 for p, r in zip(preds, rows, strict=True)) / len(rows))


def _paired_mae_gain_ci(control_preds, candidate_preds, rows, samples=2000, seed=20260827):
    gains = [
        abs(c - r.actual_total) - abs(p - r.actual_total)
        for c, p, r in zip(control_preds, candidate_preds, rows, strict=True)
    ]
    gen = random.Random(seed)
    boot = sorted(_mean_sample(gains, gen) for _ in range(samples))
    return {
        "point_estimate": round(sum(gains) / len(gains), 6),
        "ci_95_low": round(boot[int(samples * 0.025)], 6),
        "ci_95_high": round(boot[int(samples * 0.975)], 6),
        "resamples": samples,
    }


def _mean_sample(values, gen):
    return sum(values[gen.randrange(len(values))] for _ in values) / len(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="outputs/latest/mlb-weather-travel-ablation.json")
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()

    store = FeatureStore(args.data_root)
    games = store.load_games("mlb")

    # Named control variant: the incumbent itself, same harness, same rows.
    control_rows = build_total_score_rows(games)
    candidate_rows = build_total_score_rows(games, real_mlb_context=True)

    # Rule 4 -- per-row parity on the changed columns: the control must be
    # the exact incumbent constants on every row.
    parity_violations = [
        i for i, r in enumerate(control_rows) if r.features[6] != 1.0 or r.features[8] != 0.0
    ]
    non_constant_weather = sum(1 for r in candidate_rows if r.features[6] != 1.0)
    non_constant_travel = sum(1 for r in candidate_rows if r.features[8] != 0.0)

    control_train, control_test = _split(control_rows)
    candidate_train, candidate_test = _split(candidate_rows)

    # Rule 2 -- reproduction check inside the harness, before any candidate
    # numbers are trusted. Same split indices as the incumbent harness.
    repro = {
        "train_rows": len(control_train),
        "test_rows": len(control_test),
        "shipped": SHIPPED,
    }
    if len(control_train) != SHIPPED["train_rows"] or len(control_test) != SHIPPED["test_rows"]:
        print("REPRODUCTION FAILURE: row counts diverged from the shipped snapshot.")
        print(json.dumps(repro, indent=2))
        return 2

    control_preds, _control_model = _fit_predict(control_train, control_test)
    control_metrics = {
        "mae": round(_mae(control_preds, control_test), 6),
        "rmse": round(_rmse(control_preds, control_test), 6),
    }
    repro["reproduced"] = control_metrics
    for key, shipped_value in (("mae", SHIPPED["mae"]), ("rmse", SHIPPED["rmse"])):
        if abs(control_metrics[key] - shipped_value) > REPRO_TOLERANCE:
            print(f"REPRODUCTION FAILURE: {key} diverged from shipped snapshot.")
            print(json.dumps(repro, indent=2))
            return 2
    if parity_violations:
        print(
            f"REPRODUCTION FAILURE: {len(parity_violations)} control rows with non-constant weather/travel."
        )
        return 2

    candidate_preds, _candidate_model = _fit_predict(candidate_train, candidate_test)
    candidate_metrics = {
        "mae": round(_mae(candidate_preds, candidate_test), 6),
        "rmse": round(_rmse(candidate_preds, candidate_test), 6),
    }
    gain_ci = _paired_mae_gain_ci(control_preds, candidate_preds, candidate_test)

    # Rule 5 -- the verdict consumes the CI, not the point estimate, and
    # applies an explicit minimum-effect threshold.
    verdict = "PROMOTE" if gain_ci["ci_95_low"] > MIN_EFFECT_MAE_GAIN else "NO_PROMOTION"

    report = {
        "schema_version": "1",
        "harness": "scripts/mlb_weather_travel_ablation.py",
        "generated_utc": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        "reproduction_gate": {
            "passed": True,
            "parity_violations": len(parity_violations),
            **repro,
        },
        "candidate_coverage": {
            "rows_with_real_weather": non_constant_weather,
            "rows_with_real_travel": non_constant_travel,
            "total_rows": len(candidate_rows),
        },
        "variants": {
            "control_incumbent_constants": control_metrics,
            "candidate_real_weather_travel": candidate_metrics,
        },
        "mae_gain_95ci": gain_ci,
        "minimum_effect_threshold": MIN_EFFECT_MAE_GAIN,
        "verdict": verdict,
        "note": (
            "Candidate weather is REALIZED archive weather (Open-Meteo), the "
            "same approximation features.weather.historical_weather already "
            "documents: forecast≈actual, not literally knowable at first "
            "pitch. Training-side research only; promotion to any live model "
            "is a separate governance decision."
        ),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nreport written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
