"""Builds the machine-readable counterpart of model_benchmark.md's MLB
section -- CLAUDE.md's Part 2 deliverable `outputs/rebuild/model_benchmark.parquet`
(Task 17).

Reads only already-committed real result JSONs produced by this session's
comparison scripts (train_mlb_distribution_comparison.py,
train_mlb_score_model_comparison.py, train_mlb_calibration_comparison.py,
train_mlb_calibrated_ensemble_comparison.py) -- does not retrain or
re-simulate anything, and does not touch test_consumption_registry.json.
One real row per (model, market_type, line/method) combination actually
reported in this session's real comparisons.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/build_mlb_benchmark_parquet.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

OUT_DIR = Path("outputs/rebuild")
STATUS = "RESEARCH_ONLY"


def _load(name: str) -> dict:
    path = OUT_DIR / name
    if not path.exists():
        print(f"ERROR: {path} not found. Run the Task 12-16 comparison scripts first.")
        sys.exit(1)
    return json.loads(path.read_text())


def main() -> None:
    dist = _load("mlb_distribution_comparison.json")
    score = _load("mlb_score_model_comparison.json")
    calib = _load("mlb_calibration_comparison.json")
    ens = _load("mlb_calibrated_ensemble_comparison.json")

    hashes = {dist["dataset_hash"], score["dataset_hash"], calib["dataset_hash"], ens["dataset_hash"]}
    if len(hashes) != 1:
        print(f"ERROR: dataset_hash mismatch across source files: {hashes}. Stopping honestly.")
        sys.exit(1)
    dataset_hash = hashes.pop()

    rows: list[dict] = []

    # Score-distribution family comparison (Task 12): moneyline only, no line.
    for method, m in dist["oof_summary"].items():
        rows.append(
            {
                "sport": "mlb",
                "model": method,
                "market_type": "moneyline",
                "selection": None,
                "line": None,
                "calibrated": False,
                "calibration_method": None,
                "n": dist["n_oof"],
                "log_loss": m["log_loss"],
                "brier": m["brier"],
                "ece": m["ece"],
                "status": STATUS,
                "dataset_hash": dataset_hash,
                "source_file": "mlb_distribution_comparison.json",
            }
        )

    # Raw (uncalibrated) moneyline OOF comparison across model families (Task 13).
    for model, m in score["moneyline_oof_summary"].items():
        rows.append(
            {
                "sport": "mlb",
                "model": model,
                "market_type": "moneyline",
                "selection": None,
                "line": None,
                "calibrated": False,
                "calibration_method": None,
                "n": score["n_moneyline_oof"],
                "log_loss": m["log_loss"],
                "brier": m["brier"],
                "ece": None,
                "status": STATUS,
                "dataset_hash": dataset_hash,
                "source_file": "mlb_score_model_comparison.json",
            }
        )

    # Totals, predeclared grid (Task 13.5): two_head + xgb_two_head only.
    for model, by_line in score["totals_oof_summary"].items():
        for line_str, entry in by_line.items():
            rows.append(
                {
                    "sport": "mlb",
                    "model": model,
                    "market_type": "total",
                    "selection": "over",
                    "line": float(line_str),
                    "calibrated": False,
                    "calibration_method": None,
                    "n": entry["n"],
                    "log_loss": entry["log_loss"],
                    "brier": entry["brier"],
                    "ece": None,
                    "status": STATUS,
                    "dataset_hash": dataset_hash,
                    "source_file": "mlb_score_model_comparison.json",
                }
            )

    # Spread, predeclared signed home-line grid (Task 17): two_head + xgb_two_head only.
    for model, by_line in score.get("spread_oof_summary", {}).items():
        for line_str, entry in by_line.items():
            rows.append(
                {
                    "sport": "mlb",
                    "model": model,
                    "market_type": "spread",
                    "selection": "home",
                    "line": float(line_str),
                    "calibrated": False,
                    "calibration_method": None,
                    "n": entry["n"],
                    "log_loss": entry["log_loss"],
                    "brier": entry["brier"],
                    "ece": None,
                    "status": STATUS,
                    "dataset_hash": dataset_hash,
                    "source_file": "mlb_score_model_comparison.json",
                }
            )

    # Cross-fitted calibration comparison (Task 14): every method per model,
    # not just the winner -- so a full method-vs-method table survives here.
    for model, model_result in calib["models"].items():
        for method, m in model_result["cross_fit_results"].items():
            rows.append(
                {
                    "sport": "mlb",
                    "model": model,
                    "market_type": "moneyline",
                    "selection": None,
                    "line": None,
                    "calibrated": True,
                    "calibration_method": method,
                    "n": m["n_eval_total"],
                    "log_loss": m["log_loss"],
                    "brier": m["brier"],
                    "ece": m["ece"],
                    "status": STATUS,
                    "dataset_hash": dataset_hash,
                    "source_file": "mlb_calibration_comparison.json",
                }
            )

    # Meta-cross-fit calibrated ensemble comparison (Task 15): every method,
    # including the two single-model baselines scored the same way.
    for method, m in ens["meta_cross_fit_results"].items():
        rows.append(
            {
                "sport": "mlb",
                "model": method,
                "market_type": "moneyline_ensemble",
                "selection": None,
                "line": None,
                "calibrated": True,
                "calibration_method": "meta_cross_fit",
                "n": m["n_eval_total"],
                "log_loss": m["log_loss"],
                "brier": m["brier"],
                "ece": None,
                "status": STATUS,
                "dataset_hash": dataset_hash,
                "source_file": "mlb_calibrated_ensemble_comparison.json",
            }
        )

    df = pl.DataFrame(rows)
    out_path = OUT_DIR / "model_benchmark.parquet"
    df.write_parquet(out_path)
    print(f"Wrote {df.height} real rows to {out_path}")
    print(
        df.select("model", "market_type", "line", "calibrated", "n", "log_loss").sort("market_type", "model")
    )


if __name__ == "__main__":
    main()
