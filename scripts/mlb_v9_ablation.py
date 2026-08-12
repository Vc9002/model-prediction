"""MLB v9 Phase 1 ablation experiments.

Runs the full FEATURE_VARIANTS suite through walk-forward validation for MLB,
comparing existing variants with 4 new v9 candidates:

  New variants:
    - elo_trend_park_weather_starter_kbb_bullpen   (K-BB% replaces ERA)
    - elo_trend_park_weather_starter_era_kbb_bullpen  (ERA + K-BB%)
    - elo_residual_trend_park_weather_starter_era_bullpen  (Elo-residual trend)
    - elo_trend_park_weather_starter_era_bullpen_fatigue  (bullpen fatigue)

  Already-present variants reused as-is:
    - elo_trend_park_weather_starter_bullpen_fip       (FIP replaces ERA)
    - elo_trend_park_weather_starter_bullpen_era_fip    (ERA + FIP)

Every variant is evaluated with:
  - Chronological 60/20/20 train/validation/holdout split
  - LogisticRegression fit on the training cohort
  - Confidence threshold learned on the validation cohort
  - One locked evaluation on the untouched holdout

Outputs:
  1. Ablation table (JSON) printed to stdout
  2. Experiment log (JSONL) at data/experiments/mlb_v9_ablation.jsonl
     for auditable development history (section 11).

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/mlb_v9_ablation.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.config import PROJECT_ROOT
from model_prediction.experiment_design import ExperimentLog, ablation_table
from model_prediction.features.base import FeatureStore
from model_prediction.validation import (
    FEATURE_VARIANTS,
    build_walk_forward_rows,
    chronological_split,
    evaluate_variant,
)

SPORT = "mlb"

# ── Variant names to evaluate ───────────────────────────────────────────────
# Ordered so the single-feature baselines come first, then the compound
# variants, then the new v9 candidates — following section 11's directive
# to *not* reorder by P&L.

V9_ABLATION_VARIANTS = [
    # --- Single / minimal baselines ---
    "elo_only",
    "elo_trend",
    # --- MLB-specific baselines ---
    "elo_trend_adaptive_hfa",
    "elo_trend_park",
    "elo_trend_park_weather",
    # --- Pitcher / starter quality baselines ---
    "elo_trend_park_pitcher",
    "elo_trend_park_weather_pitcher",
    "elo_trend_park_weather_pitcher_bullpen",
    "elo_trend_park_weather_pitcher_bullpen_fatigue",
    "elo_trend_park_starter",
    "elo_trend_park_starter_fip",
    "elo_trend_park_weather_starter_bullpen",
    # --- Existing FIP / ERA+FIP variants (these ARE the requested
    #     "fip_bullpen" and "era_fip_bullpen" under existing names) ---
    "elo_trend_park_weather_starter_bullpen_fip",
    "elo_trend_park_weather_starter_bullpen_era_fip",
    # --- Probable-starter baselines ---
    "elo_trend_park_probable_starter",
    "elo_trend_park_weather_probable_starter",
    # ── v9 new variants ───────────────────────────────────────────────────
    "elo_trend_park_weather_starter_kbb_bullpen",
    "elo_trend_park_weather_starter_era_kbb_bullpen",
    "elo_residual_trend_park_weather_starter_era_bullpen",
    "elo_trend_park_weather_starter_era_bullpen_fatigue",
]


def main() -> None:
    data_root = PROJECT_ROOT / "data"
    store = FeatureStore(data_root)

    print("=" * 72)
    print("MLB v9 Phase 1 Ablation Experiments")
    print("=" * 72)

    # 1. Build walk-forward validation rows
    print("\n[1/4] Building walk-forward validation rows ...")
    rows = build_walk_forward_rows(store, SPORT)
    print(f"      {len(rows)} rows built from {len({row.date for row in rows})} dates")

    # 2. Chronological 60/20/20 split
    print("\n[2/4] Chronological split (60/20/20) ...")
    train, validation, holdout, split_meta = chronological_split(rows)
    print(f"      train: {len(train)} rows, validation: {len(validation)} rows, "
          f"holdout: {len(holdout)} rows")

    # 3. Evaluate every variant
    print(f"\n[3/4] Evaluating {len(V9_ABLATION_VARIANTS)} variants ...")
    variants: dict[str, dict] = {}
    for i, name in enumerate(V9_ABLATION_VARIANTS, 1):
        features = FEATURE_VARIANTS.get(name)
        if features is None:
            print(f"      [{i:2d}/{len(V9_ABLATION_VARIANTS)}] SKIP {name}: not in FEATURE_VARIANTS")
            continue
        print(f"      [{i:2d}/{len(V9_ABLATION_VARIANTS)}] {name} ({len(features)} features) ...", end=" ")
        try:
            result = evaluate_variant(train, validation, holdout, features)
            primary = result.get("primary_65", {})
            locked = primary.get("locked_holdout", {})
            status = primary.get("status", "unknown")
            calls = locked.get("calls", 0)
            hit_rate = locked.get("hit_rate", 0)
            units = locked.get("units_at_minus_110", 0)
            brier = locked.get("brier_score", None)
            variants[name] = {
                "variant": name,
                "feature_names": features,
                "status": status,
                "calls_at_executable_ev": calls,
                "net_roi": round(units, 6),
                "brier": brier,
                "hit_rate": round(hit_rate, 6) if hit_rate else None,
            }
            print(f"calls={calls}, hit_rate={hit_rate:.3f}, units={units:.1f}")
        except Exception as exc:
            print(f"ERROR: {exc}")
            variants[name] = {"variant": name, "status": "error", "error": str(exc)}

    # 4. Ablation table
    print("\n[4/4] Ablation table:")
    print("-" * 72)
    table = ablation_table(variants)
    table_json = json.dumps(table, indent=2, default=str)
    print(table_json)

    # 5. Experiment log
    log_path = PROJECT_ROOT / "data" / "experiments" / "mlb_v9_ablation.jsonl"
    log = ExperimentLog(log_path)
    for name, metrics in variants.items():
        log.record(name, SPORT, metrics, notes="mlb_v9_phase1")
    print(f"\nExperiment log: {log_path} ({log.trial_count(SPORT)} trials)")

    # 6. Summary
    qualifying = [
        (name, m) for name, m in variants.items()
        if m.get("status") == "evaluated"
        and m.get("calls_at_executable_ev", 0) >= 50
        and (m.get("hit_rate") or 0) >= 0.60
    ]
    print(f"\nQualifying variants: {len(qualifying)}/{len(variants)}")
    for name, m in sorted(qualifying, key=lambda x: x[1].get("net_roi", 0), reverse=True):
        print(f"  {name:55s}  calls={m['calls_at_executable_ev']:4d}  "
              f"hit={m['hit_rate']:.3f}  units={m['net_roi']:+.1f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
