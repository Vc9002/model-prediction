"""Generate current_model_baselines.parquet from production ledger data.

Reads all production Excel ledgers using the existing xlsx_ledger reader,
extracts model predictions and outcomes, and saves a single Parquet file
with one row per settled pick.
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from model_prediction.xlsx_ledger import read_xlsx_rows

LEDGER_DIRS = ["data/main", "data/flat", "data/research", "data/gated_research"]
SPORTS = ["mlb", "nba", "wnba", "nfl", "soccer", "tennis"]


def extract_picks(xlsx_path: Path) -> list[dict]:
    """Extract pick rows from an Excel ledger using the proper reader."""
    try:
        _headers, rows_data = read_xlsx_rows(xlsx_path)
    except (ValueError, KeyError, IOError):
        return []

    if not rows_data:
        return []

    rows = []
    for row_dict in rows_data:
        try:
            status = str(row_dict.get("status", "")).lower()
            if "settled" not in status:
                continue

            sport_raw = str(row_dict.get("league", "") or row_dict.get("sport", ""))
            rows.append({
                "pick_id": str(row_dict.get("pick_id", "")),
                "event_id": str(row_dict.get("event_id", "")),
                "sport": sport_raw.lower(),
                "market_type": str(row_dict.get("market_type", "")),
                "selection": str(row_dict.get("selection", "")),
                "model_probability": float(row_dict.get("model_probability", 0) or 0),
                "american_odds": int(float(row_dict.get("american_odds", 0) or 0)),
                "model_version": str(row_dict.get("model_version", "")),
                "result": str(row_dict.get("result", "")),
                "units": float(row_dict.get("units", 0) or 0),
                "pnl": float(row_dict.get("pnl", 0) or 0),
                "event_start_utc": str(row_dict.get("event_start_utc", "")),
                "observed_at_utc": str(row_dict.get("observed_at_utc", "")),
                "model_artifact_hash": str(row_dict.get("model_artifact_hash", "")),
            })
        except (ValueError, KeyError, TypeError):
            continue

    return rows


def main() -> None:
    all_picks: list[dict] = []

    for ledger_dir in LEDGER_DIRS:
        base = Path(ledger_dir)
        if not base.exists():
            continue

        # Main sport ledgers
        for sport in SPORTS:
            path = base / f"{sport}.xlsx"
            if path.exists():
                picks = extract_picks(path)
                if picks:
                    all_picks.extend(picks)

        # Model-specific ledgers
        model_dir = base / "model_ledgers"
        if model_dir.exists():
            for path in model_dir.glob("*.xlsx"):
                picks = extract_picks(path)
                if picks:
                    all_picks.extend(picks)

    if not all_picks:
        print("No settled picks found in production ledgers.")
        # Create empty baseline with schema
        empty = pl.DataFrame(schema={
            "pick_id": pl.Utf8, "event_id": pl.Utf8, "sport": pl.Utf8,
            "market_type": pl.Utf8, "selection": pl.Utf8,
            "model_probability": pl.Float64, "american_odds": pl.Int64,
            "model_version": pl.Utf8, "result": pl.Utf8,
            "units": pl.Float64, "pnl": pl.Float64,
            "event_start_utc": pl.Utf8, "observed_at_utc": pl.Utf8,
            "model_artifact_hash": pl.Utf8,
        })
        empty.write_parquet("outputs/rebuild/current_model_baselines.parquet")
        print("Wrote empty baseline (no settled picks found).")
        return

    df = pl.DataFrame(all_picks)
    output_path = Path("outputs/rebuild/current_model_baselines.parquet")
    df.write_parquet(output_path)

    # Summary
    by_sport = df.group_by("sport").agg(pl.len().alias("picks"))
    print(f"Wrote {df.height} settled picks to {output_path}")
    print(f"Sports: {sorted(df['sport'].unique().to_list())}")
    print("Picks by sport:")
    for row in by_sport.sort("picks", descending=True).iter_rows(named=True):
        print(f"  {row['sport']}: {row['picks']}")

    # Model versions
    by_model = df.group_by("model_version").agg(pl.len().alias("picks"))
    print("Picks by model:")
    for row in by_model.sort("picks", descending=True).iter_rows(named=True):
        print(f"  {row['model_version']}: {row['picks']}")


if __name__ == "__main__":
    main()
