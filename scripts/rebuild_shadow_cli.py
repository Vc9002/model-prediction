"""Shared multi-sport shadow CLI (FOUNDATION_COMPLETION.md Phase 13 / item 6).

    PYTHONPATH=src:. .venv/bin/python scripts/rebuild_shadow_cli.py \
        --sport mlb --date 2026-08-06 --horizon late

Routes through sport_adapter.py's real SportAdapter registry:
collect -> build_features -> predict -> match_markets -> decide, each
stage reporting a real, honest status (SUCCESS / NO_DATA / ERROR /
NOT_IMPLEMENTED) rather than a fabricated one. Persists one `runs` row
per invocation via the real shadow ledger.

No real order adapter is imported or called anywhere in this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.shadow_ledger import ShadowLedger
from model_prediction.rebuild.sport_adapter import SUPPORTED_SPORTS, build_adapter

STAGES = ("collect", "build_features", "predict", "match_markets", "decide")


def run(sport: str, date: str, horizon: str, data_root: str, only_stage: str | None) -> dict:
    adapter = build_adapter(sport, data_root)
    ledger = ShadowLedger(f"{data_root}/shadow.db")
    run_id = ledger.record_run(sport, run_type="rebuild-shadow-cli", horizon=horizon,
                                params={"date": date, "only_stage": only_stage})

    stages_to_run = [only_stage] if only_stage else list(STAGES)
    report: dict = {"run_id": run_id, "sport": sport, "date": date, "horizon": horizon, "stages": {}}

    for stage in stages_to_run:
        method = getattr(adapter, stage)
        result = method(date) if stage == "collect" else method(date, horizon)
        report["stages"][stage] = {"status": result.status, "detail": result.detail}
        # A stage that didn't succeed doesn't invalidate stages that already
        # ran, but there's no real reason to keep running downstream stages
        # against data that stage was supposed to produce -- stop honestly
        # rather than let a later stage silently operate on nothing.
        if result.status not in ("SUCCESS",) and only_stage is None:
            break

    ledger.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Shared multi-sport rebuild-shadow CLI")
    parser.add_argument("--sport", required=True, choices=SUPPORTED_SPORTS)
    parser.add_argument("--date", required=True)
    parser.add_argument("--horizon", default="late", choices=("early", "mid", "late"))
    parser.add_argument("--data-root", default="data/rebuild")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--features-only", action="store_true")
    parser.add_argument("--predict-only", action="store_true")
    parser.add_argument("--markets-only", action="store_true")
    parser.add_argument("--decision-only", action="store_true")
    args = parser.parse_args()

    only_stage = None
    if args.collect_only:
        only_stage = "collect"
    elif args.features_only:
        only_stage = "build_features"
    elif args.predict_only:
        only_stage = "predict"
    elif args.markets_only:
        only_stage = "match_markets"
    elif args.decision_only:
        only_stage = "decide"

    report = run(args.sport, args.date, args.horizon, args.data_root, only_stage)
    print(json.dumps(report, indent=2, default=str))
    print("\nNo real order adapter was imported or called by this script.")


if __name__ == "__main__":
    main()
