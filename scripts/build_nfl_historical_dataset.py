"""Build empirical NFL historical dataset using official nflverse game records and PIT features."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# Map from nflverse 3-letter codes to canonical franchise names
NFLVERSE_TEAM_MAP: dict[str, str] = {
    "ARI": "Arizona Cardinals",
    "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",
    "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "HOU": "Houston Texans",
    "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs",
    "LA": "Los Angeles Rams",
    "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders",
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",
    "NE": "New England Patriots",
    "NO": "New Orleans Saints",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "OAK": "Las Vegas Raiders",
    "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",
    "SD": "Los Angeles Chargers",
    "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers",
    "STL": "Los Angeles Rams",
    "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",
    "WAS": "Washington Commanders",
}


def build_nfl_historical_dataset(
    min_season: int = 2016,
    output_path: Path | str | None = None,
    processed_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Ingest nflverse completed games, merge with existing 2026 data, and write clean JSONL."""
    if output_path is None:
        output_path = ROOT / "data/historical/nfl_games_all.jsonl"
    if processed_path is None:
        processed_path = ROOT / "data/processed/nfl/games.jsonl"

    output_file = Path(output_path)
    processed_file = Path(processed_path)

    # 1. Fetch nflverse schedule asset
    url = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.parquet"
    logger.info("Reading nflverse dataset from %s...", url)
    df = pl.read_parquet(url)

    # Filter completed games since min_season
    filtered = (
        df.filter(pl.col("season") >= min_season)
        .filter(pl.col("home_score").is_not_null())
        .filter(pl.col("away_score").is_not_null())
    )

    records: list[dict[str, Any]] = []
    for row in filtered.iter_rows(named=True):
        game_type = str(row.get("game_type") or "REG").upper()
        if game_type in ("PRE", "PRO"):
            continue

        home_raw = str(row.get("home_team") or "")
        away_raw = str(row.get("away_team") or "")
        home_team = NFLVERSE_TEAM_MAP.get(home_raw)
        away_team = NFLVERSE_TEAM_MAP.get(away_raw)
        if not home_team or not away_team:
            continue

        gameday = str(row.get("gameday") or "")
        gametime = str(row.get("gametime") or "13:00")
        if len(gametime) == 5:
            start_utc = f"{gameday}T{gametime}:00Z"
        else:
            start_utc = f"{gameday}T13:00:00Z"

        # Filter out July/August exhibition games
        if gameday:
            try:
                gmonth = int(gameday.split("-")[1])
                if gmonth in (7, 8):
                    continue
            except (ValueError, IndexError):
                pass

        espn_id = str(row.get("espn") or "")
        event_id = espn_id if espn_id and espn_id != "None" else str(row.get("game_id") or "")

        season_type = "postseason" if game_type in ("POST", "SB", "CONF", "DIV", "WC") else "regular"
        is_neutral = str(row.get("location") or "").lower() == "neutral"

        rec: dict[str, Any] = {
            "event_id": event_id,
            "event_start_utc": start_utc,
            "league": "NFL",
            "away_team": away_team,
            "home_team": home_team,
            "away_score": int(row.get("away_score") or 0),
            "home_score": int(row.get("home_score") or 0),
            "season_type": season_type,
            "season_year": int(row.get("season") or 0),
            "competition_type": "STD",
            "status": "completed",
            "is_neutral_site": is_neutral,
            "home_qb_name": str(row.get("home_qb_name") or ""),
            "away_qb_name": str(row.get("away_qb_name") or ""),
            "home_rest_days": int(row.get("home_rest")) if row.get("home_rest") is not None else None,
            "away_rest_days": int(row.get("away_rest")) if row.get("away_rest") is not None else None,
            "spread_line": float(row.get("spread_line")) if row.get("spread_line") is not None else None,
            "total_line": float(row.get("total_line")) if row.get("total_line") is not None else None,
            "roof": str(row.get("roof") or ""),
            "surface": str(row.get("surface") or ""),
            "temp": int(row.get("temp")) if row.get("temp") is not None else None,
            "wind": int(row.get("wind")) if row.get("wind") is not None else None,
        }
        records.append(rec)

    # 2. Merge existing recent games from output_file if present (preserving any newer 2026 games)
    existing_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    if output_file.exists():
        with output_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    # Filter out preseason and exhibition games
                    st = str(item.get("season_type") or "").lower()
                    if st in {"preseason", "all-star", "exhibition"}:
                        continue
                    gday = str(item.get("event_start_utc") or "")[:10]
                    if gday and len(gday) >= 7:
                        try:
                            month = int(gday.split("-")[1])
                            if month in (7, 8):
                                continue
                        except (ValueError, IndexError):
                            pass
                    h = str(item.get("home_team") or "")
                    a = str(item.get("away_team") or "")
                    if h in {"AFC", "NFC", "AFC All-Stars", "NFC All-Stars"} or a in {
                        "AFC",
                        "NFC",
                        "AFC All-Stars",
                        "NFC All-Stars",
                    }:
                        continue
                    if h == "Washington":
                        h = "Washington Commanders"
                        item["home_team"] = h
                    if a == "Washington":
                        a = "Washington Commanders"
                        item["away_team"] = a
                    if gday and h and a:
                        existing_by_key[(gday, h, a)] = item
                except (json.JSONDecodeError, KeyError):
                    continue

    merged_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in records:
        gday = str(r["event_start_utc"])[:10]
        merged_by_key[(gday, r["home_team"], r["away_team"])] = r

    # Keep any existing games that weren't in nflverse (e.g. current live-season 2026 games)
    for key, item in existing_by_key.items():
        if key not in merged_by_key:
            merged_by_key[key] = item

    all_sorted = sorted(merged_by_key.values(), key=lambda x: str(x.get("event_start_utc") or ""))

    # Write out to historical and processed paths
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as f:
        for r in all_sorted:
            f.write(json.dumps(r) + "\n")

    processed_file.parent.mkdir(parents=True, exist_ok=True)
    with processed_file.open("w", encoding="utf-8") as f:
        for r in all_sorted:
            f.write(json.dumps(r) + "\n")

    print(f"Successfully wrote {len(all_sorted)} NFL games to {output_file} and {processed_file}.")
    return all_sorted


if __name__ == "__main__":
    build_nfl_historical_dataset()
