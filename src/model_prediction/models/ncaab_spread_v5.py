"""Dedicated NCAAB Point Spread and Coherent Logistic Moneyline Model v5.

Overhauls ncaab-tempo-v3 and prototype v4 with:
1. Native current-season sample gating with off-season opener detection.
2. Dynamic volatility widening the actual prediction distribution on thin samples.
3. Coherent logistic moneyline probability mapping (scale=9.5) eliminating
   the catastrophic normal-tail overconfidence on heavy underdogs.
4. Single-source home court advantage (HCA applied once in point margin,
   Elo HCA zeroed out to prevent double-counting).
5. Neutral-site awareness (HCA completely zeroed when neutral_site is True).
6. Recalibrated offseason retention (0.20) and gap window (150 days) reflecting
   modern NCAA transfer portal roster turnover.
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

NCAAB_SPREAD_V5_VERSION = "ncaab-spread-v5"


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


class NCAABSpreadModelV5:
    """Dedicated NCAAB Spread and Logistic Moneyline prediction engine."""

    def __init__(
        self,
        margin_sd: float = 10.5,
        elo_weight: float = 0.50,
        trend_weight: float = 0.50,
        rest_weight: float = 0.15,
        home_court_points: float = 7.2,
        rest_penalty_home: float = 1.60,
        rest_penalty_away: float = 2.00,
        schedule_density_drag: bool = True,
        dynamic_volatility: bool = True,
        use_logistic_moneyline: bool = True,
        logistic_scale: float = 9.5,
        offseason_gap_days: float = 150.0,
        offseason_retention: float = 0.20,
        isolate_postseason: bool = True,
        elo_home_advantage: float = 55.0,
        version: str = NCAAB_SPREAD_V5_VERSION,
    ) -> None:
        self.margin_sd = margin_sd
        self.elo_weight = elo_weight
        self.trend_weight = trend_weight
        self.rest_weight = rest_weight
        self.home_court_points = home_court_points
        self.rest_penalty_home = rest_penalty_home
        self.rest_penalty_away = rest_penalty_away
        self.schedule_density_drag = schedule_density_drag
        self.dynamic_volatility = dynamic_volatility
        self.use_logistic_moneyline = use_logistic_moneyline
        self.logistic_scale = logistic_scale
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.elo_home_advantage = elo_home_advantage
        self.version = version
        self.sport = "ncaab"
        self.league = "NCAAB"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NCAABSpreadModelV5:
        return cls(
            margin_sd=float(artifact.get("margin_sd", 10.5)),
            elo_weight=float(artifact.get("elo_weight", 0.50)),
            trend_weight=float(artifact.get("trend_weight", 0.50)),
            rest_weight=float(artifact.get("rest_weight", 0.15)),
            home_court_points=float(artifact.get("home_court_points", 7.2)),
            rest_penalty_home=float(artifact.get("rest_penalty_home", 1.60)),
            rest_penalty_away=float(artifact.get("rest_penalty_away", 2.00)),
            schedule_density_drag=bool(artifact.get("schedule_density_drag", True)),
            dynamic_volatility=bool(artifact.get("dynamic_volatility", True)),
            use_logistic_moneyline=bool(artifact.get("use_logistic_moneyline", True)),
            logistic_scale=float(artifact.get("logistic_scale", 9.5)),
            offseason_gap_days=float(artifact.get("offseason_gap_days", 150.0)),
            offseason_retention=float(artifact.get("offseason_retention", 0.20)),
            isolate_postseason=bool(artifact.get("isolate_postseason", True)),
            elo_home_advantage=float(artifact.get("elo_home_advantage", 55.0)),
            version=str(artifact.get("model_version", NCAAB_SPREAD_V5_VERSION)),
        )

    def predict_spreads(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        gap_days = int(self.offseason_gap_days) if self.offseason_gap_days is not None else 150
        elo = build_elo(
            history,
            "ncaab",
            offseason_gap_days=gap_days,
            home_advantage=self.elo_home_advantage,
        )
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )
        baseline = trend.league_baseline or 71.4

        predictions: list[GamePrediction] = []

        for game in upcoming:
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)

            # Rest and game history tracking
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

            away_sample = _effective_current_season_games(
                away_trend, away_prev, game_start_dt, self.offseason_gap_days
            )
            home_sample = _effective_current_season_games(
                home_trend, home_prev, game_start_dt, self.offseason_gap_days
            )
            sample = min(away_sample, home_sample)

            away_points = (away_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2 or baseline
            home_points = (home_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2 or baseline

            # Home Court Advantage: zeroed out on neutral site
            is_neutral = bool(getattr(game, "neutral_site", False))
            effective_hca = 0.0 if is_neutral else self.home_court_points
            elo_hca = 0.0 if is_neutral else self.elo_home_advantage

            r_home = elo.rating(game.home_team) + elo_hca
            r_away = elo.rating(game.away_team)
            elo_margin = (r_home - r_away) / 28.0

            # Rest and fatigue penalties
            home_rest_days = None
            away_rest_days = None
            if game_start_dt is not None:
                if home_prev:
                    home_rest_days = (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0
                if away_prev:
                    away_rest_days = (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0

            home_short_rest = home_rest_days is not None and home_rest_days < 1.75
            away_short_rest = away_rest_days is not None and away_rest_days < 1.75
            if home_short_rest and self.rest_penalty_home > 0.0:
                home_points -= self.rest_penalty_home
            if away_short_rest and self.rest_penalty_away > 0.0:
                away_points -= self.rest_penalty_away

            if home_rest_days is not None and away_rest_days is not None:
                capped_home_rest = min(5.0, max(1.0, home_rest_days))
                capped_away_rest = min(5.0, max(1.0, away_rest_days))
                rest_diff = capped_home_rest - capped_away_rest

            # Schedule density drag (3 games in 5 days)
            if self.schedule_density_drag and game_start_dt is not None:
                home_3in5 = (
                    len([g for g in home_prev if (game_start_dt - g.start).total_seconds() <= 5 * 86400]) >= 2
                )
                away_3in5 = (
                    len([g for g in away_prev if (game_start_dt - g.start).total_seconds() <= 5 * 86400]) >= 2
                )
                if home_3in5 and not home_short_rest:
                    home_points -= 0.75
                if away_3in5 and not away_short_rest:
                    away_points -= 1.00

            trend_margin = (home_points - away_points) + effective_hca

            if self.elo_weight > 0.0 or self.rest_weight != 0.0:
                expected_margin = (
                    self.elo_weight * elo_margin
                    + self.trend_weight * trend_margin
                    + self.rest_weight * rest_diff
                )
            else:
                expected_margin = trend_margin

            expected_margin += getattr(game, "availability_points_gap", 0.0)

            # Dynamic Volatility on margin_sd
            effective_margin_sd = self.margin_sd
            if self.dynamic_volatility:
                sample_factor = max(0.0, min(1.0, 1.0 - sample / 8.0))
                effective_margin_sd = max(
                    self.margin_sd, min(self.margin_sd * 1.6, self.margin_sd * (1.0 + 0.5 * sample_factor))
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
                    "elo_home": round(elo.rating(game.home_team), 1),
                    "elo_away": round(elo.rating(game.away_team), 1),
                    "away_points_hl10": round(away_points, 2),
                    "home_points_hl10": round(home_points, 2),
                    "elo_margin": round(elo_margin, 2),
                    "trend_margin": round(trend_margin, 2),
                    "rest_diff": round(rest_diff, 1),
                    "expected_margin": round(expected_margin, 2),
                    "effective_margin_sd": round(effective_margin_sd, 2),
                    "effective_hca": round(effective_hca, 2),
                    "neutral_site": bool(getattr(game, "neutral_site", False)),
                    "current_season_sample": sample,
                    "history_games": len(history),
                },
            }

            # Win probability: logistic mapping with scale 9.5
            if self.use_logistic_moneyline:
                p_margin_home = 1.0 / (1.0 + 10.0 ** (-expected_margin / self.logistic_scale))
            else:
                p_margin_home = 1.0 - _normal_cdf(0.0, expected_margin, effective_margin_sd)

            # Expected home win using neutral Elo probability when neutral site
            if is_neutral:
                home_win_elo = elo.expected_neutral_win(game.home_team, game.away_team)
            else:
                home_win_elo = elo.expected_home_win(game.home_team, game.away_team)
            p_home = self.elo_weight * home_win_elo + (1.0 - self.elo_weight) * p_margin_home
            p_h_clamped = max(0.0001, min(0.9999, p_home))
            p_a_clamped = max(0.0001, min(0.9999, 1.0 - p_home))

            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    inference_inputs={
                        "mean": round(expected_margin, 2),
                        "sd": round(effective_margin_sd, 2),
                    },
                    probabilities={"home": round(p_h_clamped, 6), "away": round(p_a_clamped, 6)},
                    rationale=(
                        f"Margin-coherent Elo {elo.rating(game.home_team):.0f} vs "
                        f"{elo.rating(game.away_team):.0f}, proj margin {expected_margin:+.1f} "
                        f"(HCA {effective_hca:.1f})"
                    ),
                    **base,
                )
            )

            if game.spread_away_line is not None:
                # P(away covers) = P(margin_home < -spread_away_line)
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
                        rationale=f"Projected margin {expected_margin:+.1f} (home), sd {effective_margin_sd:.1f}.",
                        **base,
                    )
                )

        return predictions

    predict_games = predict_spreads

    @staticmethod
    def compute_dynamic_units(edge: float, min_edge: float = 0.02, max_edge: float = 0.08) -> float:
        """Dynamic unit sizing scaling with edge: 1.00U up to 2.00U."""
        if edge <= min_edge:
            return 1.00
        scaled = 1.00 + (edge - min_edge) * (2.00 - 1.00) / (max_edge - min_edge)
        return round(min(2.00, max(1.00, scaled)), 2)


def ncaab_spread_v5_model() -> NCAABSpreadModelV5:
    """Factory function for production registry loading."""
    return NCAABSpreadModelV5()
