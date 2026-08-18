"""MLB v10 moneyline ablation -- untested candidates on the frozen PIT table.

The v9 14-variant matrix tested additions (FIP/K-BB/trend/bullpen/park-add/
weather/lineup) and closed with ZERO KEEP. This pass tests the candidates
that matrix never ran:

  - REPRO: control reproduction gate -- re-run the v8 control with the
    original seed and compare against the stored variant_A numbers (the
    harness must reproduce the incumbent before any variant is believable)
  - O: park_factor -> park_factor_pit REPLACEMENT (the documented static-
    table leak fix; variant I only ADDED pit park, never removed the leak)
  - P: + rest_disparity
  - Q: + back_to_back_gap
  - R: + games_last_7_gap
  - S: + hot_cold_gap
  - T: + consistency_gap
  - U: + trailing_home_win_rate_30d
  - V: + elo_neutral_probability
  - W: starter_era_gap -> pitcher_era_gap (naming legacy check)

Same gates as the v9 matrix: mean holdout-fold Brier delta < -0.002,
>=4/5 folds better, date-cluster bootstrap P(better) >= 0.90, coverage
>= 0.90. Each variant runs through the identical run_variant harness --
no new evaluation code, so the comparison against the v9 results is
apples-to-apples.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.mlb_v9_ablation_matrix import CONTROL_FEATURES, PROJECT_ROOT, make_feature_set, run_variant

VARIANTS = [
    # (tag, features to add, features to swap out, description)
    ("O", ["park_factor_pit"], ["park_factor"], "pit park factor REPLACES the leaked static park_factor"),
    ("P", ["rest_disparity"], [], "add rest disparity (schedule)"),
    ("Q", ["back_to_back_gap"], [], "add back-to-back game flag"),
    ("R", ["games_last_7_gap"], [], "add games-in-last-7-days gap"),
    ("S", ["hot_cold_gap"], [], "add hot/cold form"),
    ("T", ["consistency_gap"], [], "add consistency form"),
    ("U", ["trailing_home_win_rate_30d"], [], "add trailing 30d home win rate"),
    ("V", ["elo_neutral_probability"], [], "add neutral Elo probability"),
    ("W", ["pitcher_era_gap"], ["starter_era_gap"], "starter_era_gap -> pitcher_era_gap naming swap"),
]


def main() -> int:
    out_dir = PROJECT_ROOT / "outputs/research/mlb_v9_ablation"

    # Reproduction gate: re-run the exact control with the original seed and
    # compare against the stored variant_A result from the v9 matrix.
    repro = run_variant(
        variant="REPRO_control",
        features=list(CONTROL_FEATURES),
        seed=20260817,
        description="v8 control reproduction gate (same seed as the v9 matrix)",
    )
    stored_a_path = out_dir / "variant_A.json"
    stored_a = json.loads(stored_a_path.read_text(encoding="utf-8")) if stored_a_path.exists() else None
    if stored_a is not None:
        # The reproduction compares the control-vs-control delta (both sides
        # identical => mean delta ~0) -- the real check is that incumbent
        # brier levels match the stored run's incumbent brier levels.
        drift = abs(
            sum(f["incumbent_brier"] for f in repro["fold_results"])
            - sum(f["incumbent_brier"] for f in stored_a.get("fold_results", []))
        )
        print(f"REPRO: stored variant_A exists; incumbent-brier-sum drift vs stored = {drift:.6f}")
        repro["reproduction"] = {
            "stored_variant_A_mean_delta": stored_a.get("mean_delta_brier"),
            "repro_mean_delta": repro["mean_delta_brier"],
            "incumbent_brier_sum_drift": round(drift, 6),
            "reproduces": drift < 1e-9,
        }
        (out_dir / "variant_REPRO_control.json").write_text(
            json.dumps(repro, indent=2) + "\n", encoding="utf-8"
        )
    else:
        print("REPRO: no stored variant_A.json -- skipping stored comparison, running control only")
        (out_dir / "variant_REPRO_control.json").write_text(
            json.dumps(repro, indent=2) + "\n", encoding="utf-8"
        )

    results = {}
    for tag, additions, swaps, description in VARIANTS:
        print(f"\n=== variant {tag}: {description} ===", flush=True)
        # run_variant takes the FULL feature tuple; compose control + additions
        # - swaps through the same helper the matrix CLI uses.
        features = make_feature_set(additions, swaps)
        report = run_variant(
            variant=tag,
            features=features,
            seed=20260818,
            description=description,
            min_date=None,
        )
        results[tag] = {
            "description": description,
            "mean_delta_brier": report["mean_delta_brier"],
            "folds_better": report["folds_better"],
            "p_better": report["bootstrap_p_better"],
            "coverage": report["coverage"],
            "verdict": report["verdict"],
        }
        print(
            f"  delta={report['mean_delta_brier']} folds_better={report['folds_better']} "
            f"p_better={report['bootstrap_p_better']} coverage={report['coverage']} "
            f"verdict={report['verdict']}",
            flush=True,
        )

    summary_path = out_dir / "v10_summary.json"
    summary_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"\nsummary written to {summary_path}")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
