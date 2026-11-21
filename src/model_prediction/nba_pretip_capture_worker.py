"""Supervisor worker ``nba-pretip-capture``: ESPN odds and Polymarket quotes for NBA games about to tip.

Scheduled every 10 minutes from 11:00 to 02:00 Eastern (``scripts/register_windows_tasks.ps1``), so
every game is captured about 20 and 10 minutes before tip. It deliberately does not take the daily
writer lock: daily can hold it for most of an hour and the capture would miss its T-10 slot. It only
appends to capture files and never touches a ledger.

Exit code: 0 for a run that captured, had nothing in the window, or failed on some games (the next
run is 10 minutes away); 1 only when no ESPN scoreboard could be read or the runtime root is missing,
so ``system_health`` reports a capture that is fully down.
"""

from __future__ import annotations

import argparse
import json
import logging

from .data_sources.nba_pretip_capture import (
    DEFAULT_ESPN_WINDOW_MINUTES,
    DEFAULT_POLYMARKET_MAX_SECONDS,
    DEFAULT_WINDOW_MINUTES,
    capture_nba_pretip,
)
from .runtime_paths import RuntimePaths

_logger = logging.getLogger(__name__)


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
    print(json.dumps(result, default=str, sort_keys=True), flush=True)
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
