"""Data-only CLI for free/open rebuild providers."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .config import default_repo_root, load_rebuild_config
from .mlb_v3.foundation import MLBV3Foundation
from .providers.cache import ProviderRawCache
from .providers.config import load_rebuild_sources_config
from .providers.http import HttpProviderClient, RetryPolicy
from .providers.mlb_stats import MLBStatsProvider
from .providers.sportsdataverse import SportsDataverseProvider
from .providers.statcast import StatcastProvider
from .safety import RebuildPathPolicy, assert_runtime_data_root, assert_shadow_only
from .wnba.foundation import WNBAFoundation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rebuild-data", description="Free/open rebuild data operations")
    sub = parser.add_subparsers(dest="command", required=True)
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--sport", required=True, choices=("wnba", "mlb"))
    backfill.add_argument("--version", choices=("v3",))
    backfill.add_argument("--season", type=int, action="append")
    backfill.add_argument(
        "--provider",
        default="sportsdataverse",
        choices=("sportsdataverse", "mlb_stats", "statcast"),
    )
    backfill.add_argument(
        "--table",
        action="append",
        choices=("schedule", "team_box", "player_box", "rosters", "pbp", "game_feed", "transactions"),
    )
    backfill.add_argument("--start")
    backfill.add_argument("--end")
    backfill.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)

    audit = sub.add_parser("audit")
    audit.add_argument("--sport", required=True, choices=("wnba", "mlb"))
    audit.add_argument("--version", choices=("v3",))
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


def _mlb_foundation(
    data_root: Path, provider_name: str
) -> tuple[MLBV3Foundation, HttpProviderClient]:
    sources = load_rebuild_sources_config()
    policy = sources.mlb_stats if provider_name == "mlb_stats" else sources.statcast
    http = HttpProviderClient(
        retry=RetryPolicy(attempts=policy.retries),
        min_interval_seconds=policy.min_interval_seconds,
    )
    cache = ProviderRawCache(data_root / "raw")
    if provider_name == "mlb_stats":
        return (
            MLBV3Foundation(
                data_root / "normalized",
                repo_root=default_repo_root(),
                mlb_stats=MLBStatsProvider(http, cache),
            ),
            http,
        )
    return (
        MLBV3Foundation(
            data_root / "normalized",
            repo_root=default_repo_root(),
            statcast=StatcastProvider(http, cache),
        ),
        http,
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    config = load_rebuild_config()
    assert_shadow_only(config)
    data_root = assert_runtime_data_root(config.paths.data_root, default_repo_root())
    RebuildPathPolicy.from_config(config).assert_runtime_write(data_root / "raw")
    foundation: WNBAFoundation | MLBV3Foundation
    http: HttpProviderClient
    if args.sport == "wnba":
        if args.version is not None:
            raise SystemExit("WNBA data commands do not accept --version")
        if args.command == "backfill" and args.provider != "sportsdataverse":
            raise SystemExit("WNBA backfill requires the SportsDataverse provider")
        if args.command == "backfill" and not args.season:
            raise SystemExit("WNBA backfill requires --season")
        foundation, http = _foundation(data_root)
    else:
        if args.version != "v3":
            raise SystemExit("MLB research data commands require --version v3")
        if args.command == "backfill" and args.provider not in {"mlb_stats", "statcast"}:
            raise SystemExit("MLB v3 backfill requires --provider mlb_stats or statcast")
        foundation, http = _mlb_foundation(data_root, args.provider if args.command == "backfill" else "mlb_stats")
    try:
        if args.command == "backfill":
            if args.sport == "wnba":
                if args.start or args.end:
                    raise SystemExit("--start/--end filtering is not implemented for season release assets")
                assert isinstance(foundation, WNBAFoundation)
                report = foundation.backfill(
                    args.season,
                    tables=args.table or ("schedule", "team_box", "player_box", "rosters", "pbp"),
                    force=not args.resume,
                )
            else:
                if not args.start or not args.end:
                    raise SystemExit("MLB v3 backfill requires --start and --end")
                start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
                assert isinstance(foundation, MLBV3Foundation)
                if args.provider == "mlb_stats":
                    report = foundation.backfill_mlb_stats(
                        start,
                        end,
                        tables=args.table or ("schedule",),
                        force=not args.resume,
                    )
                else:
                    if args.table:
                        raise SystemExit("Statcast backfill does not accept --table")
                    report = foundation.backfill_statcast(start, end, force=not args.resume)
        else:
            if args.sport == "wnba":
                assert isinstance(foundation, WNBAFoundation)
                report = foundation.audit(args.season)
            else:
                assert isinstance(foundation, MLBV3Foundation)
                report = foundation.audit(args.season)
    finally:
        http.close()
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
