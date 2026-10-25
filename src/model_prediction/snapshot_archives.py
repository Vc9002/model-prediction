"""Discover canonical and historical shared basketball quote archives."""

from pathlib import Path

from .runtime_paths import RuntimePaths


def snapshot_paths(data_root, sport: str, game_date: str):
    paths_config = RuntimePaths.resolve()
    root = Path(data_root)
    # Only reroute the checkout's legacy data root. Explicit isolated roots
    # (including test fixtures) must never borrow production market data.
    if root.resolve() == (paths_config.repo_root / "data").resolve():
        root = paths_config.runtime_root
    base = root / "odds"
    paths = [base / sport.lower() / game_date / "polymarket_snapshots.jsonl"]
    if sport.lower() in {"nba", "wnba", "ncaab", "wncaab"}:
        paths.append(base / "basketball" / game_date / "polymarket_snapshots.jsonl")
    return [path for path in paths if path.is_file()]
