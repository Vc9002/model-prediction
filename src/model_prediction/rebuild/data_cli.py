"""Data-only CLI for free/open rebuild providers."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import default_repo_root, load_rebuild_config
from .providers.cache import ProviderRawCache
from .providers.config import load_rebuild_sources_config
from .providers.http import HttpProviderClient, RetryPolicy
from .providers.sportsdataverse import SportsDataverseProvider
from .safety import RebuildPathPolicy, assert_runtime_data_root, assert_shadow_only
from .wnba.foundation import WNBAFoundation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rebuild-data", description="Free/open rebuild data operations")
    sub = parser.add_subparsers(dest="command", required=True)
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--sport", required=True, choices=("wnba",))
    backfill.add_argument("--season", type=int, action="append", required=True)
    backfill.add_argument("--provider", default="sportsdataverse", choices=("sportsdataverse",))
    backfill.add_argument("--table", action="append", choices=("schedule", "team_box", "player_box", "rosters", "pbp"))
    backfill.add_argument("--start")
    backfill.add_argument("--end")
    backfill.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)

    audit = sub.add_parser("audit")
    audit.add_argument("--sport", required=True, choices=("wnba",))
    audit.add_argument("--season", type=int, required=True)
    return parser


def _foundation(data_root: Path) -> tuple[WNBAFoundation, HttpProviderClient]:
    sources = load_rebuild_sources_config()
    policy = sources.sportsdataverse
    http = HttpProviderClient(
        retry=RetryPolicy(attempts=policy.retries),
        min_interval_seconds=policy.min_interval_seconds,
    )
    cache = ProviderRawCache(data_root / "raw")
    provider = SportsDataverseProvider(http, cache)
    return WNBAFoundation(provider, data_root / "normalized"), http


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    config = load_rebuild_config()
    assert_shadow_only(config)
    data_root = assert_runtime_data_root(config.paths.data_root, default_repo_root())
    RebuildPathPolicy.from_config(config).assert_runtime_write(data_root / "raw")
    foundation, http = _foundation(data_root)
    try:
        if args.command == "backfill":
            if args.start or args.end:
                raise SystemExit("--start/--end filtering is not implemented for season release assets")
            report = foundation.backfill(
                args.season,
                tables=args.table or ("schedule", "team_box", "player_box", "rosters", "pbp"),
                force=not args.resume,
            )
        else:
            report = foundation.audit(args.season)
    finally:
        http.close()
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
