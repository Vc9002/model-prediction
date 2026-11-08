"""Dedicated NCAAB Over/Under Total Points Model v5.

Overhauls ncaab-tempo-v3 and prototype v4 with:
1. Native current-season sample gating with off-season opener detection.
2. Dynamic pace and possession modeling: multiplicative tempo-efficiency
   estimation preventing raw point addition distortion.
3. Pace-scaled dynamic total volatility: adjusts total standard deviation
   proportionally to projected game possessions (Virginia vs Alabama adjustment).
4. Rest, schedule-density, and overtime risk calibration tuned for college basketball.
5. Recalibrated offseason retention (0.20) and gap window (150 days).
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

NCAAB_TOTAL_V5_VERSION = "ncaab-total-v5"


def _effective_current_season_games(team_trend, team_prev, game_start_dt, offseason_gap_days):
    """Detect true season openers where last historical game exceeds offseason_gap_days."""
    if game_start_dt is not None and team_prev:
        last_gap_days = (game_start_dt - team_prev[-1].start).total_seconds() / 86400.0
        if last_gap_days > offseason_gap_days:
            return 0
    return team_trend.current_season_games_played


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if isnan(x) or isnan(mean) or isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    return max(0.0001, min(0.9999, raw))


class NCAABTotalModelV5:
    """Independent NCAAB Over/Under Total Points prediction engine."""

    def __init__(
        self,
        total_sd: float = 13.5,
        baseline_points_per_team: float = 71.4,
        league_pace: float = 68.1,
        trend_total_weight: float = 0.85,
        team_total_weight: float = 0.15,
        dynamic_matchup_pace: bool = True,
        pace_drag: float = 0.5,
        schedule_density_drag: bool = True,
        overtime_risk_adjustment: bool = True,
        offseason_gap_days: float = 150.0,
        offseason_retention: float = 0.20,
        isolate_postseason: bool = True,
        version: str = NCAAB_TOTAL_V5_VERSION,
    ) -> None:
        self.total_sd = total_sd
        self.baseline_points_per_team = baseline_points_per_team
        self.league_pace = league_pace
        self.trend_total_weight = trend_total_weight
        self.team_total_weight = team_total_weight
        self.dynamic_matchup_pace = dynamic_matchup_pace
        self.pace_drag = pace_drag
        self.schedule_density_drag = schedule_density_drag
        self.overtime_risk_adjustment = overtime_risk_adjustment
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.version = version
        self.sport = "ncaab"
        self.league = "NCAAB"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NCAABTotalModelV5:
        return cls(
            total_sd=float(artifact.get("total_sd", 13.5)),
            baseline_points_per_team=float(artifact.get("baseline_points_per_team", 71.4)),
            league_pace=float(artifact.get("league_pace", 68.1)),
            trend_total_weight=float(artifact.get("trend_total_weight", 0.85)),
            team_total_weight=float(artifact.get("team_total_weight", 0.15)),
            dynamic_matchup_pace=bool(artifact.get("dynamic_matchup_pace", True)),
            pace_drag=float(artifact.get("pace_drag", 0.5)),
            schedule_density_drag=bool(artifact.get("schedule_density_drag", True)),
            overtime_risk_adjustment=bool(artifact.get("overtime_risk_adjustment", True)),
            offseason_gap_days=float(artifact.get("offseason_gap_days", 150.0)),
            offseason_retention=float(artifact.get("offseason_retention", 0.20)),
            isolate_postseason=bool(artifact.get("isolate_postseason", True)),
            version=str(artifact.get("model_version", NCAAB_TOTAL_V5_VERSION)),
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

            away_sample = _effective_current_season_games(
                away_trend, away_prev, game_start_dt, self.offseason_gap_days
            )
            home_sample = _effective_current_season_games(
                home_trend, home_prev, game_start_dt, self.offseason_gap_days
            )
            sample = min(away_sample, home_sample)

            away_points = (away_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2 or baseline
            home_points = (home_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2 or baseline

            home_rest_days = None
            away_rest_days = None
            if game_start_dt is not None:
                if home_prev:
                    home_rest_days = (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0
                if away_prev:
                    away_rest_days = (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0

            home_short_rest = home_rest_days is not None and home_rest_days < 1.75
            away_short_rest = away_rest_days is not None and away_rest_days < 1.75
            if home_short_rest:
                home_points -= 0.8
            if away_short_rest:
                away_points -= 1.0

            home_3in5 = False
            away_3in5 = False
            if self.schedule_density_drag and game_start_dt is not None:
                home_3in5 = (
                    len([g for g in home_prev if (game_start_dt - g.start).total_seconds() <= 5 * 86400]) >= 2
                )
                away_3in5 = (
                    len([g for g in away_prev if (game_start_dt - g.start).total_seconds() <= 5 * 86400]) >= 2
                )
                if home_3in5 and not home_short_rest:
                    home_points -= 0.5
                if away_3in5 and not away_short_rest:
                    away_points -= 0.6

            raw_trend_total = away_points + home_points

            # Pace adjustment: scale by expected matchup possessions
            pace_adj = 0.0
            if (home_short_rest or away_short_rest) and self.pace_drag > 0.0:
                pace_adj -= self.pace_drag
            if (home_3in5 or away_3in5) and self.schedule_density_drag:
                pace_adj -= 0.4

            expected_pace = self.league_pace
            if self.dynamic_matchup_pace and baseline > 0.0:
                pf_away = 1.0 + 0.45 * (
                    ((away_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2.0 - baseline) / baseline
                )
                pf_home = 1.0 + 0.45 * (
                    ((home_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2.0 - baseline) / baseline
                )
                expected_pace = max(
                    56.0,
                    min(
                        78.0,
                        self.league_pace * (1.0 + 0.50 * ((pf_away - 1.0) + (pf_home - 1.0))) + pace_adj,
                    ),
                )
                pace_multiplier = expected_pace / self.league_pace
                raw_trend_total = raw_trend_total * pace_multiplier

            # Composite total with conference baseline shrinkage
            team_avg_total = (
                away_trend.offense["hl10"]
                + away_trend.defense["hl10"]
                + home_trend.offense["hl10"]
                + home_trend.defense["hl10"]
            ) / 2.0

            w_trend = self.trend_total_weight
            w_team = self.team_total_weight
            w_league = max(0.0, 1.0 - (w_trend + w_team))
            expected_total = w_trend * raw_trend_total + w_team * team_avg_total + w_league * (baseline * 2.0)

            expected_total -= getattr(game, "availability_total_loss", 0.0)

            # Overtime probability bump (spread closeness adjustment)
            expected_margin = home_points - away_points
            if self.overtime_risk_adjustment:
                if abs(expected_margin) <= 3.5:
                    expected_total += 1.2
                elif abs(expected_margin) <= 6.5:
                    expected_total += 0.6

            # Pace-scaled dynamic total standard deviation
            effective_total_sd = self.total_sd * sqrt(expected_pace / self.league_pace)
            if sample < 8:
                sample_factor = max(0.0, min(1.0, 1.0 - sample / 8.0))
                effective_total_sd = max(
                    self.total_sd,
                    min(self.total_sd * 1.5, effective_total_sd * (1.0 + 0.40 * sample_factor)),
                )

            uncertainty = max(0.03, min(0.20, 0.20 - 0.025 * sample))
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
                    "expected_pace": round(expected_pace, 1),
                    "expected_total": round(expected_total, 2),
                    "effective_total_sd": round(effective_total_sd, 2),
                    "current_season_sample": sample,
                    "history_games": len(history),
                },
            }

            p_over = 1.0 - _normal_cdf(
                game.total_line if game.total_line is not None else expected_total,
                expected_total,
                effective_total_sd,
            )
            p_over = max(0.0001, min(0.9999, p_over))
            p_under = 1.0 - p_over

            predictions.append(
                GamePrediction(
                    market_type="total",
                    inference_inputs={
                        "mean": expected_total,
                        "sd": effective_total_sd,
                        "line": game.total_line if game.total_line is not None else expected_total,
                    },
                    line=game.total_line if game.total_line is not None else expected_total,
                    probabilities={
                        "over": round(p_over, 6),
                        "under": round(p_under, 6),
                    },
                    rationale=(
                        f"Expected total {expected_total:.1f} (pace {expected_pace:.1f}, "
                        f"sd {effective_total_sd:.1f})."
                    ),
                    **base,
                )
            )

        return predictions

    predict_games = predict_totals

    @staticmethod
    def compute_dynamic_units(edge: float, min_edge: float = 0.02, max_edge: float = 0.08) -> float:
        """Dynamic unit sizing scaling with edge: 1.00U up to 2.00U."""
        if edge <= min_edge:
            return 1.00
        scaled = 1.00 + (edge - min_edge) * (2.00 - 1.00) / (max_edge - min_edge)
        return round(min(2.00, max(1.00, scaled)), 2)


def ncaab_total_v5_model() -> NCAABTotalModelV5:
    """Factory function for production registry loading."""
    return NCAABTotalModelV5()
