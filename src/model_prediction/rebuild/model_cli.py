"""Command-line entrypoint for `rebuild-model`: model train/compare ops.

Same shape as `data_cli.py`: this module owns argument parsing and safety
wiring only. Sport-specific training/comparison logic lives behind the
`model_lifecycle.build_model_lifecycle` registration seam. Every sport
currently reports `NOT_IMPLEMENTED`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from .config import load_rebuild_config
from .model_lifecycle import SUPPORTED_MODEL_SPORTS, build_model_lifecycle
from .safety import RebuildPathPolicy, assert_shadow_only

FORBIDDEN_LIVE_FLAGS = frozenset({"--execute", "--live", "--real-order", "--promote"})


def run(command: str, sport: str, data_root: str, *, status: str) -> dict[str, Any]:
    """Run one model-lifecycle operation. ``data_root`` stays injectable for
    tests, same convention as `cli.py::run` / `data_cli.py::run`."""
    assert_shadow_only()
    lifecycle = build_model_lifecycle(sport, data_root, status=status)
    if command == "train":
        return lifecycle.train()
    return lifecycle.compare()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rebuild-model",
        description="Shadow-only rebuild model train/compare (SHADOW ONLY; NO LIVE EXECUTION; NO PROMOTION)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train", help="Train a challenger model for one sport")
    train.add_argument("--sport", required=True, choices=SUPPORTED_MODEL_SPORTS)
    compare = sub.add_parser("compare", help="Compare challenger model families for one sport")
    compare.add_argument("--sport", required=True, choices=SUPPORTED_MODEL_SPORTS)
    return parser


def _reject_live_flags(parser: argparse.ArgumentParser, argv: Sequence[str]) -> None:
    for raw in argv:
        flag = raw.split("=", 1)[0]
        if flag in FORBIDDEN_LIVE_FLAGS:
            parser.error(f"{flag} is forbidden: rebuild model operations are permanently shadow-only")


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
