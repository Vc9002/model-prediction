"""NBA research replay harness CLI (exploratory; train + validation only).

  python scripts/nba_v10/replay_harness.py golden          # replay v9, write/diff golden rows
  python scripts/nba_v10/replay_harness.py replay NAME...  # replay named engines, cache rows
  python scripts/nba_v10/replay_harness.py score NAME...   # scorecards from cached rows

Inputs are read from the main data root (--data-root, default E:/MP/data). Cached replay
rows go to <data-root>/research/nba_v10/harness/ (untracked); the golden v9 rows and the
reports are written under outputs/nba/v10_harness/ in this repo.

Do not run heavy replays between 00:20 and 01:20 ET (scheduled runs crash on low memory).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from model_prediction.domain import EASTERN
from model_prediction.features.base import FeatureStore
from model_prediction.nba_replay_harness import (
    DEFAULT_LINES_PATH,
    EngineSpec,
    diff_golden,
    file_sha256,
    golden_rows,
    load_closing_lines,
    read_rows,
    replay,
    replay_dates,
    score_engine,
    scorecard_markdown,
    split_boundaries,
    write_rows,
)

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "outputs" / "nba" / "v10_harness"
GOLDEN = OUT_DIR / "golden_v9_rows.jsonl"
GOLDEN_META = OUT_DIR / "golden_v9_meta.json"


def _blackout_guard() -> None:
    now = datetime.now(EASTERN)
    minutes = now.hour * 60 + now.minute
    if 20 <= minutes <= 80:
        sys.exit(f"refusing heavy replay at {now:%H:%M} ET (00:20-01:20 blackout)")


def _engines() -> dict[str, EngineSpec]:
    from model_prediction.nba_v10_engines import engine_registry

    return engine_registry(REPO)


def _cache_dir(data_root: Path) -> Path:
    return data_root / "research" / "nba_v10" / "harness"


def _inputs(data_root: Path) -> tuple[FeatureStore, dict[str, Any], dict[str, str]]:
    store = FeatureStore(data_root)
    lines_path = data_root / DEFAULT_LINES_PATH.relative_to("data")
    lines = load_closing_lines(lines_path)
    meta = {
        "games_jsonl": str(store.processed_path("nba")),
        "games_sha256": file_sha256(store.processed_path("nba")),
        "lines_jsonl": str(lines_path),
        "lines_sha256": file_sha256(lines_path),
    }
    return store, lines, meta


# Pinned 2026-11-20 from split_boundaries over every replayable date (same rule as the review
# diagnostics: 2,049 train / 762 validation games). games.jsonl grows daily, which would slide a
# recomputed boundary; the study keeps these fixed and only reports drift.
PINNED_VAL_START = "2025-11-03"
PINNED_HOLDOUT_START = "2026-02-23"


def _boundaries(store: FeatureStore) -> tuple[str, str]:
    all_dates = list(replay_dates(store, end_date="9999-12-31"))
    dates_with_history = [d for d in all_dates if len(store.games_before("nba", d)) >= 200]
    computed = split_boundaries(dates_with_history)
    if computed != (PINNED_VAL_START, PINNED_HOLDOUT_START):
        print(f"note: recomputed split {computed} differs from pinned; using pinned", flush=True)
    return PINNED_VAL_START, PINNED_HOLDOUT_START


def cmd_replay(names: list[str], data_root: Path) -> dict[str, list[dict[str, Any]]]:
    _blackout_guard()
    registry = _engines()
    store, lines, meta = _inputs(data_root)
    t0 = time.time()
    rows = replay(store, [registry[n] for n in names], lines, progress=lambda d: print(d, flush=True))
    print(f"replayed {names} in {time.time() - t0:.0f}s", flush=True)
    for name, engine_rows in rows.items():
        path = _cache_dir(data_root) / f"{name}.jsonl"
        write_rows(path, engine_rows)
        (path.with_suffix(".meta.json")).write_text(json.dumps(meta, indent=2))
        print(f"{name}: {len(engine_rows)} rows -> {path}")
    return rows


def cmd_golden(data_root: Path) -> None:
    rows = cmd_replay(["v9"], data_root)["v9"]
    _, _, meta = _inputs(data_root)
    golden = golden_rows(rows)
    if GOLDEN.exists():
        result = diff_golden(read_rows(GOLDEN), golden)
        print("golden diff:", result)
        return
    write_rows(GOLDEN, golden)
    GOLDEN_META.write_text(json.dumps({**meta, "rows": len(golden)}, indent=2) + "\n")
    print(f"wrote golden {len(golden)} rows")


def cmd_diff(names: list[str], data_root: Path) -> None:
    golden = read_rows(GOLDEN)
    for name in names:
        print(name, diff_golden(golden, golden_rows(read_rows(_cache_dir(data_root) / f"{name}.jsonl"))))


def cmd_score(names: list[str], data_root: Path, draws: int) -> None:
    store = FeatureStore(data_root)
    val_start, holdout_start = _boundaries(store)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        rows = read_rows(_cache_dir(data_root) / f"{name}.jsonl")
        card = score_engine(rows, val_start, holdout_start, draws=draws)
        md = scorecard_markdown(name, card)
        (OUT_DIR / f"scorecard_{name}.md").write_text(md, encoding="utf-8")
        print(md)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["golden", "replay", "diff", "score"])
    parser.add_argument("names", nargs="*")
    parser.add_argument("--data-root", type=Path, default=Path("E:/MP/data"))
    parser.add_argument("--draws", type=int, default=2000)
    args = parser.parse_args()
    if args.command == "golden":
        cmd_golden(args.data_root)
    elif args.command == "replay":
        cmd_replay(args.names, args.data_root)
    elif args.command == "diff":
        cmd_diff(args.names, args.data_root)
    else:
        cmd_score(args.names, args.data_root, args.draws)


if __name__ == "__main__":
    main()
