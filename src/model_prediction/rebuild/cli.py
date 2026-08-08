"""Command-line entrypoint for the isolated, shadow-only rebuild."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from time import perf_counter
from typing import Any

from .config import default_repo_root, load_rebuild_config
from .safety import RebuildPathPolicy, assert_runtime_data_root, assert_shadow_only
from .shadow_ledger import ShadowLedger
from .sport_adapter import SUPPORTED_SPORTS, build_adapter

STAGES = ("collect", "build_features", "predict", "match_markets", "decide")
RESUMABLE_STAGES = frozenset({"collect", "build_features"})
FORBIDDEN_LIVE_FLAGS = frozenset({"--execute", "--live", "--real-order", "--promote"})


def _honest_row_count(stage: str, detail: dict[str, Any]) -> int | None:
    keys = {
        "build_features": ("real_scoreboard_rows", "rows_built"),
        "predict": ("games_predicted",),
        "match_markets": ("real_market_rows",),
    }.get(stage, ())
    for key in keys:
        value = detail.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    if stage == "build_features" and isinstance(detail.get("coverage"), dict):
        value = detail["coverage"].get("rows_built")
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if stage == "decide" and isinstance(detail.get("games"), list):
        return len(detail["games"])
    return None


def run(
    sport: str,
    date: str,
    horizon: str,
    data_root: str,
    only_stage: str | None,
    resume_run_id: str | None = None,
    *,
    challenger_root: str | None = None,
) -> dict[str, Any]:
    """Run one isolated shadow lineage.

    ``data_root`` remains injectable for unit tests. The installed CLI never
    accepts it from argv; it supplies the path validated from rebuild.yaml.
    """
    assert_shadow_only()
    isolated_data_root = assert_runtime_data_root(data_root, default_repo_root())
    ledger = ShadowLedger(str(isolated_data_root / "shadow.db"))
    run_started = perf_counter()
    run_mode = "resumed" if resume_run_id else ("partial" if only_stage else "fresh")
    run_id: str | None = None
    try:
        run_id = ledger.record_run(
            sport,
            run_type="rebuild-shadow-cli",
            horizon=horizon,
            params={"date": date, "only_stage": only_stage, "shadow_only": True},
            run_id=resume_run_id,
            mode=run_mode,
        )
        adapter = build_adapter(sport, str(isolated_data_root), challenger_root=challenger_root)
        already_completed = ledger.get_completed_stages(run_id) if resume_run_id else {}
        stages_to_run = [only_stage] if only_stage else list(STAGES)
        report: dict[str, Any] = {
            "run_id": run_id,
            "sport": sport,
            "date": date,
            "horizon": horizon,
            "shadow_only": True,
            "execution_enabled": False,
            "stages": {},
        }
        for stage in stages_to_run:
            if stage is None:
                continue
            if stage in RESUMABLE_STAGES and already_completed.get(stage) == "SUCCESS":
                prior = ledger.get_stage_result(run_id, stage) or {}
                detail = json.loads(prior["detail_json"]) if prior.get("detail_json") else {}
                row_count = prior.get("row_count")
                ledger.record_stage_result(
                    run_id, stage, "SUCCESS", detail, artifact_hash=prior.get("artifact_hash"),
                    duration_seconds=0.0, mode="resumed", row_count=row_count,
                )
                report["stages"][stage] = {
                    "status": "SKIPPED_ALREADY_COMPLETE", "detail": detail,
                    "duration_seconds": 0.0, "error": None, "mode": "resumed", "rows": row_count,
                }
                continue
            method = getattr(adapter, stage)
            stage_started = perf_counter()
            stage_mode = "resumed" if resume_run_id else ("partial" if only_stage else "fresh")
            try:
                result = method(date, run_id=run_id) if stage == "collect" else method(
                    date, horizon, run_id=run_id
                )
            except Exception as exc:
                duration = perf_counter() - stage_started
                exception_error = str(exc)[:500]
                ledger.record_stage_result(
                    run_id, stage, "ERROR", {"error": exception_error}, duration_seconds=duration,
                    error=exception_error, mode=stage_mode,
                )
                ledger.finish_run(
                    run_id, "ERROR", duration_seconds=perf_counter() - run_started,
                    error=exception_error, mode=run_mode,
                )
                raise
            duration = perf_counter() - stage_started
            stage_error: str | None = str(result.detail.get("error") or result.detail.get("reason"))[:500] \
                if result.status == "ERROR" else None
            row_count = _honest_row_count(stage, result.detail)
            report["stages"][stage] = {
                "status": result.status, "detail": result.detail, "duration_seconds": duration,
                "error": stage_error, "mode": stage_mode, "rows": row_count,
            }
            ledger.record_stage_result(
                run_id, stage, result.status, result.detail, duration_seconds=duration,
                error=stage_error, mode=stage_mode, row_count=row_count,
            )
            if result.status != "SUCCESS" and only_stage is None:
                break
        terminal_status = next(reversed(report["stages"].values()))["status"] if report["stages"] else "SUCCESS"
        if terminal_status == "SKIPPED_ALREADY_COMPLETE":
            terminal_status = "SUCCESS"
        terminal_error = next(
            (item["error"] for item in reversed(list(report["stages"].values())) if item["error"]), None
        )
        ledger.finish_run(
            run_id, terminal_status, duration_seconds=perf_counter() - run_started,
            error=terminal_error, mode=run_mode,
        )
        return report
    except Exception as exc:
        if run_id is not None:
            row = ledger.get_run(run_id)
            if row is not None and row.get("finished_at") is None:
                ledger.finish_run(
                    run_id, "ERROR", duration_seconds=perf_counter() - run_started,
                    error=str(exc)[:500], mode=run_mode,
                )
        raise
    finally:
        ledger.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rebuild-shadow",
        description="Run the isolated clean-slate rebuild (SHADOW ONLY; NO LIVE EXECUTION)",
    )
    parser.add_argument("--sport", required=True, choices=SUPPORTED_SPORTS)
    parser.add_argument("--date", required=True)
    parser.add_argument("--horizon", default="late", choices=("early", "mid", "late"))
    stages = parser.add_mutually_exclusive_group()
    stages.add_argument("--collect-only", action="store_true")
    stages.add_argument("--features-only", action="store_true")
    stages.add_argument("--predict-only", action="store_true")
    stages.add_argument("--markets-only", action="store_true")
    stages.add_argument("--decision-only", action="store_true")
    parser.add_argument("--resume-run-id")
    return parser


def _reject_live_flags(parser: argparse.ArgumentParser, argv: Sequence[str]) -> None:
    for raw in argv:
        flag = raw.split("=", 1)[0]
        if flag in FORBIDDEN_LIVE_FLAGS:
            parser.error(f"{flag} is forbidden: rebuild execution is permanently shadow-only")


def main(argv: Sequence[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    _reject_live_flags(parser, raw_argv)
    args = parser.parse_args(raw_argv)

    config = load_rebuild_config()
    assert_shadow_only(config)
    policy = RebuildPathPolicy.from_config(config)
    policy.assert_runtime_write(config.paths.data_root / "shadow.db")
    sport_config = config.sports.get(args.sport)
    if sport_config is None or not sport_config.enabled:
        parser.error(f"{args.sport} is disabled in config/rebuild.yaml")

    stage_flags = {
        "collect": args.collect_only,
        "build_features": args.features_only,
        "predict": args.predict_only,
        "match_markets": args.markets_only,
        "decide": args.decision_only,
    }
    only_stage = next((stage for stage, selected in stage_flags.items() if selected), None)
    report = run(
        args.sport,
        args.date,
        args.horizon,
        str(config.paths.data_root),
        only_stage,
        args.resume_run_id,
        challenger_root=str(config.paths.challenger_root),
    )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
