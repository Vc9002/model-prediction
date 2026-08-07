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

# Task 5 (true resume system): only these two stages are honestly
# skippable on resume. Both are disk-backed and idempotent (RawStore/
# NormalizedStore for collect, FeatureStore for build_features) -- a
# resumed invocation re-reads their real prior output straight from disk
# regardless of whether this call actually re-executes them, so skipping
# a completed one is a genuine time/network-call saving, not a shortcut
# that changes behavior. predict/match_markets/decide are NOT in this set
# on purpose: MLBAdapter (sport_adapter.py) holds their real output in
# `self._state`, populated only by predict() running earlier in THIS
# process -- a fresh process resuming after a crash has no such state, so
# skipping any of these three would make match_markets/decide fail closed
# against a state that was never populated this run, not silently succeed
# against stale data. They correctly always re-run; real cross-process
# resume for them requires real fitted-model artifact reload (see
# mlb_shadow_pipeline.py's train_through() docstring for that disclosed,
# separate gap), not orchestration logic.
RESUMABLE_STAGES = frozenset({"collect", "build_features"})


def run(
    sport: str, date: str, horizon: str, data_root: str, only_stage: str | None,
    resume_run_id: str | None = None,
) -> dict:
    adapter = build_adapter(sport, data_root)
    ledger = ShadowLedger(f"{data_root}/shadow.db")
    # record_run() is itself idempotent on an explicit run_id (real,
    # tested behavior -- see shadow_ledger.py): resuming an existing run
    # and starting a brand-new one with a caller-chosen ID both just work,
    # no separate "does this run exist" check needed. This is the real
    # gap CLAUDE.md's own orchestration spec called out
    # ("--resume-run-id") -- previously every invocation always minted a
    # fresh run_id, so a stage-only rerun (e.g. --decision-only after a
    # --predict-only that already ran) could never continue one real
    # lineage chain.
    run_id = ledger.record_run(sport, run_type="rebuild-shadow-cli", horizon=horizon,
                                params={"date": date, "only_stage": only_stage}, run_id=resume_run_id)

    # Real per-stage completion check, not just a fresh run_id -- see
    # RESUMABLE_STAGES above for exactly which stages this can honestly
    # skip and why. Only consulted when the caller actually asked to
    # resume; a fresh run_id always has an empty completed-stages map
    # anyway (record_stage_result() is scoped per run_id), so this is a
    # no-op distinction, kept explicit for clarity.
    already_completed = ledger.get_completed_stages(run_id) if resume_run_id else {}

    stages_to_run = [only_stage] if only_stage else list(STAGES)
    report: dict = {"run_id": run_id, "sport": sport, "date": date, "horizon": horizon, "stages": {}}

    for stage in stages_to_run:
        if stage in RESUMABLE_STAGES and already_completed.get(stage) == "SUCCESS":
            report["stages"][stage] = {"status": "SKIPPED_ALREADY_COMPLETE", "detail": {}}
            continue
        method = getattr(adapter, stage)
        result = method(date, run_id=run_id) if stage == "collect" else method(date, horizon, run_id=run_id)
        report["stages"][stage] = {"status": result.status, "detail": result.detail}
        ledger.record_stage_result(run_id, stage, result.status, result.detail)
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
    parser.add_argument(
        "--resume-run-id", default=None,
        help="Reuse this run_id instead of minting a new one (record_run() is idempotent on it), and "
             "genuinely skip stages already recorded SUCCESS for it in run_stages -- but only collect "
             "and build_features (see RESUMABLE_STAGES): both are disk-backed and idempotent, so a "
             "fresh process re-reads their real prior output regardless of whether this call reruns "
             "them. predict/match_markets/decide always re-run, on purpose: they need MLBAdapter.state "
             "from an earlier stage in the SAME process, which a resumed process never has -- "
             "--decision-only --resume-run-id in a fresh process still correctly fails closed with "
             "'predict() must run first', it does not silently skip that requirement.",
    )
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

    report = run(args.sport, args.date, args.horizon, args.data_root, only_stage, args.resume_run_id)
    print(json.dumps(report, indent=2, default=str))
    print("\nNo real order adapter was imported or called by this script.")


if __name__ == "__main__":
    main()
