#!/usr/bin/env python3
"""Compare Measured Edge v1 (already logged) against a v2 candidate on the
SAME real, already-settled flat-ledger picks -- not just fresh calibration
data. For each settled measured-edge-{margin,totals}-v1 pick, recompute what
v2's formula + calibration would have said for the exact same game/selection
/line, using the real known outcome already in the ledger to grade both.

Requires scripts/mlb_elasticity_refit.py's feature cache to already contain
the relevant games (run that script first) and the v2 calibration artifacts
from scripts/mlb_measured_edge_calibrate.py.

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/mlb_measured_edge_compare_settled.py \
        --formula config/models/mlb-analyst-poisson-trend-v0.2.yaml \
        --margin-artifact config/models/measured-edge-margin-v2.json \
        --totals-artifact config/models/measured-edge-totals-v2.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from model_prediction.ledger import PickLedger


def _load_calibrate_module():
    spec = importlib.util.spec_from_file_location(
        "mlb_measured_edge_calibrate", PROJECT_ROOT / "scripts" / "mlb_measured_edge_calibrate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _brier(probabilities: list[float], outcomes: list[float]) -> float:
    return sum((p - o) ** 2 for p, o in zip(probabilities, outcomes, strict=True)) / len(probabilities)


def _log_loss(probabilities: list[float], outcomes: list[float]) -> float:
    import math

    total = 0.0
    for p, o in zip(probabilities, outcomes, strict=True):
        p = min(max(p, 1e-6), 1 - 1e-6)
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(probabilities)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formula", required=True)
    parser.add_argument(
        "--feature-cache", default=str(PROJECT_ROOT / "data/research/mlb_elasticity_feature_cache.jsonl")
    )
    parser.add_argument(
        "--margin-artifact", default=str(PROJECT_ROOT / "config/models/measured-edge-margin-v2.json")
    )
    parser.add_argument(
        "--totals-artifact", default=str(PROJECT_ROOT / "config/models/measured-edge-totals-v2.json")
    )
    parser.add_argument("--flat-ledger", default=str(PROJECT_ROOT / "data/flat/mlb.xlsx"))
    args = parser.parse_args()

    calibrate = _load_calibrate_module()
    spec = calibrate.load_formula_spec_unchecked(args.formula)
    feature_cache = calibrate._load_feature_cache(Path(args.feature_cache))
    margin_artifact = json.loads(Path(args.margin_artifact).read_text())
    totals_artifact = json.loads(Path(args.totals_artifact).read_text())

    ledger = PickLedger(args.flat_ledger)
    rows = ledger.rows()
    settled = [
        row
        for row in rows
        if row.get("status") == "settled"
        and str(row.get("model_version", "")).lower()
        in ("measured-edge-margin-v1", "measured-edge-totals-v1")
        and row.get("result") in ("win", "loss")
    ]
    print(f"{len(settled)} settled measured-edge-v1 picks in {args.flat_ledger}")

    comparisons: list[dict[str, Any]] = []
    missing_features = 0
    for row in settled:
        event_id = str(row["event_id"])
        feature_row = feature_cache.get(event_id)
        if feature_row is None:
            missing_features += 1
            continue
        features = calibrate._features_from_cache_row(feature_row)
        is_margin = "margin" in row["model_version"]
        line = float(row["line"]) if row.get("line") not in (None, "") else None
        if is_margin:
            spread_line = line if row["selection"] == "away" else (-line if line is not None else None)
            probs = calibrate.raw_probabilities(features, spec, spread_line, None)
            raw = probs.get(f"spread_{row['selection']}_probability")
            artifact = margin_artifact
        else:
            probs = calibrate.raw_probabilities(features, spec, None, line)
            raw = probs.get(f"total_{row['selection']}_probability")
            artifact = totals_artifact
        if raw is None:
            continue
        v2_probability = artifact["scale"] * raw + artifact["offset"]
        v2_probability = min(max(v2_probability, 1e-6), 1 - 1e-6)
        outcome = 1.0 if row["result"] == "win" else 0.0
        comparisons.append(
            {
                "event_id": event_id,
                "market": row["model_version"],
                "selection": row["selection"],
                "line": line,
                "away_team": row.get("away_team"),
                "home_team": row.get("home_team"),
                "v1_probability": float(row["model_probability"]),
                "v2_raw_probability": round(raw, 4),
                "v2_calibrated_probability": round(v2_probability, 4),
                "result": row["result"],
                "outcome": outcome,
            }
        )

    print(
        f"{len(comparisons)} picks matched to cached features ({missing_features} missing -- "
        f"run mlb_elasticity_refit.py over a window covering these event dates first)"
    )
    if not comparisons:
        return

    v1_probs = [c["v1_probability"] for c in comparisons]
    v2_probs = [c["v2_calibrated_probability"] for c in comparisons]
    outcomes = [c["outcome"] for c in comparisons]

    print(json.dumps(comparisons, indent=2))
    print("--- summary ---")
    print(f"n = {len(comparisons)}")
    print(f"v1 Brier = {_brier(v1_probs, outcomes):.4f}, v1 log-loss = {_log_loss(v1_probs, outcomes):.4f}")
    print(f"v2 Brier = {_brier(v2_probs, outcomes):.4f}, v2 log-loss = {_log_loss(v2_probs, outcomes):.4f}")
    v1_hits = sum(1 for c in comparisons if (c["v1_probability"] > 0.5) == (c["outcome"] == 1.0))
    v2_hits = sum(1 for c in comparisons if (c["v2_calibrated_probability"] > 0.5) == (c["outcome"] == 1.0))
    print(f"v1 direction-correct: {v1_hits}/{len(comparisons)}")
    print(f"v2 direction-correct: {v2_hits}/{len(comparisons)}")


if __name__ == "__main__":
    main()
