"""Command-line entrypoint for `rebuild-data`: data-only backfill/audit ops.

This module owns argument parsing and safety wiring only. Sport-specific
ingestion logic lives behind the `data_foundation.build_data_foundation`
registration seam (see that module's docstring). `mlb` (MLB v3,
research-only) and `wnba` are wired to real backends; every other sport
reports `NOT_IMPLEMENTED`.
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


def run(command: str, sport: str, data_root: str, *, status: str, repo_root: str, **kwargs: Any) -> dict[str, Any]:
    """Run one data operation. ``data_root``/``repo_root`` stay injectable
    for tests, same convention as `cli.py::run`."""
    assert_shadow_only()
    with build_data_foundation(sport, data_root, status=status, repo_root=repo_root) as foundation:
        if command == "backfill":
            return foundation.backfill(**kwargs)
        return foundation.audit(**kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rebuild-data",
        description="Shadow-only rebuild data backfill/audit (SHADOW ONLY; NO LIVE EXECUTION)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    backfill = sub.add_parser("backfill", help="Backfill raw/normalized data for one sport")
    backfill.add_argument("--sport", required=True, choices=SUPPORTED_DATA_SPORTS)
    backfill.add_argument("--version", choices=("v3",), help="MLB-only: research lane version")
    backfill.add_argument("--provider", choices=("mlb_stats", "statcast"), default="mlb_stats", help="MLB-only")
    backfill.add_argument("--start", help="MLB-only")
    backfill.add_argument("--end", help="MLB-only")
    backfill.add_argument("--season", type=int, action="append", help="WNBA-only, repeatable")
    backfill.add_argument(
        "--table", action="append", dest="tables",
        choices=("schedule", "game_feed", "transactions", "team_box", "player_box", "rosters", "pbp"),
    )
    backfill.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)

    audit = sub.add_parser("audit", help="Audit already-backfilled coverage for one sport")
    audit.add_argument("--sport", required=True, choices=SUPPORTED_DATA_SPORTS)
    audit.add_argument("--version", choices=("v3",), help="MLB-only: research lane version")
    audit.add_argument("--season", type=int)
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

    if args.sport == "mlb" and args.version != "v3":
        parser.error("MLB data commands require --version v3 (the only MLB research lane wired up so far)")
    if args.sport != "mlb" and args.version is not None:
        parser.error(f"--version is not meaningful for {args.sport}")
    if args.command == "backfill" and args.sport == "wnba" and (args.start or args.end):
        parser.error("--start/--end are not meaningful for WNBA (season-based backfill only)")
    if args.command == "backfill" and args.sport != "wnba" and getattr(args, "season", None):
        parser.error(f"--season is not meaningful for {args.sport}")

    config = load_rebuild_config()
    assert_shadow_only(config)
    policy = RebuildPathPolicy.from_config(config)
    policy.assert_runtime_write(config.paths.data_root)
    sport_config = config.sports.get(args.sport)
    status = sport_config.status if sport_config is not None else "unregistered"

    if args.command == "backfill":
        report = run(
            "backfill", args.sport, str(config.paths.data_root),
            status=status, repo_root=str(config.repo_root),
            provider=args.provider, start=args.start, end=args.end,
            seasons=tuple(args.season) if args.season else None,
            tables=tuple(args.tables) if args.tables else None, force=not args.resume,
        )
    else:
        report = run(
            "audit", args.sport, str(config.paths.data_root),
            status=status, repo_root=str(config.repo_root), season=args.season,
        )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
