"""Decision horizons — early/mid/late feature schemas with separate models and calibrators.

Late information must never be backfilled into early decisions. Each horizon
receives a separate feature schema, model, calibrator, coverage report, and
economic evaluation.

Horizons:
    early  — 24-48 hours before start (lineups unconfirmed, probable starters)
    mid    — 4-8 hours before start (confirmed starters, partial lineups)
    late   — 15-90 minutes before start (final lineups, weather, market depth)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Any


HORIZONS = ("early", "mid", "late")
HORIZON_HOURS_BEFORE = {"early": 36, "mid": 6, "late": 0.5}


@dataclass
class HorizonSpec:
    """What's available at each decision horizon."""
    name: str
    hours_before_start: float
    # Which feature groups are available at this horizon
    available_features: list[str] = field(default_factory=list)
    missing_features: list[str] = field(default_factory=list)
    min_quote_depth: float = 0.0
    max_quote_age_seconds: float = float("inf")


def horizon_specs_for_sport(sport: str) -> dict[str, HorizonSpec]:
    """Return feature availability per horizon for a sport.

    Early: only long-lived information (ratings, season stats, schedules)
    Mid: adds confirmed starters, partial lineups, weather forecasts
    Late: adds final lineups, BBO depth, last-minute injuries
    """
    common_early = ["elo_rating", "trend_momentum", "rest_days", "schedule_load"]
    common_mid = ["confirmed_starters", "lineup_projections", "weather_forecast", "park_factors"]
    common_late = ["final_lineups", "market_depth", "quote_age_seconds", "injury_updates"]

    if sport in ("mlb",):
        return {
            "early": HorizonSpec("early", 36, common_early + ["season_stats", "pitcher_season_era"]),
            "mid": HorizonSpec("mid", 6, common_mid + ["starter_k_bb_pct", "bullpen_availability"]),
            "late": HorizonSpec("late", 0.5, common_late + ["confirmed_batting_order", "wind_vector"]),
        }
    elif sport in ("nba", "wnba"):
        return {
            "early": HorizonSpec("early", 36, common_early + ["team_ortg", "team_drtg", "pace"]),
            "mid": HorizonSpec("mid", 6, common_mid + ["player_availability", "lineup_continuity"]),
            "late": HorizonSpec("late", 0.5, common_late + ["starting_lineup_confirmed", "referee_crew"]),
        }
    elif sport in ("nfl",):
        return {
            "early": HorizonSpec("early", 36, common_early + ["qb_status", "team_epa", "drive_efficiency"]),
            "mid": HorizonSpec("mid", 6, common_mid + ["injury_report", "weather_forecast"]),
            "late": HorizonSpec("late", 0.5, common_late + ["inactive_list", "game_time_weather"]),
        }
    elif sport in ("soccer",):
        return {
            "early": HorizonSpec("early", 36, common_early + ["xg_form", "attack_strength", "defense_strength"]),
            "mid": HorizonSpec("mid", 6, common_mid + ["starting_xi", "goalkeeper_confirmed"]),
            "late": HorizonSpec("late", 0.5, common_late + ["final_lineup", "market_depth"]),
        }
    elif sport in ("tennis",):
        return {
            "early": HorizonSpec("early", 36, common_early + ["surface_elo", "overall_elo"]),
            "mid": HorizonSpec("mid", 6, common_mid + ["serve_pct", "return_pct", "recent_workload"]),
            "late": HorizonSpec("late", 0.5, common_late + ["withdrawal_risk"]),
        }
    else:
        return {
            "early": HorizonSpec("early", 36, common_early),
            "mid": HorizonSpec("mid", 6, common_mid),
            "late": HorizonSpec("late", 0.5, common_late),
        }


def horizon_from_time_to_start(hours_to_start: float) -> str:
    """Map hours-before-start to the appropriate horizon name."""
    if hours_to_start >= 18:
        return "early"
    elif hours_to_start >= 3:
        return "mid"
    return "late"


def validate_horizon_separation(
    early_predictions: list[dict[str, Any]],
    mid_predictions: list[dict[str, Any]],
    late_predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ensure late information hasn't leaked into early predictions.

    Returns a report with any violations found.
    """
    violations: list[str] = []
    early_event_ids = {p.get("event_id") for p in early_predictions}
    mid_event_ids = {p.get("event_id") for p in mid_predictions}
    late_event_ids = {p.get("event_id") for p in late_predictions}

    # Check that predictions have timestamp metadata and that
    # late-only features don't appear in early/mid predictions
    late_only_features = {"confirmed_batting_order", "wind_vector", "final_lineups",
                          "inactive_list", "game_time_weather", "withdrawal_risk"}

    for p in early_predictions:
        features = set(p.get("features_used", []))
        leaked = features & late_only_features
        if leaked:
            violations.append(
                f"Early prediction {p.get('event_id', '?')} contains late-only features: {leaked}"
            )
        observed = p.get("observed_at_utc", "")
        event_start = p.get("event_start_utc", "")
        if observed and event_start and observed > event_start:
            violations.append(
                f"Early prediction {p.get('event_id', '?')} observed {observed} after event start {event_start}"
            )

    for p in mid_predictions:
        features = set(p.get("features_used", []))
        leaked = features & late_only_features
        if leaked:
            violations.append(
                f"Mid prediction {p.get('event_id', '?')} contains late-only features: {leaked}"
            )

    return {
        "horizon_separation_valid": len(violations) == 0,
        "violations": violations,
        "early_events": len(early_event_ids),
        "mid_events": len(mid_event_ids),
        "late_events": len(late_event_ids),
    }
