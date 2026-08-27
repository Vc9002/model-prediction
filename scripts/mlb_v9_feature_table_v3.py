"""Freeze the MLB v9 research feature table v3 (Real Point-in-Time Feature Engineering Layer).

Builds outputs/research/mlb_v9/tables/mlb_v9_feature_table_v3.parquet containing:
1. Identity columns & split assignments (Train / Validation / Research Test).
2. Baseline v8 control information (Elo, trend, park factor, rest disparity).
3. Real empirical-Bayes projected offense (wOBA, K%, BB%, ISO).
4. Real starter-state vectors (K%, BB%, K-BB%, CSW%, xwOBA allowed, velo, expected IP depth).
5. Canonical dynamic bullpen state (Effective FIP gap, freshness/availability gap, high-leverage gap).
6. Real batter-level platoon splits against starting pitcher handedness.
7. Explicit source availability flags.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/mlb_v9_feature_table_v3.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import polars as pl
from mlb_research_common import v8_contract

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.features.batter_priors import BatterPriorEngine
from model_prediction.features.bullpen_state import PointInTimeBullpenEngine
from model_prediction.features.mlb_v9_features import load_probable_starter_index
from model_prediction.features.park_factors_pit import park_factor_at
from model_prediction.features.platoon_matchup import compute_lineup_platoon_matchup
from model_prediction.features.projected_offense import projected_offense_matchup_gaps
from model_prediction.features.starter_state import starter_state_matchup_gaps
from model_prediction.validation import build_walk_forward_rows

BASE_OUT_DIR = PROJECT_ROOT / "outputs" / "research" / "mlb_v9"
TABLES_DIR = BASE_OUT_DIR / "tables"
MANIFESTS_DIR = BASE_OUT_DIR / "manifests"
COHORTS_DIR = BASE_OUT_DIR / "cohorts"
REPORTS_DIR = BASE_OUT_DIR / "reports"
GAMES_PATH = PROJECT_ROOT / "data" / "historical" / "mlb_games_all.jsonl"
DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "mlb_statsapi" / "game_snapshots.jsonl"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_obj(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def get_builder_git_sha() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(PROJECT_ROOT),
        )
        return res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def build_v3_table() -> None:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    COHORTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/5] Building walk-forward rows from FeatureStore ...")
    store = FeatureStore(PROJECT_ROOT / "data")
    rows = build_walk_forward_rows(store, "mlb")
    print(f"      {len(rows)} raw walk-forward rows")

    print("[2/5] Initializing Point-in-Time feature engines ...")
    bp_engine = PointInTimeBullpenEngine(snapshot_path=DEFAULT_SNAPSHOT_PATH)
    batter_engine = BatterPriorEngine(snapshot_path=DEFAULT_SNAPSHOT_PATH)

    print("[2.5/5] Loading probable-starter crosswalk from snapshots ...")
    starter_lookup = load_probable_starter_index(DEFAULT_SNAPSHOT_PATH)

    print("[3/5] Loading game identity metadata and starters ...")
    game_meta: dict[str, dict] = {}
    if GAMES_PATH.exists():
        for line in GAMES_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                game = json.loads(line)
                game_meta[str(game.get("event_id"))] = game
            except json.JSONDecodeError:
                continue

    contract = v8_contract()
    train_end = contract["train_end"]
    val_end = contract["validation_end"]

    print("[4/5] Extracting true Point-in-Time v9 features for all games ...")
    records = []
    train_ids, val_ids, test_ids = [], [], []

    for idx, row in enumerate(rows):
        if (idx + 1) % 1000 == 0 or idx == len(rows) - 1:
            print(f"      Processed {idx + 1}/{len(rows)} games ...")
        meta = game_meta.get(str(row.event_id)) or {}
        start_utc = meta.get("event_start_utc") or f"{row.date}T00:00:00Z"
        try:
            clean_utc = start_utc.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            date_et = dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        except (ValueError, TypeError):
            dt = datetime(int(row.date[:4]), int(row.date[5:7]), int(row.date[8:10]), 18, 0, tzinfo=UTC)
            date_et = row.date

        if date_et <= train_end:
            split = "train"
            train_ids.append(str(row.event_id))
        elif date_et <= val_end:
            split = "validation"
            val_ids.append(str(row.event_id))
        else:
            split = "research_test"
            test_ids.append(str(row.event_id))

        home_score = int(float(meta.get("home_score", 0) or 0)) if meta.get("home_score") is not None else 0
        away_score = int(float(meta.get("away_score", 0) or 0)) if meta.get("away_score") is not None else 0
        home_team = str(meta.get("home_team") or getattr(row, "home_team", ""))
        away_team = str(meta.get("away_team") or getattr(row, "away_team", ""))

        # Starters. Neither the games file nor the walk-forward rows carry
        # starter identity, so resolve probable starters from the snapshot via
        # the same (start[:16], home, away) crosswalk validation.py uses;
        # without this every starter-state feature fell back to league priors
        # (DEBUG.md 2026-08-26: 6 dead v9 starter columns).
        snap_starters = starter_lookup.get((start_utc[:16], home_team, away_team)) or {}
        home_starter = str(
            meta.get("home_starter_name")
            or snap_starters.get("home_starter_name")
            or getattr(row, "home_starter_name", "")
            or ""
        )
        away_starter = str(
            meta.get("away_starter_name")
            or snap_starters.get("away_starter_name")
            or getattr(row, "away_starter_name", "")
            or ""
        )
        home_throws = str(meta.get("home_starter_throws") or snap_starters.get("home_starter_throws") or "R")
        away_throws = str(meta.get("away_starter_throws") or snap_starters.get("away_starter_throws") or "R")

        # Baseline v8 controls
        elo_prob = float(getattr(row, "elo_probability", 0.535))
        trend_gap = float(getattr(row, "trend_gap", 0.0))
        rest_disp = float(getattr(row, "rest_disparity", 0.0))
        b2b_gap = float(getattr(row, "back_to_back_gap", 0.0))

        # Real PIT Starter state
        starter_gaps = starter_state_matchup_gaps(
            home_starter, away_starter, dt, snapshot_path=DEFAULT_SNAPSHOT_PATH
        )
        starter_avail = bool(
            getattr(row, "probable_starter_available", False)
            or getattr(row, "starter_available", False)
            or bool(home_starter and away_starter)
        )

        # Real PIT Projected offense
        offense_gaps = projected_offense_matchup_gaps(
            batter_engine,
            home_team,
            away_team,
            date_et,
            home_sp_hand=home_throws,
            away_sp_hand=away_throws,
        )

        # Real PIT Bullpen state
        bp_adv = bp_engine.evaluate_matchup(home_team, away_team, date_et)

        # Real PIT Platoon splits. platoon_matchup_gaps() constructs a fresh
        # BatterPriorEngine per game (a full 6,683-line snapshot re-scan --
        # the dominant cost of this builder, making the rebuild take hours).
        # The shared batter_engine has identical content and
        # compute_lineup_platoon_matchup evaluates the same queries with the
        # same as_of date (dt.strftime("%Y-%m-%d"), exactly what the wrapper
        # passes), so this is a pure de-duplication, not a semantic change.
        platoon_raw = compute_lineup_platoon_matchup(
            batter_engine,
            home_team,
            away_team,
            home_throws,
            away_throws,
            dt.strftime("%Y-%m-%d"),
        )
        platoon_gaps = {
            "platoon_woba_advantage": platoon_raw["platoon_woba_gap"],
            "platoon_iso_advantage": platoon_raw["platoon_iso_gap"],
        }

        # PIT Park Factor
        pf_obj = park_factor_at(home_team, date_et)
        park = float(pf_obj.get("park_factor", 1.0)) if isinstance(pf_obj, dict) else float(pf_obj or 1.0)

        records.append(
            {
                "event_id": str(row.event_id),
                "game_start_utc": start_utc,
                "decision_time_utc": start_utc,
                "date_et": date_et,
                "home_team_id": home_team,
                "away_team_id": away_team,
                "home_score": home_score,
                "away_score": away_score,
                "home_win": int(row.outcome),
                "split": split,
                # Baseline Control
                "elo_probability": round(elo_prob, 4),
                "trend_gap": round(trend_gap, 4),
                "park_factor_pit": round(park, 4),
                "rest_disparity": round(rest_disp, 4),
                "back_to_back_gap": round(b2b_gap, 4),
                # Starter State Family
                "starter_k_pct_gap": round(float(starter_gaps.get("starter_k_pct_gap", 0.0)), 4),
                "starter_bb_pct_gap": round(float(starter_gaps.get("starter_bb_pct_gap", 0.0)), 4),
                "starter_k_bb_gap": round(float(starter_gaps.get("starter_k_minus_bb_pct_gap", 0.0)), 4),
                "starter_depth_gap": round(float(starter_gaps.get("starter_depth_gap", 0.0)), 4),
                "home_expected_starter_ip": round(
                    float(starter_gaps.get("home_expected_starter_ip", 5.3)), 2
                ),
                "away_expected_starter_ip": round(
                    float(starter_gaps.get("away_expected_starter_ip", 5.3)), 2
                ),
                "starter_available": starter_avail,
                # Projected Offense Family
                "projected_woba_gap": round(float(offense_gaps.get("projected_offense_quality_gap", 0.0)), 4),
                "projected_iso_gap": round(float(offense_gaps.get("projected_offense_power_gap", 0.0)), 4),
                "projected_k_pct_gap": round(float(offense_gaps.get("projected_offense_k_pct_gap", 0.0)), 4),
                "projected_bb_pct_gap": round(
                    float(offense_gaps.get("projected_offense_bb_pct_gap", 0.0)), 4
                ),
                "home_projected_woba": round(float(offense_gaps.get("home_projected_xwoba", 0.318)), 4),
                "away_projected_woba": round(float(offense_gaps.get("away_projected_xwoba", 0.318)), 4),
                "projected_offense_available": True,
                # Bullpen State Family
                "bullpen_fip_advantage": round(float(bp_adv.fip_gap), 4),
                "bullpen_freshness_advantage": round(float(bp_adv.availability_gap), 4),
                "bullpen_hl_advantage": round(float(bp_adv.high_leverage_avail_gap), 4),
                "home_bullpen_effective_fip": round(float(bp_adv.home_state.available_fip), 4),
                "away_bullpen_effective_fip": round(float(bp_adv.away_state.available_fip), 4),
                "bullpen_available": True,
                # Platoon Family
                "platoon_woba_advantage": round(float(platoon_gaps.get("platoon_woba_advantage", 0.0)), 4),
                "platoon_iso_advantage": round(float(platoon_gaps.get("platoon_iso_advantage", 0.0)), 4),
                "platoon_available": True,
            }
        )

    df = pl.DataFrame(records)
    out_table_path = TABLES_DIR / "mlb_v9_feature_table_v3.parquet"
    df.write_parquet(out_table_path)
    table_hash = sha256_file(out_table_path)

    print(f"[5/5] Saved {len(df)} rows to {out_table_path} (hash: {table_hash[:12]})")

    manifest = {
        "table_name": "mlb_v9_feature_table_v3",
        "table_path": str(out_table_path.relative_to(PROJECT_ROOT)),
        "table_hash": table_hash,
        "row_count": len(df),
        "columns": df.columns,
        "train_rows": len(train_ids),
        "validation_rows": len(val_ids),
        "test_rows": len(test_ids),
        "built_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": get_builder_git_sha(),
        "provenance": {
            "source": "canonical_real_point_in_time_statcast_and_statsapi",
            "statcast_pitcher_metrics": "data/statcast/pitcher_game_metrics.parquet",
            "statcast_batter_metrics": "data/statcast/batter_game_metrics.parquet",
            "bullpen_engine": "src/model_prediction/features/bullpen_state.py",
            "batter_priors": "src/model_prediction/features/batter_priors.py",
            "starter_state": "src/model_prediction/features/starter_state.py",
        },
    }
    manifest_path = MANIFESTS_DIR / "mlb_v9_feature_table_v3.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"      Manifest written to {manifest_path}")

    cohort = {
        "cohort_id": "mlb_v9_cohort_v3",
        "train_event_ids": train_ids,
        "validation_event_ids": val_ids,
        "test_event_ids": test_ids,
    }
    cohort_path = COHORTS_DIR / "mlb_v9_cohort_v3.json"
    cohort_path.write_text(json.dumps(cohort, indent=2), encoding="utf-8")
    print(f"      Cohort written to {cohort_path}")


if __name__ == "__main__":
    build_v3_table()
