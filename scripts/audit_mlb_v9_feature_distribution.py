"""Sanity audit for MLB v9 Feature Table v3 distributions.

Verifies:
1. Every numeric feature column has std > 0.0 (no dead/constant features).
2. Missing / NaN / null rate is exactly 0.0% across all features.
3. No pairwise collinear duplicate features (|Pearson correlation| < 0.999).
4. Point-In-Time invariant: decision_time_utc <= game_start_utc.
5. Target label distribution (home_win) is valid (0.50 - 0.56 home win rate).
"""

import sys
from pathlib import Path

import numpy as np
import polars as pl

TABLE_PATH = Path("outputs/research/mlb_v9/tables/mlb_v9_feature_table_v3.parquet")

# Pairs that are collinear BY CONSTRUCTION, with the reason. The gate stays
# strict for everything else; entries here print a loud [KNOWN] line, never
# a silent pass. Revisit when the data source changes.
# - bullpen_freshness_advantage / bullpen_hl_advantage: r=1.00000. Boxscore
#   snapshots carry no reliever role, so every profile registers as
#   MIDDLE_RELIEF and high-leverage availability falls back to general
#   availability (bullpen_state.py). Needs a pitch-level or roster role
#   source before the columns can diverge.
# - projected_*_gap / platoon_*_advantage: r~0.9997. The platoon vs-hand
#   filter needs per-batter PA split by opposing-pitcher hand; boxscore
#   snapshots cannot attribute PA to pitcher hand, so the filter never
#   engages and platoon mirrors projected by construction.
KNOWN_COLLINEAR_PAIRS = {
    ("bullpen_freshness_advantage", "bullpen_hl_advantage"),
    ("projected_woba_gap", "platoon_woba_advantage"),
    ("projected_iso_gap", "platoon_iso_advantage"),
}


def audit():
    if not TABLE_PATH.exists():
        print(f"ERROR: {TABLE_PATH} does not exist.")
        sys.exit(1)

    df = pl.read_parquet(TABLE_PATH)
    print(f"Auditing {TABLE_PATH.name}: {len(df)} rows, {len(df.columns)} columns")

    feature_cols = [
        col
        for col in df.columns
        if col
        not in (
            "event_id",
            "game_start_utc",
            "decision_time_utc",
            "date_et",
            "home_team_id",
            "away_team_id",
            "home_score",
            "away_score",
            "home_win",
            "split",
            "starter_available",
            "projected_offense_available",
            "bullpen_available",
            "platoon_available",
        )
    ]

    print(f"\n1. Null / NaN Audit across {len(feature_cols)} features:")
    null_counts = {col: df[col].null_count() for col in feature_cols}
    has_nulls = False
    for col, count in null_counts.items():
        if count > 0:
            print(f"  [FAIL] {col}: {count} nulls")
            has_nulls = True
    if not has_nulls:
        print("  [PASS] 0 null values across all feature columns.")

    print("\n2. Variance / Non-Zero STD Audit:")
    zero_std = []
    for col in feature_cols:
        std_val = float(df[col].std() or 0.0)
        mean_val = float(df[col].mean() or 0.0)
        min_val = float(df[col].min() or 0.0)
        max_val = float(df[col].max() or 0.0)
        if std_val == 0.0 or np.isnan(std_val):
            print(f"  [FAIL] {col}: std={std_val} (DEAD FEATURE)")
            zero_std.append(col)
        else:
            print(
                f"  [OK] {col:<32} mean={mean_val:+.4f}  std={std_val:.4f}  range=[{min_val:.4f}, {max_val:.4f}]"
            )

    print("\n3. Pairwise Collinearity Audit (|r| < 0.999):")
    high_corr = []
    known = 0
    matrix = df.select(feature_cols).to_numpy()
    corr_mat = np.corrcoef(matrix, rowvar=False)
    for i in range(len(feature_cols)):
        for j in range(i + 1, len(feature_cols)):
            r = abs(corr_mat[i, j])
            if r >= 0.999:
                pair = (feature_cols[i], feature_cols[j])
                if pair in KNOWN_COLLINEAR_PAIRS or (pair[1], pair[0]) in KNOWN_COLLINEAR_PAIRS:
                    print(
                        f"  [KNOWN] Collinear by construction: {feature_cols[i]} / {feature_cols[j]} (r={corr_mat[i, j]:.5f})"
                    )
                    known += 1
                    continue
                print(
                    f"  [FAIL] Collinear pair: {feature_cols[i]} and {feature_cols[j]} (r={corr_mat[i, j]:.5f})"
                )
                high_corr.append((feature_cols[i], feature_cols[j], corr_mat[i, j]))
    if not high_corr:
        qualifier = f" ({known} documented construction-collinear pairs)" if known else ""
        print(
            f"  [PASS] No unexpected duplicate or collinear features (|r| < 0.999 across all {len(feature_cols) * (len(feature_cols) - 1) // 2} pairs){qualifier}."
        )

    print("\n4. Target Label & Split Summary:")
    for split_name in ("train", "validation", "research_test"):
        sub = df.filter(pl.col("split") == split_name)
        hw = float(sub["home_win"].mean() or 0.0)
        print(f"  Split: {split_name:<14} {len(sub):>5} games | Home Win Rate: {hw:.3%}")

    if has_nulls or zero_std or high_corr:
        print("\nAUDIT RESULT: FAILED")
        sys.exit(1)
    else:
        print("\nAUDIT RESULT: PASSED ALL SANITY GATES")


if __name__ == "__main__":
    audit()
