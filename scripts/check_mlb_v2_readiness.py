"""Real, read-only readiness check for mlb_moneyline_v2 -- CLAUDE.md's
next-phase MLB-8 (multi-sport execution spec).

Reports how many real completed MLB games exist in the frozen
mlb_moneyline_v2 test window (test_start onward) against the predeclared
minimum sample floor (test_consumption_registry.json's own
minimum_sample_before_evaluation.n_real_games). Never computes or prints
any aggregate accuracy/log-loss/Brier number -- that would be exactly the
"peeking before the predeclared minimum sample" this script exists to
prevent. Reports real sample size and a real readiness verdict only.

Registry-safe: reads test_consumption_registry.json, never writes it.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/check_mlb_v2_readiness.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.mlb_features import dedupe_scoreboard

REGISTRY_PATH = Path("outputs/rebuild/test_consumption_registry.json")
SCOREBOARD_PATH = Path("data/rebuild/normalized/mlb/scoreboard.parquet")


def main() -> None:
    if not REGISTRY_PATH.exists():
        print(f"ERROR: {REGISTRY_PATH} not found.")
        sys.exit(1)
    registry = json.loads(REGISTRY_PATH.read_text())
    v2 = registry.get("active_tests", {}).get("mlb_moneyline_v2")
    if v2 is None:
        print("ERROR: mlb_moneyline_v2 not found in the registry. Nothing to check.")
        sys.exit(1)

    if v2.get("consumed"):
        print(f"mlb_moneyline_v2 is already consumed (consumed_at_utc={v2.get('consumed_at_utc')}). "
              "Nothing to check -- a consumed test is never re-evaluated.")
        sys.exit(0)

    test_start = v2["test_start"]
    test_end = v2.get("test_end")
    min_sample = v2.get("minimum_sample_before_evaluation", {}).get("n_real_games")
    if min_sample is None:
        print("ERROR: minimum_sample_before_evaluation.n_real_games is not set on mlb_moneyline_v2. "
              "Refusing to report a readiness verdict without a real predeclared floor.")
        sys.exit(1)

    print(f"1. mlb_moneyline_v2 window: test_start={test_start} test_end={test_end or '(open)'}")
    print(f"2. Predeclared minimum sample before evaluation: {min_sample} real completed games")

    if not SCOREBOARD_PATH.exists():
        print(f"3. {SCOREBOARD_PATH} not found -- 0 real completed games. Not ready.")
        print("\nVerdict: NOT_READY (n=0)")
        sys.exit(0)

    sb = dedupe_scoreboard(pl.read_parquet(SCOREBOARD_PATH))
    completed = sb.filter(pl.col("status") == "STATUS_FINAL")
    in_window = completed.filter(pl.col("event_start_utc") >= test_start)
    if test_end:
        in_window = in_window.filter(pl.col("event_start_utc") <= test_end)
    n_real = in_window.height

    print(f"3. Real completed games in the mlb_moneyline_v2 window so far: {n_real}")
    ready = n_real >= min_sample
    print(f"\nVerdict: {'READY_FOR_EVALUATION' if ready else 'NOT_READY'} "
          f"({n_real}/{min_sample} real completed games)")
    if ready:
        print(
            "\nReal games have reached the predeclared floor. Evaluating mlb_moneyline_v2 is now a "
            "real, deliberate decision that can be made -- this script does not make it or compute "
            "any metric itself. That evaluation, once performed, marks the test consumed and it is "
            "never reused."
        )
    else:
        print(f"\n{min_sample - n_real} more real completed games needed before evaluation is honest. "
              "No aggregate metric is computed or reported below this floor.")


if __name__ == "__main__":
    main()
