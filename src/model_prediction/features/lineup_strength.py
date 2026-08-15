"""Rolling team offensive/defensive ratings (points scored/allowed vs league).

True lineup data (who is actually playing tonight) is not cached yet; this
module provides the team-level rolling strength ratings and reports lineup
availability honestly as ``unavailable_from_source``.
"""

from __future__ import annotations

from typing import Any

from .base import FeatureContext, register_feature
from .trends import ewm_level


@register_feature("lineup_strength")
def lineup_strength_snapshot(context: FeatureContext) -> dict[str, Any]:
    by_team: dict[str, dict[str, list[float]]] = {}
    league_points: list[float] = []
    for game in context.games:
        league_points.extend([float(game.away_score), float(game.home_score)])
        for team, scored, allowed in (
            (game.away_team, game.away_score, game.home_score),
            (game.home_team, game.home_score, game.away_score),
        ):
            entry = by_team.setdefault(team, {"scored": [], "allowed": []})
            entry["scored"].append(float(scored))
            entry["allowed"].append(float(allowed))
    baseline = sum(league_points) / len(league_points) if league_points else 0.0
    teams = {}
    for team, entry in sorted(by_team.items()):
        offense = ewm_level(entry["scored"], 10.0, baseline, 12.0)
        defense = ewm_level(entry["allowed"], 10.0, baseline, 12.0)
        teams[team] = {
            "offensive_rating": round(offense / baseline, 6) if baseline else 1.0,
            "defensive_rating": round(defense / baseline, 6) if baseline else 1.0,
            "games": len(entry["scored"]),
            "lineup_status": "unavailable_from_source",
        }
    return {"league_baseline": round(baseline, 6), "teams": teams}
