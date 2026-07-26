from __future__ import annotations

import argparse
import json

from model_prediction.config import PROJECT_ROOT, ledger_path, load_config
from model_prediction.research_cleanup import apply_cleanup_plan, build_cleanup_plan


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit, clean, and split legacy mixed research ledgers by sport."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write per-sport ledgers and archive the two legacy mixed workbooks",
    )
    args = parser.parse_args()
    config = load_config()
    data_root = ledger_path(config).parent
    plan = build_cleanup_plan(
        data_root,
        PROJECT_ROOT / "config" / "models",
        config,
    )
    output = plan.report()
    if args.apply:
        output = apply_cleanup_plan(data_root, plan)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
