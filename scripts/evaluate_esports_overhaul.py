"""Run isolated read-only esports audit and chronological diagnostic research."""

import argparse
from pathlib import Path

from model_prediction.esports_overhaul_research import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--ledger-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--incumbent-source", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.data_root, args.ledger_db, args.output, args.incumbent_source, args.source_repo)
    print("Reproduction gate:", report["reproduction_gate"])
    print("Production ranking:", report["production_ranking"])
    print("Report:", args.output / "report.json")
