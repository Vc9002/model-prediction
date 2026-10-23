"""Discover canonical and historical shared basketball quote archives."""

from pathlib import Path


def snapshot_paths(data_root, sport: str, game_date: str):
    base = Path(data_root) / "odds"
    paths = [base / sport.lower() / game_date / "polymarket_snapshots.jsonl"]
    if sport.lower() in {"nba", "wnba", "ncaab", "wncaab"}:
        paths.append(base / "basketball" / game_date / "polymarket_snapshots.jsonl")
    return [path for path in paths if path.is_file()]
