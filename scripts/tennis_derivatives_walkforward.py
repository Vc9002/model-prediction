#!/usr/bin/env python3
"""Walk-forward research validation for tennis spread/total derivatives.

Research-only: opens the ledger READ-ONLY, writes nothing but the JSON
report, and never touches the forward slate (which stays failed-closed).

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/tennis_derivatives_walkforward.py \
        [--data-root PATH] [--ledgers-db PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from model_prediction.tennis_derivatives_research import main

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
DEFAULT_LEDGER_DB = Path.home() / "model-prediction-runtime" / "ledgers" / "ledgers.db"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "tmp" / "tennis_derivatives_walkforward.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--ledgers-db", type=Path, default=DEFAULT_LEDGER_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args(argv)


def main_cli(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.ledgers_db.exists():
        print(f"ledgers db not found: {args.ledgers_db}", file=sys.stderr)
        return 2
    report = main(data_root=args.data_root, ledgers_db=args.ledgers_db, out_path=args.out)
    print(f"wrote {args.out}")
    sample = report["sample"]
    metrics = report["metrics"]
    print(
        f"contracts: {sample['valid_contracts']} valid "
        f"(spread {sample['valid_spread']}, total {sample['valid_total']}) "
        f"over {sample['unique_events']} events; {sample['excluded_contracts']} excluded"
    )
    print(f"spread brier {metrics['spread']['brier']:.4f} vs naive 0.25")
    print(f"total  brier {metrics['total']['brier']:.4f} vs naive 0.25")
    print(f"expected-total MAE {metrics['expected_total_games']['mae']:.2f} games")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
