"""Freeze the MLB v9 research feature table v4 (Clean Non-Collinear Feature Matrix).

Builds outputs/research/mlb_v9/tables/mlb_v9_feature_table_v4.parquet containing:
1. Identity columns & split assignments (Train / Validation / Research Test).
2. Clean, non-collinear feature blocks passing automated statistical audit (VIF < 6.0, condition number < 6.0).
3. Manifest and metadata JSON with cryptographic hashes.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/mlb_v9_feature_table_v4.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl

from model_prediction.features.mlb_v9_v4 import audit_v9_features

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TABLES_DIR = PROJECT_ROOT / "outputs" / "research" / "mlb_v9" / "tables"
MANIFESTS_DIR = PROJECT_ROOT / "outputs" / "research" / "mlb_v9" / "manifests"

V3_TABLE_PATH = TABLES_DIR / "mlb_v9_feature_table_v3.parquet"
V4_TABLE_PATH = TABLES_DIR / "mlb_v9_feature_table_v4.parquet"
V4_MANIFEST_PATH = MANIFESTS_DIR / "mlb_v9_feature_table_v4.json"

V4_FEATURES = [
    "elo_probability",
    "trend_gap",
    "park_factor_pit",
    "rest_disparity",
    "back_to_back_gap",
    "starter_k_pct_gap",
    "starter_bb_pct_gap",
    "starter_depth_gap",
    "projected_woba_gap",
    "projected_iso_gap",
    "projected_k_pct_gap",
    "projected_bb_pct_gap",
    "bullpen_fip_advantage",
    "bullpen_freshness_advantage",
]

META_COLUMNS = [
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
]


def build_v4_table() -> None:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/4] Loading v3 feature table ...")
    if not V3_TABLE_PATH.exists():
        raise FileNotFoundError(f"Missing {V3_TABLE_PATH}")
    df_v3 = pl.read_parquet(V3_TABLE_PATH)
    print(f"      Loaded {len(df_v3)} games from v3")

    print("[2/4] Constructing clean non-collinear v4 feature matrix ...")
    selected_cols = META_COLUMNS + V4_FEATURES
    df_v4 = df_v3.select(selected_cols)

    print("[3/4] Running automated statistical audit ...")
    feat_mat = df_v4.select(V4_FEATURES).to_numpy()
    audit_rep = audit_v9_features(feat_mat, V4_FEATURES)
    print(f"      Audit Passed: {audit_rep.passed_audit}")
    print(f"      Condition Number: {audit_rep.condition_number}")
    print(f"      Max VIF: {audit_rep.max_vif}")
    print(f"      High Correlation Pairs: {len(audit_rep.high_correlation_pairs)}")

    if not audit_rep.passed_audit:
        raise ValueError("v4 feature matrix failed statistical audit!")

    print("[4/4] Writing v4 parquet and manifest ...")
    df_v4.write_parquet(V4_TABLE_PATH)
    file_bytes = V4_TABLE_PATH.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    manifest = {
        "dataset_name": "mlb_v9_feature_table_v4",
        "schema_version": "4.0.0",
        "n_games": len(df_v4),
        "feature_count": len(V4_FEATURES),
        "features": V4_FEATURES,
        "meta_columns": META_COLUMNS,
        "sha256": file_hash,
        "statistical_audit": {
            "passed": audit_rep.passed_audit,
            "condition_number": audit_rep.condition_number,
            "max_vif": audit_rep.max_vif,
            "high_correlation_pairs": audit_rep.high_correlation_pairs,
            "zero_variance_features": audit_rep.zero_variance_features,
        },
    }
    V4_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"      Saved {V4_TABLE_PATH} ({len(file_bytes)} bytes, hash {file_hash[:16]}...)")
    print(f"      Saved {V4_MANIFEST_PATH}")


if __name__ == "__main__":
    build_v4_table()
