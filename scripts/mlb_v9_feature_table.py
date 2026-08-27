"""Freeze the MLB v9 research feature table (immutable dataset contract).

Builds ``outputs/research/mlb_v9/tables/mlb_v9_feature_table_v1.parquet`` from
the full walk-forward dataset with explicit identity columns, split assignments,
availability flags, and a hard-pinned JSON manifest with SHA-256 integrity hashes.
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
from model_prediction.validation import build_walk_forward_rows

BASE_OUT_DIR = PROJECT_ROOT / "outputs" / "research" / "mlb_v9"
TABLES_DIR = BASE_OUT_DIR / "tables"
MANIFESTS_DIR = BASE_OUT_DIR / "manifests"
COHORTS_DIR = BASE_OUT_DIR / "cohorts"
GAMES_PATH = PROJECT_ROOT / "data" / "historical" / "mlb_games_all.jsonl"

COLUMNS = [
    "date",
    "event_id",
    "outcome",
    "elo_probability",
    "trend_gap",
    "park_factor",
    "weather_factor",
    "park_available",
    "weather_available",
    "park_factor_pit",
    "elo_neutral_probability",
    "trailing_home_win_rate_30d",
    "trailing_home_games_30d",
    "residual_trend_gap",
    "defensive_trend_gap",
    "pitcher_era_gap",
    "starter_era_gap",
    "starter_fip_gap",
    "starter_kbb_gap",
    "probable_starter_era_gap",
    "probable_starter_available",
    "bullpen_weakness_gap",
    "bullpen_available",
    "bullpen_fatigue_gap",
    "bullpen_fatigue_available",
    "consistency_gap",
    "hot_cold_gap",
    "rest_disparity",
    "back_to_back_gap",
    "games_last_7_gap",
    "schedule_available",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_obj(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def main() -> int:
    print("[1/4] Building walk-forward rows from feature store ...")
    store = FeatureStore(PROJECT_ROOT / "data")
    rows = build_walk_forward_rows(store, "mlb")
    print(f"      {len(rows)} raw walk-forward rows")

    print("[2/4] Joining event identity, scores, and ET dates ...")
    game_meta: dict[str, dict] = {}
    for line in GAMES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            game = json.loads(line)
        except json.JSONDecodeError:
            continue
        game_meta[str(game.get("event_id"))] = game

    contract = v8_contract()
    train_end = contract["train_end"]
    val_end = contract["validation_end"]

    records = []
    train_ids, val_ids, test_ids = [], [], []

    for row in rows:
        meta = game_meta.get(str(row.event_id)) or {}
        start_utc = meta.get("event_start_utc") or f"{row.date}T00:00:00Z"
        try:
            dt = datetime.fromisoformat(start_utc)
            date_et = dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        except (ValueError, TypeError):
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

        # Availability must strictly come from boolean flags, never numeric gap != 0
        starter_avail = bool(
            getattr(row, "probable_starter_available", False) or getattr(row, "starter_available", False)
        )
        bullpen_avail = bool(getattr(row, "bullpen_available", False))
        weather_avail = bool(getattr(row, "weather_available", False))
        park_avail = bool(getattr(row, "park_available", False))

        rec = {
            "event_id": str(row.event_id),
            "game_start_utc": start_utc,
            "decision_time_utc": start_utc,
            "date_et": date_et,
            "home_team_id": str(meta.get("home_team", "")),
            "away_team_id": str(meta.get("away_team", "")),
            "home_score": home_score,
            "away_score": away_score,
            "home_win": int(row.outcome),
            "split": split,
            "starter_available": starter_avail,
            "bullpen_available": bullpen_avail,
            "weather_available": weather_avail,
            "park_available": park_avail,
            **{name: getattr(row, name) for name in COLUMNS if name not in ("date", "event_id", "outcome")},
        }
        records.append(rec)

    frame = pl.DataFrame(records)
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    COHORTS_DIR.mkdir(parents=True, exist_ok=True)

    parquet_path = TABLES_DIR / "mlb_v9_feature_table_v1.parquet"
    frame.write_parquet(parquet_path)
    print(f"[3/4] Wrote {parquet_path} ({len(records)} rows, {frame.width} cols)")

    # Write Cohort Files
    train_ids_path = COHORTS_DIR / "train_event_ids_v1.json"
    val_ids_path = COHORTS_DIR / "validation_event_ids_v1.json"
    test_ids_path = COHORTS_DIR / "research_test_event_ids_v1.json"

    train_ids_path.write_text(json.dumps(train_ids, indent=2) + "\n", encoding="utf-8")
    val_ids_path.write_text(json.dumps(val_ids, indent=2) + "\n", encoding="utf-8")
    test_ids_path.write_text(json.dumps(test_ids, indent=2) + "\n", encoding="utf-8")

    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        git_sha = "unknown"

    manifest = {
        "schema_version": "mlb-v9-feature-table-v1",
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "builder_git_sha": git_sha,
        "dataset_sha256": sha256_file(parquet_path),
        "schema_sha256": hashlib.sha256(
            json.dumps(sorted([(c, str(t)) for c, t in frame.schema.items()]), sort_keys=True).encode()
        ).hexdigest(),
        "train_event_ids_sha256": sha256_file(train_ids_path),
        "validation_event_ids_sha256": sha256_file(val_ids_path),
        "research_test_event_ids_sha256": sha256_file(test_ids_path),
        "rows": {
            "total": len(records),
            "train": len(train_ids),
            "validation": len(val_ids),
            "research_test": len(test_ids),
        },
        "features": sorted(
            [
                c
                for c in frame.columns
                if c
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
                )
            ]
        ),
        "sources": {"mlb_games_all.jsonl": sha256_file(GAMES_PATH)},
        "missingness_policy": {
            "starter_available": "Boolean flag indicating pitcher data presence",
            "bullpen_available": "Boolean flag indicating bullpen data presence",
            "weather_available": "Boolean flag indicating weather data presence",
            "park_available": "Boolean flag indicating park factor presence",
        },
    }

    manifest_path = MANIFESTS_DIR / "mlb_v9_feature_table_v1.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[4/4] Wrote manifest to {manifest_path}")
    print(f"      Train: {len(train_ids)} | Val: {len(val_ids)} | Research Test: {len(test_ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
