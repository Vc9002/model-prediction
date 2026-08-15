"""Freeze the MLB v9 research feature table (prep-only tooling).

Builds ``mlb_v9_feature_table.parquet`` from the full walk-forward
dataset (no date cap — the whole history through today) with every
feature column the current codebase can compute, plus availability
flags, plus a manifest:

    dataset_hash        sha256 of the parquet bytes
    feature_schema_hash sha256 of the sorted column list
    source_hashes       sha256 of the games file(s) read
    git_sha             HEAD when the table was built
    created_at          UTC timestamp
    decision_horizon    game-day walk-forward, ET dates

Do NOT use this table to select a model during burn-in; it exists so
post-burn-in v9 research cites one immutable dataset. Features that do
not exist yet (lineup strength, bullpen talent, PIT forecast
temperature/humidity/wind/roof) are added later — any addition changes
feature_schema_hash, which is the point.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import polars as pl  # noqa: E402

from model_prediction.config import PROJECT_ROOT  # noqa: E402
from model_prediction.features.base import FeatureStore  # noqa: E402
from model_prediction.validation import build_walk_forward_rows  # noqa: E402

OUT_DIR = PROJECT_ROOT / "outputs" / "research" / "mlb_v9_feature_table"
GAMES_PATH = PROJECT_ROOT / "data" / "historical" / "mlb_games_all.jsonl"

# Every field ValidationRow exposes (the frozen column contract). New
# features get appended here AND to ValidationRow together.
COLUMNS = [
    "date", "event_id", "outcome", "elo_probability", "trend_gap",
    "park_factor", "weather_factor", "park_available", "weather_available",
    "park_factor_pit", "elo_neutral_probability", "trailing_home_win_rate_30d",
    "trailing_home_games_30d", "residual_trend_gap", "defensive_trend_gap",
    "pitcher_era_gap", "starter_era_gap", "starter_fip_gap", "starter_kbb_gap",
    "probable_starter_era_gap", "probable_starter_available",
    "bullpen_weakness_gap", "bullpen_available", "bullpen_fatigue_gap",
    "bullpen_fatigue_available", "consistency_gap", "hot_cold_gap",
    "rest_disparity", "back_to_back_gap", "games_last_7_gap",
    "schedule_available",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    print("[1/3] Building walk-forward rows (full history, no cap) ...")
    store = FeatureStore(PROJECT_ROOT / "data")
    rows = build_walk_forward_rows(store, "mlb")
    print(f"      {len(rows)} rows")

    print("[2/3] Joining event identity (teams, decision timestamp) ...")
    game_meta: dict[str, dict] = {}
    for line in GAMES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            game = json.loads(line)
        except json.JSONDecodeError:
            continue
        game_meta[game.get("event_id")] = game

    records = []
    missing = 0
    for row in rows:
        meta = game_meta.get(row.event_id) or {}
        records.append(
            {
                "event_id": row.event_id,
                "date": row.date,
                "decision_time_utc": meta.get("event_start_utc") or f"{row.date}T00:00:00Z",
                "home_team": meta.get("home_team"),
                "away_team": meta.get("away_team"),
                "outcome": row.outcome,
                **{name: getattr(row, name) for name in COLUMNS if name not in ("date", "event_id", "outcome")},
            }
        )
        if not meta:
            missing += 1
    print(f"      {missing} rows without game metadata (decision_time defaulted)")

    frame = pl.DataFrame(records)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = OUT_DIR / "mlb_v9_feature_table.parquet"
    frame.write_parquet(parquet_path)
    print(f"[3/3] wrote {parquet_path} ({len(records)} rows, {frame.width} columns)")

    try:
        git_sha = __import__("subprocess").run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — sha is informational
        git_sha = "unknown"

    manifest = {
        "schema_version": "mlb-v9-feature-table-v1",
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "rows": len(records),
        "columns": sorted(frame.columns),
        "dataset_hash": sha256_file(parquet_path),
        "feature_schema_hash": hashlib.sha256(
            json.dumps(sorted(frame.columns)).encode()
        ).hexdigest(),
        "source_hashes": {"mlb_games_all.jsonl": sha256_file(GAMES_PATH)},
        "git_sha": git_sha,
        "decision_horizon": "game-day walk-forward, ET dates; features computed from strictly prior completed games",
        "status": "PREP_ONLY — not to be used for model selection until burn-in passes (2026-08-18)",
        "missing_features_not_yet_built": [
            "lineup_strength_projected", "lineup_strength_confirmed",
            "bullpen_talent", "pit_weather_temperature", "pit_weather_humidity",
            "pit_weather_wind", "pit_weather_roof",
        ],
    }
    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
