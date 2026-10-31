"""Dedicated NBA Over/Under Total Points Model v6.

Completely independent from the point spread engine. Focuses exclusively on
matchup possession projection, offensive and defensive efficiency trends,
schedule density fatigue, and overtime inflation adjustments.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import erf, isnan, sqrt
from typing import Any

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase
from .basketball import UpcomingGame

NBA_TOTAL_V6_VERSION = "nba-total-margin-v6"


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if isnan(x) or isnan(mean) or isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    return max(0.0001, min(0.9999, raw))


class NBATotalModelV6:
    """Independent NBA Over/Under Total Points prediction engine."""

    def __init__(
        self,
        total_sd: float = 17.8,
        baseline_points_per_team: float = 114.2,
        league_pace: float = 99.2,
        trend_total_weight: float = 0.85,
        team_total_weight: float = 0.15,
        dynamic_matchup_pace: bool = True,
        b2b_pace_drag: float = 0.8,
        schedule_density_drag: bool = True,
        overtime_risk_adjustment: bool = True,
        offseason_gap_days: float = 60.0,
        offseason_retention: float = 0.40,
        isolate_postseason: bool = True,
        version: str = NBA_TOTAL_V6_VERSION,
    ) -> None:
        self.total_sd = total_sd
        self.baseline_points_per_team = baseline_points_per_team
        self.league_pace = league_pace
        self.trend_total_weight = trend_total_weight
        self.team_total_weight = team_total_weight
        self.dynamic_matchup_pace = dynamic_matchup_pace
        self.b2b_pace_drag = b2b_pace_drag
        self.schedule_density_drag = schedule_density_drag
        self.overtime_risk_adjustment = overtime_risk_adjustment
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.version = version
        self.sport = "nba"
        self.league = "NBA"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NBATotalModelV6:
        return cls(
            total_sd=float(artifact.get("total_sd", 17.8)),
            baseline_points_per_team=float(artifact.get("baseline_points_per_team", 114.2)),
            league_pace=float(artifact.get("league_pace", 99.2)),
            trend_total_weight=float(artifact.get("trend_total_weight", 0.85)),
            team_total_weight=float(artifact.get("team_total_weight", 0.15)),
            dynamic_matchup_pace=bool(artifact.get("dynamic_matchup_pace", True)),
            b2b_pace_drag=float(artifact.get("b2b_pace_drag", 0.8)),
            schedule_density_drag=bool(artifact.get("schedule_density_drag", True)),
            overtime_risk_adjustment=bool(artifact.get("overtime_risk_adjustment", True)),
            offseason_gap_days=float(artifact.get("offseason_gap_days", 60.0)),
            offseason_retention=float(artifact.get("offseason_retention", 0.40)),
            isolate_postseason=bool(artifact.get("isolate_postseason", True)),
            version=str(artifact.get("model_version", NBA_TOTAL_V6_VERSION)),
        )

    def predict_totals(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )
        baseline = trend.league_baseline or self.baseline_points_per_team

        predictions: list[GamePrediction] = []

        for game in upcoming:
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)
            sample = min(away_trend.games_played, home_trend.games_played)

            # Efficiency ratings (per 100 possessions)
            away_off = away_trend.offense["hl10"] if away_trend.games_played > 0 else baseline
            away_def = away_trend.defense["hl10"] if away_trend.games_played > 0 else baseline
            home_off = home_trend.offense["hl10"] if home_trend.games_played > 0 else baseline
            home_def = home_trend.defense["hl10"] if home_trend.games_played > 0 else baseline

            away_points = (away_off + home_def) / 2.0
            home_points = (home_off + away_def) / 2.0

            # Rest disparity & B2B detection
            game_start_dt = None
            try:
                game_start_dt = parse_utc(game.event_start_utc)
            except (ValueError, TypeError):
                pass

            home_prev = [g for g in history if g.home_team == game.home_team or g.away_team == game.home_team]
            away_prev = [g for g in history if g.home_team == game.away_team or g.away_team == game.away_team]
            if game_start_dt is not None:
                home_prev = [g for g in home_prev if g.start < game_start_dt]
                away_prev = [g for g in away_prev if g.start < game_start_dt]
            home_prev.sort(key=lambda g: g.start)
            away_prev.sort(key=lambda g: g.start)

            home_b2b = False
            away_b2b = False
            try:
                home_rest = (
                    (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0
                    if (game_start_dt and home_prev)
                    else 3.0
                )
                away_rest = (
                    (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0
                    if (game_start_dt and away_prev)
                    else 3.0
                )
                home_b2b = home_rest <= 1.25
                away_b2b = away_rest <= 1.25
            except (ValueError, TypeError):
                pass

            home_3in4 = False
            away_3in4 = False
            if self.schedule_density_drag and game_start_dt is not None:
                h_4d = [g for g in home_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
                a_4d = [g for g in away_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
                home_3in4 = len(h_4d) >= 2
                away_3in4 = len(a_4d) >= 2

            # Matchup pace calculation
            est_possessions = self.league_pace
            if self.dynamic_matchup_pace:
                pf_away = (
                    1.0 + 0.50 * (((away_off + away_def) / (2.0 * baseline)) - 1.0)
                    if away_trend.games_played > 0
                    else 1.0
                )
                pf_home = (
                    1.0 + 0.50 * (((home_off + home_def) / (2.0 * baseline)) - 1.0)
                    if home_trend.games_played > 0
                    else 1.0
                )
                pace_drag = -self.b2b_pace_drag if (home_b2b or away_b2b) else 0.0
                est_possessions = max(80.0, (self.league_pace * pf_away * pf_home) + pace_drag)

            # Combined base total
            expected_total = (away_points + home_points) * (est_possessions / self.league_pace)

            # Schedule fatigue drag on total
            if (home_3in4 or away_3in4) and self.schedule_density_drag:
                expected_total -= 0.60
            if home_b2b and away_b2b:
                expected_total -= 1.00

            # Overtime close game adjustment
            margin_proxy = abs(home_points - away_points)
            if self.overtime_risk_adjustment and margin_proxy <= 3.5:
                expected_total += 1.20

            # Total SD scaling with possessions
            effective_total_sd = max(
                13.0, min(24.0, self.total_sd * sqrt(est_possessions / self.league_pace))
            )
            uncertainty = max(0.03, min(0.20, 0.20 - 0.005 * sample))

            base: GamePredictionBase = {
                "event_id": game.event_id,
                "event_start_utc": game.event_start_utc,
                "league": self.league,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.version,
                "feature_basis": {
                    "away_points_hl10": round(away_points, 2),
                    "home_points_hl10": round(home_points, 2),
                    "est_possessions": round(est_possessions, 1),
                    "expected_total": round(expected_total, 2),
                    "effective_total_sd": round(effective_total_sd, 2),
                    "home_b2b": home_b2b,
                    "away_b2b": away_b2b,
                    "history_games": len(history),
                },
            }

            if game.total_line is not None:
                under_prob = _normal_cdf(game.total_line, expected_total, effective_total_sd)
                over_prob = 1.0 - under_prob
                predictions.append(
                    GamePrediction(
                        market_type="total",
                        inference_inputs={
                            "mean": expected_total,
                            "sd": effective_total_sd,
                            "line": game.total_line,
                        },
                        line=game.total_line,
                        probabilities={
                            "over": round(over_prob, 6),
                            "under": round(under_prob, 6),
                        },
                        rationale=f"Projected total {expected_total:.1f} (poss {est_possessions:.1f}), pace-scaled sd {effective_total_sd:.1f}.",
                        **base,
                    )
                )

        return predictions
