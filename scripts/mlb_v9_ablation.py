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
    train, validation, holdout, _split_meta = chronological_split(rows)
    print(f"      train: {len(train)} rows, validation: {len(validation)} rows, holdout: {len(holdout)} rows")

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
            all_rows = result.get("holdout_all_rows", {})
            per_split = result.get("per_split", {})
            diag_locked = (result.get("diagnostic_60") or {}).get("locked_holdout", {})
            variants[name] = {
                "variant": name,
                "feature_names": features,
                "status": status,
                # Metrics-first fields (operator directive 2026-08-13): the
                # threshold-independent holdout metrics lead; executable-EV
                # calls/units are secondary evidence.
                "holdout_all_rows": all_rows,
                "coverage": result.get("coverage"),
                "per_split": per_split,
                "calls_at_executable_ev": calls,
                "net_roi": round(units, 6),
                "brier": brier,
                "hit_rate": round(hit_rate, 6) if hit_rate else None,
                "diagnostic_60_holdout": {
                    "calls": diag_locked.get("calls"),
                    "hit_rate": diag_locked.get("hit_rate"),
                    "brier_score": diag_locked.get("brier_score"),
                },
            }
            ll = all_rows.get("log_loss")
            ec = all_rows.get("expected_calibration_error")
            print(f"calls={calls}, hit_rate={hit_rate:.3f}, units={units:.1f}, logloss={ll}, ece={ec}")
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: one variant's failure must not abort the rest of the ablation sweep
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

    # 6. Summary — metrics-first ordering (operator directive 2026-08-13).
    # LogLoss/Brier/ECE/calibration/fold-stability/coverage lead; units last.
    print("\n[6/6] Summary (sorted by holdout log loss; units listed last):")
    header = (
        f"  {'variant':55s} {'logloss':>8s} {'brier':>8s} {'ece':>7s} "
        f"{'calSlope':>8s} {'calInt':>8s} {'cov':>6s} "
        f"{'foldΔLL':>7s} {'calls':>5s} {'hit':>6s} {'units':>8s}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    rows = [
        (name, m)
        for name, m in variants.items()
        if m.get("status") == "evaluated" and m.get("holdout_all_rows", {}).get("log_loss") is not None
    ]
    for name, m in sorted(rows, key=lambda x: x[1]["holdout_all_rows"]["log_loss"]):
        ar = m["holdout_all_rows"]
        ps = m.get("per_split", {})
        folds = [ps.get(k, {}).get("log_loss") for k in ("train", "validation", "holdout")]
        fold_delta = round(max(folds) - min(folds), 4) if all(v is not None for v in folds) else None
        print(
            f"  {name:55s} {ar.get('log_loss'):>8.4f} {ar.get('brier_score'):>8.4f} "
            f"{ar.get('expected_calibration_error'):>7.4f} "
            f"{ar.get('calibration_slope'):>8.3f} {ar.get('calibration_intercept'):>8.3f} "
            f"{m.get('coverage'):>6.3f} {fold_delta:>7.4f} "
            f"{m['calls_at_executable_ev']:>5d} {m['hit_rate']:>6.3f} {m['net_roi']:>+8.1f}"
        )

    # 7. Incumbent (v8) reproduction gate. v8's shipped feature set is
    # elo_trend_park_weather_starter_bullpen; its artifact qualification was
    # produced at the 0.60 validation-target threshold (diagnostic_60).
    # If this run cannot reproduce it closely, the ablation results must not
    # be trusted for promotion decisions (operator directive 2026-08-13).
    v8_name = "elo_trend_park_weather_starter_bullpen"
    v8 = variants.get(v8_name)
    if v8 and v8.get("status") == "evaluated":
        v8_artifact = json.loads((PROJECT_ROOT / "config/models/mlb-elo-trend-lr-v8.json").read_text())
        v8_qual = v8_artifact.get("qualification", {})
        diag = v8.get("diagnostic_60_holdout", {})
        print("\n[v8 reproduction gate]")
        print(
            f"  harness {v8_name} @0.60-target: calls={diag.get('calls')}, "
            f"hit={diag.get('hit_rate')}, brier={diag.get('brier_score')}"
        )
        print(
            f"  artifact mlb-elo-trend-lr-v8 qualification: calls={v8_qual.get('calls')}, "
            f"hit={v8_qual.get('hit_rate')}, brier={v8_qual.get('brier_score')}"
        )
        if diag.get("calls") and v8_qual.get("calls"):
            call_ratio = diag["calls"] / v8_qual["calls"]
            hit_delta = abs(diag["hit_rate"] - v8_qual["hit_rate"])
            reproduced = 0.7 <= call_ratio <= 1.3 and hit_delta <= 0.03
            print(
                f"  reproduced_closely: {reproduced} (call_ratio={call_ratio:.3f}, hit_delta={hit_delta:.4f})"
            )
    else:
        print(
            f"\n[v8 reproduction gate] {v8_name} missing or failed — STOP: do not "
            f"use these ablation results for promotion decisions."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
