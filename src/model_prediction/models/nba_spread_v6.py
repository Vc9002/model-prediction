"""Dedicated NBA Point Spread and Coherent Moneyline Model v6.

Completely independent from the totals model. Focuses strictly on scoring margin,
possession-scaled spread standard deviation, empirical back-to-back fatigue,
and calibrated logistic moneyline conversion.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import erf, isnan, sqrt
from typing import Any

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.elo_ratings import build_elo
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase
from .basketball import UpcomingGame

NBA_SPREAD_V6_VERSION = "nba-spread-margin-v6"


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if isnan(x) or isnan(mean) or isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    return max(0.0001, min(0.9999, raw))


class NBASpreadModelV6:
    """Independent NBA Spread and Moneyline prediction engine."""

    def __init__(
        self,
        margin_sd: float = 14.5,
        elo_weight: float = 0.55,
        trend_weight: float = 0.45,
        rest_weight: float = 0.10,
        home_court_points: float = 1.80,
        b2b_penalty_home: float = 3.20,
        b2b_penalty_away: float = 0.80,
        schedule_density_drag: bool = True,
        dynamic_volatility: bool = True,
        pace_sd_scaling: bool = True,
        use_logistic_moneyline: bool = True,
        logistic_scale: float = 11.8,
        offseason_gap_days: float = 60.0,
        offseason_retention: float = 0.40,
        isolate_postseason: bool = True,
        elo_home_advantage: float = 38.0,
        version: str = NBA_SPREAD_V6_VERSION,
    ) -> None:
        self.margin_sd = margin_sd
        self.elo_weight = elo_weight
        self.trend_weight = trend_weight
        self.rest_weight = rest_weight
        self.home_court_points = home_court_points
        self.b2b_penalty_home = b2b_penalty_home
        self.b2b_penalty_away = b2b_penalty_away
        self.schedule_density_drag = schedule_density_drag
        self.dynamic_volatility = dynamic_volatility
        self.pace_sd_scaling = pace_sd_scaling
        self.use_logistic_moneyline = use_logistic_moneyline
        self.logistic_scale = logistic_scale
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.elo_home_advantage = elo_home_advantage
        self.version = version
        self.sport = "nba"
        self.league = "NBA"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NBASpreadModelV6:
        return cls(
            margin_sd=float(artifact.get("margin_sd", 14.5)),
            elo_weight=float(artifact.get("elo_weight", 0.55)),
            trend_weight=float(artifact.get("trend_weight", 0.45)),
            rest_weight=float(artifact.get("rest_weight", 0.10)),
            home_court_points=float(artifact.get("home_court_points", 1.80)),
            b2b_penalty_home=float(artifact.get("b2b_penalty_home", 3.20)),
            b2b_penalty_away=float(artifact.get("b2b_penalty_away", 0.80)),
            schedule_density_drag=bool(artifact.get("schedule_density_drag", True)),
            dynamic_volatility=bool(artifact.get("dynamic_volatility", True)),
            pace_sd_scaling=bool(artifact.get("pace_sd_scaling", True)),
            use_logistic_moneyline=bool(artifact.get("use_logistic_moneyline", True)),
            logistic_scale=float(artifact.get("logistic_scale", 11.8)),
            offseason_gap_days=float(artifact.get("offseason_gap_days", 60.0)),
            offseason_retention=float(artifact.get("offseason_retention", 0.40)),
            isolate_postseason=bool(artifact.get("isolate_postseason", True)),
            elo_home_advantage=float(artifact.get("elo_home_advantage", 38.0)),
            version=str(artifact.get("model_version", NBA_SPREAD_V6_VERSION)),
        )

    def predict_spreads(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        gap_days = int(self.offseason_gap_days) if self.offseason_gap_days is not None else 60
        elo = build_elo(
            history,
            "nba",
            offseason_gap_days=gap_days,
            home_advantage=self.elo_home_advantage,
        )
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )
        baseline = trend.league_baseline or 114.2
        league_pace = 99.2

        predictions: list[GamePrediction] = []

        for game in upcoming:
            home_win = elo.expected_home_win(game.home_team, game.away_team)
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)
            sample = min(away_trend.games_played, home_trend.games_played)

            away_points = (away_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2 or baseline
            home_points = (home_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2 or baseline

            r_home = elo.rating(game.home_team) + elo.home_advantage
            r_away = elo.rating(game.away_team)
            elo_margin = (r_home - r_away) / 28.0

            # Rest disparity & B2B detection
            rest_diff = 0.0
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
                rest_diff = min(5.0, max(-5.0, home_rest - away_rest))
                home_b2b = home_rest <= 1.25
                away_b2b = away_rest <= 1.25
            except (ValueError, TypeError):
                rest_diff = 0.0

            home_3in4 = False
            away_3in4 = False
            if self.schedule_density_drag and game_start_dt is not None:
                h_4d = [g for g in home_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
                a_4d = [g for g in away_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
                home_3in4 = len(h_4d) >= 2
                away_3in4 = len(a_4d) >= 2

            # Apply empirical B2B and 3-in-4 fatigue
            if home_b2b and self.b2b_penalty_home > 0:
                home_points -= self.b2b_penalty_home
            if away_b2b and self.b2b_penalty_away > 0:
                away_points -= self.b2b_penalty_away
            if home_3in4 and not home_b2b:
                home_points -= 0.80
            if away_3in4 and not away_b2b:
                away_points -= 1.20

            trend_margin = (home_points - away_points) + self.home_court_points
            expected_margin = (
                self.elo_weight * elo_margin + self.trend_weight * trend_margin + self.rest_weight * rest_diff
            )
            expected_margin += game.availability_points_gap

            # Dynamic Volatility & Pace-Scaled SD
            effective_margin_sd = self.margin_sd
            if self.dynamic_volatility:
                c_away = away_trend.consistency or 0.05
                c_home = home_trend.consistency or 0.05
                avg_c = (c_away + c_home) / 2.0
                effective_margin_sd = max(
                    12.0, min(17.5, self.margin_sd * (1.0 + 0.25 * (1.0 - avg_c / 0.055)))
                )

            if self.pace_sd_scaling:
                pf_away = (
                    1.0
                    + 0.50
                    * (((away_trend.offense["hl10"] + away_trend.defense["hl10"]) / (2.0 * baseline)) - 1.0)
                    if away_trend.games_played > 0
                    else 1.0
                )
                pf_home = (
                    1.0
                    + 0.50
                    * (((home_trend.offense["hl10"] + home_trend.defense["hl10"]) / (2.0 * baseline)) - 1.0)
                    if home_trend.games_played > 0
                    else 1.0
                )
                pace_drag = -0.8 if (home_b2b or away_b2b) else 0.0
                est_possessions = max(80.0, (league_pace * pf_away * pf_home) + pace_drag)
                effective_margin_sd = max(
                    10.0, min(20.0, effective_margin_sd * sqrt(est_possessions / league_pace))
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
                    "elo_home": round(elo.rating(game.home_team), 1),
                    "elo_away": round(elo.rating(game.away_team), 1),
                    "away_points_hl10": round(away_points, 2),
                    "home_points_hl10": round(home_points, 2),
                    "elo_margin": round(elo_margin, 2),
                    "trend_margin": round(trend_margin, 2),
                    "rest_diff": round(rest_diff, 1),
                    "expected_margin": round(expected_margin, 2),
                    "effective_margin_sd": round(effective_margin_sd, 2),
                    "history_games": len(history),
                },
            }

            # 1. Coherent Moneyline prediction
            if self.use_logistic_moneyline:
                p_margin_home = 1.0 / (1.0 + 10.0 ** (-expected_margin / self.logistic_scale))
            else:
                p_margin_home = 1.0 - _normal_cdf(0.0, expected_margin, effective_margin_sd)
            p_home = self.elo_weight * home_win + (1.0 - self.elo_weight) * p_margin_home
            p_h_clamped = max(0.0001, min(0.9999, p_home))
            p_a_clamped = max(0.0001, min(0.9999, 1.0 - p_home))

            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    probabilities={"home": round(p_h_clamped, 6), "away": round(p_a_clamped, 6)},
                    rationale=f"Margin-coherent Elo {elo.rating(game.home_team):.0f} vs {elo.rating(game.away_team):.0f}, proj margin {expected_margin:+.1f}",
                    **base,
                )
            )

            # 2. Point Spread prediction
            if game.spread_away_line is not None:
                away_cover = _normal_cdf(game.spread_away_line, expected_margin, effective_margin_sd)
                predictions.append(
                    GamePrediction(
                        market_type="spread",
                        inference_inputs={
                            "mean": expected_margin,
                            "sd": effective_margin_sd,
                            "line": game.spread_away_line,
                        },
                        line=game.spread_away_line,
                        probabilities={
                            "away": round(away_cover, 6),
                            "home": round(1.0 - away_cover, 6),
                            "away_cover": round(away_cover, 6),
                            "home_cover": round(1.0 - away_cover, 6),
                        },
                        rationale=f"Projected margin {expected_margin:+.1f} (home), pace-scaled sd {effective_margin_sd:.1f}.",
                        **base,
                    )
                )

        return predictions
