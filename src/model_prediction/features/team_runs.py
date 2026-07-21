"""Shared team run-environment primitives.

One definition used by BOTH the validation pipeline and forward inference so
a feature name always means the same quantity at train and serve time. The
``pitcher_era_gap`` moneyline feature is, and has always been at training
time, the gap in team runs allowed per game over the last N games — a
team-level pitching proxy. (The name is historical; renaming it would break
every existing artifact's feature_names.)
"""

from __future__ import annotations

from typing import Sequence


def rolling_runs_allowed(history: Sequence, team: str, n: int = 5) -> float | None:
    """Runs allowed per game over the team's last ``n`` prior games, or None."""
    team_games = [
        game
        for game in history
        if (game.home_team == team or game.away_team == team)
        and game.home_score is not None
        and game.away_score is not None
    ]
    if len(team_games) < n:
        return None
    recent = team_games[-n:]
    total = 0.0
    for game in recent:
        total += float(game.away_score if game.home_team == team else game.home_score)
    return total / n


def pitcher_era_gap_from_history(history: Sequence, home_team: str, away_team: str) -> float:
    """Home-minus-away rolling runs-allowed gap; 0.0 when either side lacks history."""
    home = rolling_runs_allowed(history, home_team, 5)
    away = rolling_runs_allowed(history, away_team, 5)
    return round(home - away, 4) if home is not None and away is not None else 0.0
