"""Soccer form features: goal rates, over-2.5 and BTTS frequencies, form points.

xG and possession require the football-data source to be cached; when absent
those fields report ``unavailable_from_source`` rather than a fabricated value.
"""

from __future__ import annotations

from typing import Any

from .base import FeatureContext, register_feature
from .trends import ewm_level


@register_feature("soccer_form")
def soccer_form_snapshot(context: FeatureContext) -> dict[str, Any]:
    by_team: dict[str, dict[str, list[float]]] = {}
    league_goals: list[float] = []
    for game in context.games:
        league_goals.extend([float(game.away_score), float(game.home_score)])
        over25 = 1.0 if game.total > 2.5 else 0.0
        btts = 1.0 if game.away_score > 0 and game.home_score > 0 else 0.0
        for team, scored, allowed, points in (
            (
                game.away_team,
                game.away_score,
                game.home_score,
                3.0 if game.away_score > game.home_score else 1.0 if game.away_score == game.home_score else 0.0,
            ),
            (
                game.home_team,
                game.home_score,
                game.away_score,
                3.0 if game.home_score > game.away_score else 1.0 if game.home_score == game.away_score else 0.0,
            ),
        ):
            entry = by_team.setdefault(
                team, {"scored": [], "allowed": [], "points": [], "over25": [], "btts": []}
            )
            entry["scored"].append(float(scored))
            entry["allowed"].append(float(allowed))
            entry["points"].append(points)
            entry["over25"].append(over25)
            entry["btts"].append(btts)
    baseline = sum(league_goals) / len(league_goals) if league_goals else 1.3
    teams = {}
    for team, entry in sorted(by_team.items()):
        recent = entry["points"][-5:]
        teams[team] = {
            "games": len(entry["scored"]),
            "attack_strength": round(ewm_level(entry["scored"], 10.0, baseline, 8.0) / baseline, 6)
            if baseline
            else 1.0,
            "defense_weakness": round(ewm_level(entry["allowed"], 10.0, baseline, 8.0) / baseline, 6)
            if baseline
            else 1.0,
            "form_points_last5": sum(recent),
            "over25_rate": round(sum(entry["over25"]) / len(entry["over25"]), 6),
            "btts_rate": round(sum(entry["btts"]) / len(entry["btts"]), 6),
            "xg_status": "unavailable_from_source",
            "possession_status": "unavailable_from_source",
        }
    return {"league_goals_per_team": round(baseline, 6), "teams": teams}
