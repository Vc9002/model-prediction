"""Supervisor worker ``nba-pretip-capture``: ESPN odds and Polymarket quotes for NBA games about to tip.

Scheduled every 10 minutes from 11:00 to 02:00 Eastern (``scripts/register_windows_tasks.ps1``), so
every game is captured about 20 and 10 minutes before tip. It deliberately does not take the daily
writer lock: daily can hold it for most of an hour and the capture would miss its T-10 slot. It only
appends to capture files and never touches a ledger.

Exit code: 0 for a run that captured, had nothing in the window, or failed on some games (the next
run is 10 minutes away); 1 only when no ESPN scoreboard could be read or the runtime root is missing,
so ``system_health`` reports a capture that is fully down.

NBA v10 forward research (``nba_v10_forward``, ``outputs/nba/v10_forward/PREREG_FORWARD.md``):
after the capture has returned and its exit code is fixed, the official injury report capture,
the player-model shadow log and the T-C2 event log run in a daemon thread with a time budget
measured from process start, so they can neither change the capture nor push the worker past its
supervisor timeout. Their summary is printed under ``nba_v10_forward``; ``--no-forward`` turns
them off.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .data_sources.nba_pretip_capture import (
    DEFAULT_ESPN_WINDOW_MINUTES,
    DEFAULT_POLYMARKET_MAX_SECONDS,
    DEFAULT_WINDOW_MINUTES,
    capture_nba_pretip,
)
from .runtime_paths import RuntimePaths

_logger = logging.getLogger(__name__)

# The supervisor kills this worker at 300 s (run_supervisor.DEFAULT_WORKER_TIMEOUTS). The research
# extras must be done by FORWARD_HARD_STOP after process start, with at most FORWARD_BUDGET of
# their own, and they are skipped when less than FORWARD_MIN_BUDGET is left.
FORWARD_HARD_STOP_SECONDS = 240.0
FORWARD_BUDGET_SECONDS = 90.0
FORWARD_MIN_BUDGET_SECONDS = 15.0
# The thread is joined this long after the deadline the steps check, so a step that respects the
# deadline returns its own summary instead of a timeout marker.
FORWARD_JOIN_GRACE_SECONDS = 5.0
_PROCESS_START = time.monotonic()
_ET = ZoneInfo("America/New_York")
# The host runs low on virtual memory around the 00:30 ET daily run (MemoryError, SQLite disk I/O
# errors, 0xC0000005); a cold player-state rebuild loads every box score, so research stays off then.
_MEMORY_WINDOW_ET = (dtime(0, 15), dtime(1, 20))


def _in_memory_pressure_window(now_et: datetime) -> bool:
    start, end = _MEMORY_WINDOW_ET
    return start <= now_et.time() < end


def _open_nba_pick_slugs(data_root) -> frozenset[str]:
    """Open NBA pick slugs, so their rungs always get a sized /book quote. Fail-soft."""
    try:
        from .cli.commands import _open_pick_slugs

        return _open_pick_slugs(data_root, "nba")
    except Exception:  # losing the safeguard must not stop the capture
        _logger.warning("open NBA pick slug lookup failed", exc_info=True)
        return frozenset()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nba-pretip-capture", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--window-minutes",
        type=float,
        default=DEFAULT_WINDOW_MINUTES,
        help="capture games tipping within this many minutes (default %(default)s)",
    )
    parser.add_argument(
        "--espn-window-minutes",
        type=float,
        default=DEFAULT_ESPN_WINDOW_MINUTES,
        help="also write ESPN odds for games tipping within this many minutes (default %(default)s)",
    )
    parser.add_argument(
        "--polymarket-max-seconds",
        type=float,
        default=DEFAULT_POLYMARKET_MAX_SECONDS,
        help="time budget for the Polymarket quote capture (default %(default)s)",
    )
    parser.add_argument("--no-forward", action="store_true", help="skip the NBA v10 forward research steps")
    parser.add_argument(
        "--forward-scratch",
        type=Path,
        default=None,
        help="write every forward research output under this directory instead of the runtime root",
    )
    parser.add_argument(
        "--forward-repo-data",
        type=Path,
        default=None,
        help="read schedule and box captures from this data directory (default: <repo root>/data)",
    )
    parser.add_argument("--forward-budget-seconds", type=float, default=FORWARD_BUDGET_SECONDS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paths = RuntimePaths.resolve(require_external_runtime=True)
    result = capture_nba_pretip(
        paths.data_root,
        window_minutes=args.window_minutes,
        espn_window_minutes=args.espn_window_minutes,
        keep_slugs=_open_nba_pick_slugs(paths.data_root),
        polymarket_max_seconds=args.polymarket_max_seconds,
    )
    # Fixed from the capture alone, before any research step runs.
    exit_code = 1 if result.get("status") == "error" else 0
    timed_out = False
    if not args.no_forward:
        forward = run_forward_extras(paths, args)
        timed_out = forward.get("status") == "timeout"
        result = {**result, "nba_v10_forward": forward}
    print(json.dumps(result, default=str, sort_keys=True), flush=True)
    if timed_out:
        # The research thread is still running; leave without waiting for it (a daemon thread
        # can still hold interpreter shutdown up), keeping the capture's exit code.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)
    return exit_code


def run_forward_extras(paths: RuntimePaths, args: argparse.Namespace) -> dict[str, Any]:
    """The NBA v10 forward research steps, isolated and budgeted. Never raises."""
    try:
        if _in_memory_pressure_window(datetime.now(_ET)):
            return {"status": "skipped_memory_window"}
        from .nba_v10_forward import ForwardConfig, run_forward, run_isolated

        elapsed = time.monotonic() - _PROCESS_START
        budget = min(float(args.forward_budget_seconds), FORWARD_HARD_STOP_SECONDS - elapsed)
        if budget < FORWARD_MIN_BUDGET_SECONDS:
            return {"status": "skipped_budget", "process_elapsed_seconds": round(elapsed, 1)}
        cfg = ForwardConfig.default(paths.runtime_root, paths.repo_root, paths.research_root)
        if args.forward_repo_data is not None:
            cfg = ForwardConfig(
                runtime_root=cfg.runtime_root,
                repo_data=Path(args.forward_repo_data),
                repo_root=cfg.repo_root,
                out_root=cfg.out_root,
                injury_root=cfg.injury_root,
            )
        if args.forward_scratch is not None:
            cfg = cfg.with_scratch(Path(args.forward_scratch))
        deadline = time.monotonic() + budget
        out = run_isolated(lambda: run_forward(cfg, deadline=deadline), budget + FORWARD_JOIN_GRACE_SECONDS)
        out["budget_seconds"] = round(budget, 1)
        out["process_elapsed_at_start_seconds"] = round(elapsed, 1)
        return out
    except Exception as exc:  # research must never change the capture's outcome
        _logger.warning("NBA v10 forward extras failed to start", exc_info=True)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:200]}


if __name__ == "__main__":
    raise SystemExit(main())
