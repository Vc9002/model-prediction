"""Command-line entrypoint for `rebuild-data`: data-only backfill/audit ops.

This module owns argument parsing and safety wiring only. It carries no
sport-specific ingestion logic itself -- that lives behind the
`data_foundation.build_data_foundation` registration seam (see that
module's docstring). Every sport currently reports `NOT_IMPLEMENTED`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from .config import load_rebuild_config
from .data_foundation import SUPPORTED_DATA_SPORTS, build_data_foundation
from .safety import RebuildPathPolicy, assert_shadow_only

FORBIDDEN_LIVE_FLAGS = frozenset({"--execute", "--live", "--real-order", "--promote"})


def run(command: str, sport: str, data_root: str, *, status: str) -> dict[str, Any]:
    """Run one data operation. ``data_root`` stays injectable for tests,
    same convention as `cli.py::run`."""
    assert_shadow_only()
    foundation = build_data_foundation(sport, data_root, status=status)
    if command == "backfill":
        return foundation.backfill()
    return foundation.audit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rebuild-data",
        description="Shadow-only rebuild data backfill/audit (SHADOW ONLY; NO LIVE EXECUTION)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    backfill = sub.add_parser("backfill", help="Backfill raw/normalized data for one sport")
    backfill.add_argument("--sport", required=True, choices=SUPPORTED_DATA_SPORTS)
    audit = sub.add_parser("audit", help="Audit already-backfilled coverage for one sport")
    audit.add_argument("--sport", required=True, choices=SUPPORTED_DATA_SPORTS)
    return parser


def _reject_live_flags(parser: argparse.ArgumentParser, argv: Sequence[str]) -> None:
    for raw in argv:
        flag = raw.split("=", 1)[0]
        if flag in FORBIDDEN_LIVE_FLAGS:
            parser.error(f"{flag} is forbidden: rebuild data operations are permanently shadow-only")


def main(argv: Sequence[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    _reject_live_flags(parser, raw_argv)
    args = parser.parse_args(raw_argv)

    config = load_rebuild_config()
    assert_shadow_only(config)
    policy = RebuildPathPolicy.from_config(config)
    policy.assert_runtime_write(config.paths.data_root)
    sport_config = config.sports.get(args.sport)
    status = sport_config.status if sport_config is not None else "unregistered"

    report = run(args.command, args.sport, str(config.paths.data_root), status=status)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
