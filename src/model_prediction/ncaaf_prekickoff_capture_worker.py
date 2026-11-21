"""Supervisor worker ``ncaaf-prekickoff-capture``: Polymarket closes for NCAAF games about to kick off.

Scheduled every 10 minutes from 09:05 to 01:05 Eastern (``scripts/register_windows_tasks.ps1``), so
every game is quoted two or three times in its last 25 minutes (``ncaaf_prekickoff_capture``). It does
not take the daily writer lock: the Daily can hold it for most of an hour and the capture would miss
its slot. It reads the ledger read-only and only appends to the snapshot archive.

Exit code: 0 for a run that captured, had nothing in the window, or failed on some contracts (the next
run is 10 minutes away); 1 only when the exchange could not be reached and nothing was saved, or the
runtime root is missing, so ``system_health`` reports a capture that is fully down.
"""

from __future__ import annotations

import argparse
import json
import logging

from .ncaaf_prekickoff_capture import (
    DEFAULT_MAX_CAPTURE_SECONDS,
    DEFAULT_WINDOW_MINUTES,
    capture_ncaaf_prekickoff,
)
from .runtime_paths import RuntimePaths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ncaaf-prekickoff-capture", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--window-minutes",
        type=float,
        default=DEFAULT_WINDOW_MINUTES,
        help="capture games kicking off within this many minutes (default %(default)s)",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=DEFAULT_MAX_CAPTURE_SECONDS,
        help="wall-clock budget for discovery and capture (default %(default)s)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paths = RuntimePaths.resolve(require_external_runtime=True)
    result = capture_ncaaf_prekickoff(
        window_minutes=args.window_minutes,
        ledgers_db=paths.ledgers_db,
        runtime_root=paths.runtime_root,
        max_capture_seconds=args.max_seconds,
    )
    print(json.dumps(result, default=str, sort_keys=True), flush=True)
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
