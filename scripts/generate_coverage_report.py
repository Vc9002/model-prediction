"""Real coverage/missingness report generation (FOUNDATION_COMPLETION.md
Phase 7 / item 7 of the foundation sequence).

Writes, per FOUNDATION_COMPLETION.md's own required paths:
    outputs/rebuild/coverage/{sport}_{horizon}.json
    outputs/rebuild/missingness/{sport}_{horizon}.json

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/generate_coverage_report.py \
        --sport mlb --date 2026-08-06
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.horizon_builder import build_mlb_horizon_dataset
from model_prediction.rebuild.horizons import HORIZONS


def generate_mlb_coverage(data_root: str, date: str, out_root: str) -> dict:
    probables_path = Path("data/point_in_time/mlb_probable_starters.jsonl")
    records = (
        [json.loads(line) for line in probables_path.read_text().splitlines() if line.strip()]
        if probables_path.exists() else []
    )

    coverage_dir = Path(out_root) / "coverage"
    missingness_dir = Path(out_root) / "missingness"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    missingness_dir.mkdir(parents=True, exist_ok=True)

    summary = {}
    for horizon in HORIZONS:
        result = build_mlb_horizon_dataset(data_root, date, horizon, records)
        coverage_payload = {
            "sport": "mlb", "horizon": horizon, "date": date,
            "generated_from_code": True, **result.coverage,
        }
        missingness_payload = {
            "sport": "mlb", "horizon": horizon, "date": date,
            "generated_from_code": True, **result.missingness,
        }
        (coverage_dir / f"mlb_{horizon}.json").write_text(json.dumps(coverage_payload, indent=2))
        (missingness_dir / f"mlb_{horizon}.json").write_text(json.dumps(missingness_payload, indent=2))
        summary[horizon] = coverage_payload
        print(f"{horizon}: {result.coverage}")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=["mlb"])
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", default="data/rebuild")
    parser.add_argument("--out-root", default="outputs/rebuild")
    args = parser.parse_args()

    if args.sport == "mlb":
        generate_mlb_coverage(args.data_root, args.date, args.out_root)


if __name__ == "__main__":
    main()
