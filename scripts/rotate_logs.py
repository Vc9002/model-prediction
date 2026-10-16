#!/usr/bin/env python3
"""Run automated log rotation, retention cleanup, and SQLite WAL checkpointing."""

import argparse
import json
import sys
from pathlib import Path

# Ensure package is on sys.path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from model_prediction.log_maintenance import run_maintenance
from model_prediction.runtime_paths import RuntimePaths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=14,
        help="maximum age in days for supervisor and daily logs (default: 14)",
    )
    args = parser.parse_args()

    paths = RuntimePaths.resolve(repo_root=repo_root, require_external_runtime=False)
    report = run_maintenance(repo_root=repo_root, paths=paths, max_age_days=args.max_age_days)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
