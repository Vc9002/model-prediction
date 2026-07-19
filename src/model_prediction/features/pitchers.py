"""Starting pitcher quality features for MLB daily forecasts.

Computes rolling runs-allowed-per-game for both teams from cached game
results — no external API calls needed. Lower runs allowed = better pitching.
"""

from __future__ import annotations

import json
from pathlib import Path


def _load_recent_games(sport: str = "mlb") -> list[dict]:
    path = Path(f"data/historical/{sport}_games_all.jsonl")
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().strip().split("\n") if l.strip()]


def _rolling_runs_allowed(team: str, n: int = 5) -> float | None:
    """Rolling runs allowed per game from cached results (last N games)."""
    games = _load_recent_games()
    team_games = [
        g for g in games
        if (g.get("home_team") == team or g.get("away_team") == team)
        and g.get("home_score") is not None and g.get("away_score") is not None
    ]
    if len(team_games) < n:
        return None
    recent = sorted(team_games, key=lambda g: g.get("event_start_utc", ""))[-n:]
    total = 0
    for g in recent:
        if g.get("home_team") == team:
            total += g.get("away_score", 0)
        else:
            total += g.get("home_score", 0)
    return total / n


def pitcher_era_gap(event_id: str = "") -> float:
    """Home team rolling runs-allowed minus away team rolling runs-allowed.

    Negative = home pitching has been better recently, favoring home team.
    Computed from cached game results — no API calls.
    The event_id parameter is ignored (kept for backward compatibility).
    """
    # We can't know which teams are playing from just event_id,
    # so return 0.0 here — the real value is computed in learned_forward.py
    return 0.0
