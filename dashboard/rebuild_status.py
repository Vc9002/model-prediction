"""Dashboard adapter for rebuild outputs — read-only status view."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

REBUILD_OUTPUTS = Path("outputs/rebuild")
REBUILD_DATA = Path("data/rebuild")


def get_status() -> dict:
    """Return a dashboard-ready status dict for the rebuild system."""
    status: dict = {
        "branch": "rebuild/clean-slate-v1",
        "tests": 755,
        "baseline_picks": 0,
        "sports_with_data": [],
        "collector_status": {},
        "model_artifacts": [],
        "deliverables": [],
        "normalized_tables": {},
        "market_data_exists": False,
    }

    # Baseline parquet
    baseline_path = REBUILD_OUTPUTS / "current_model_baselines.parquet"
    if baseline_path.exists():
        df = pl.read_parquet(str(baseline_path))
        status["baseline_picks"] = df.height
        status["sports_with_data"] = sorted(df["sport"].unique().to_list())

    # Deliverables
    if REBUILD_OUTPUTS.exists():
        status["deliverables"] = sorted(
            f.name for f in REBUILD_OUTPUTS.iterdir() if f.is_file()
        )

    # Normalized data
    norm = REBUILD_DATA / "normalized"
    if norm.exists():
        for sport_dir in sorted(norm.iterdir()):
            if sport_dir.is_dir():
                tables = [p.stem for p in sport_dir.glob("*.parquet")]
                if tables:
                    status["normalized_tables"][sport_dir.name] = tables

    # Market data
    markets = REBUILD_DATA / "markets"
    if markets.exists():
        status["market_data_exists"] = any(markets.iterdir())

    # Collector status
    collector_map = {
        "mlb": "active (ESPN+pybaseball+weather+Polymarket)",
        "nba": "active (ESPN+Polymarket)",
        "wnba": "active (ESPN+Polymarket)",
        "nfl": "active (ESPN+Polymarket)",
        "soccer": "active (ESPN+Polymarket)",
        "tennis": "active (ESPN+Polymarket)",
        "esports": "stub (Polymarket only)",
        "kbo": "production-only (not rebuild)",
        "npb": "production-only (not rebuild)",
    }
    status["collector_status"] = collector_map

    # Model artifacts
    challengers = Path("config/models/challengers")
    if challengers.exists():
        status["model_artifacts"] = sorted(p.name for p in challengers.glob("*.json"))

    return status


if __name__ == "__main__":
    import sys
    status = get_status()
    json.dump(status, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
