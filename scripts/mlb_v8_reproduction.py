"""Pin-and-replay reproduction of the shipped MLB v8 model's validation.

This is validation/reproduction tooling ONLY. It does not touch, rebuild, or
overwrite ``config/models/mlb-elo-trend-lr-v8.json``, and it makes no
promotion decision -- it just answers "does the walk-forward harness, run
today against exactly v8's own recorded date boundaries and confidence
threshold, reproduce the numbers v8 shipped with?"

How it pins the replay to v8's exact original split:
  - Reads v8's own ``training`` block (coefficient_fit / threshold_selection
    / locked_holdout date boundaries) and its ``market_models.moneyline.
    confidence_threshold`` straight out of the artifact -- no numbers are
    hardcoded here.
  - ``build_walk_forward_rows(..., end_date=...)`` caps the walk-forward
    dataset at the day after v8's locked_holdout end, so today's larger,
    still-growing games.jsonl cannot leak games v8 never saw.
  - ``chronological_split(..., train_end_date=..., validation_end_date=...)``
    reconstructs the three cohorts at v8's exact recorded calendar
    boundaries, not a freshly recomputed 60/20/20 fraction of however many
    rows exist today.
  - ``evaluate_variant(..., fixed_threshold=...)`` plugs v8's own shipped
    confidence_threshold directly into the locked-holdout grading step
    instead of relearning a new one from today's validation cohort.

Usage:
    env PYTHONPATH=src:. .venv/bin/python scripts/mlb_v8_reproduction.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.validation import (
    FEATURE_VARIANTS,
    build_walk_forward_rows,
    chronological_split,
    evaluate_variant,
)

SPORT = "mlb"
V8_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / "mlb-elo-trend-lr-v8.json"
V8_VARIANT_NAME = "elo_trend_park_weather_starter_bullpen"

# Same tolerance bands scripts/mlb_v9_ablation.py's "[v8 reproduction gate]"
# uses -- reused verbatim for consistency, not reinvented here.
REPRODUCTION_CALL_RATIO_BAND = (0.7, 1.3)
REPRODUCTION_HIT_DELTA_TOLERANCE = 0.03


def main() -> int:
    print("=" * 72)
    print("MLB v8 pin-and-replay reproduction (validation tooling only)")
    print("=" * 72)

    # 1. Load v8's own recorded date boundaries and confidence threshold.
    print("\n[1/5] Loading v8 artifact ...")
    artifact = json.loads(V8_ARTIFACT_PATH.read_text(encoding="utf-8"))
    training = artifact["training"]
    train_end_date = training["coefficient_fit"]["end"]
    validation_end_date = training["threshold_selection"]["end"]
    locked_holdout_end = training["locked_holdout"]["end"]
    confidence_threshold = float(
        artifact["market_models"]["moneyline"]["confidence_threshold"]
    )
    v8_qualification = artifact["qualification"]
    print(f"      model_version: {artifact['model_version']}")
    print(f"      train (coefficient_fit):      <= {train_end_date}")
    print(f"      validation (threshold_selection): {train_end_date} < d <= {validation_end_date}")
    print(f"      locked_holdout:                {validation_end_date} < d <= {locked_holdout_end}")
    print(f"      pinned confidence_threshold:  {confidence_threshold}")

    # Exclusive cutoff for build_walk_forward_rows: one day past the
    # inclusive locked_holdout end date it's meant to cap at.
    rows_end_date = (
        date.fromisoformat(locked_holdout_end) + timedelta(days=1)
    ).isoformat()

    # 2. Build walk-forward rows capped at v8's own locked_holdout end date.
    print("\n[2/5] Building walk-forward rows (capped at "
          f"{locked_holdout_end}) ...")
    data_root = PROJECT_ROOT / "data"
    store = FeatureStore(data_root)
    rows = build_walk_forward_rows(store, SPORT, end_date=rows_end_date)
    print(f"      {len(rows)} rows built from {len({row.date for row in rows})} dates")

    # 3. Split at v8's exact recorded date boundaries (not fractional).
    print("\n[3/5] Splitting at v8's exact recorded date boundaries ...")
    train, validation, holdout, split_meta = chronological_split(
        rows,
        train_end_date=train_end_date,
        validation_end_date=validation_end_date,
    )
    print(f"      train: {len(train)} rows ({split_meta['train']['start']}..{split_meta['train']['end']})")
    print(f"      validation: {len(validation)} rows "
          f"({split_meta['validation']['start']}..{split_meta['validation']['end']})")
    print(f"      locked_holdout: {len(holdout)} rows "
          f"({split_meta['locked_holdout']['start']}..{split_meta['locked_holdout']['end']})")

    # 4. Evaluate v8's exact shipped feature set with the pinned threshold
    #    (no relearning from today's validation cohort).
    print(f"\n[4/5] Evaluating {V8_VARIANT_NAME!r} with pinned threshold "
          f"{confidence_threshold} ...")
    feature_names = FEATURE_VARIANTS[V8_VARIANT_NAME]
    result = evaluate_variant(
        train, validation, holdout, feature_names,
        fixed_threshold=confidence_threshold,
    )
    primary = result["primary_65"]
    if primary.get("status") != "evaluated":
        print(f"      FAILED: {primary}")
        return 1
    replay = primary["locked_holdout"]

    # 5. Side-by-side comparison against the artifact's own qualification.
    print("\n[5/5] Replay vs. artifact qualification block:")
    print("-" * 72)
    print(f"  {'metric':16s} {'replay':>14s} {'artifact':>14s}")
    print(f"  {'calls':16s} {replay.get('calls'):>14} {v8_qualification.get('calls'):>14}")
    print(f"  {'hit_rate':16s} {replay.get('hit_rate'):>14} {v8_qualification.get('hit_rate'):>14}")
    print(f"  {'brier_score':16s} {replay.get('brier_score'):>14} {v8_qualification.get('brier_score'):>14}")

    replay_calls = replay.get("calls")
    replay_hit_rate = replay.get("hit_rate")
    v8_calls = v8_qualification.get("calls")
    v8_hit_rate = v8_qualification.get("hit_rate")

    reproduced_closely = False
    call_ratio = hit_delta = None
    if replay_calls and v8_calls and replay_hit_rate is not None and v8_hit_rate is not None:
        call_ratio = replay_calls / v8_calls
        hit_delta = abs(replay_hit_rate - v8_hit_rate)
        reproduced_closely = (
            REPRODUCTION_CALL_RATIO_BAND[0] <= call_ratio <= REPRODUCTION_CALL_RATIO_BAND[1]
            and hit_delta <= REPRODUCTION_HIT_DELTA_TOLERANCE
        )

    print(f"\n  call_ratio: {call_ratio}")
    print(f"  hit_delta: {hit_delta}")
    print(f"  reproduced_closely: {reproduced_closely}")

    print("\nNo promotion decision is made by this script. Report the raw "
          "numbers above; interpretation is a separate, explicit step.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
