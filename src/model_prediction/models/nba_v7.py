"""NBA Structural Model v7 (nba-structural-v7).

Unified point-in-time scoring distribution, margin, total, and coherent moneyline engine:
1. Dynamic matchup pace with B2B and 3-in-4 fatigue drag.
2. Possessions-scaled margin standard deviation and total standard deviation.
3. Calibrated modern home court advantage (+1.75 pts).
4. Early-season Elo-dominant weighting (70% Elo / 30% Trend) to suppress small-sample noise.
5. Calibrated baseline points per team (113.8 pts) matching 2026-27 scoring environment.
6. Replacement-aware player availability adjustments (shifting margin without artificially depressing total floor).
7. Coherent logistic moneyline conversion (s = 11.8).
8. Overtime variance inflation for close games (|margin| <= 3.5).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.elo_ratings import build_elo
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase
from .basketball import UpcomingGame

NBA_SPREAD_V7_VERSION = "nba-spread-margin-v7"
NBA_TOTAL_V7_VERSION = "nba-total-margin-v7"
NBA_MONEYLINE_V7_VERSION = "nba-moneyline-v7"


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if math.isnan(x) or math.isnan(mean) or math.isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))
    return max(0.0001, min(0.9999, raw))


class NBAStructuralV7Engine:
    """Unified NBA score simulation and multi-market pricing engine."""

    def __init__(
        self,
        margin_sd: float = 14.0,
        total_sd: float = 18.0,
        baseline_points_per_team: float = 113.8,
        league_pace: float = 99.2,
        home_court_points: float = 1.75,
        elo_weight: float = 0.70,
        trend_weight: float = 0.30,
        rest_weight: float = 0.15,
        b2b_penalty_home: float = 2.0,
        b2b_penalty_away: float = 1.5,
        b2b_pace_drag: float = 0.8,
        schedule_density_drag: bool = True,
        dynamic_volatility: bool = True,
        pace_sd_scaling: bool = True,
        overtime_risk_adjustment: bool = True,
        logistic_scale: float = 11.8,
        offseason_gap_days: float = 60.0,
        offseason_retention: float = 0.40,
        isolate_postseason: bool = True,
        elo_home_advantage: float = 38.0,
        spread_version: str = NBA_SPREAD_V7_VERSION,
        total_version: str = NBA_TOTAL_V7_VERSION,
        moneyline_version: str = NBA_MONEYLINE_V7_VERSION,
        **_extra_kwargs: Any,
    ) -> None:
        self.margin_sd = margin_sd
        self.total_sd = total_sd
        self.baseline_points_per_team = baseline_points_per_team
        self.league_pace = league_pace
        self.home_court_points = home_court_points
        self.elo_weight = elo_weight
        self.trend_weight = trend_weight
        self.rest_weight = rest_weight
        self.b2b_penalty_home = b2b_penalty_home
        self.b2b_penalty_away = b2b_penalty_away
        self.b2b_pace_drag = b2b_pace_drag
        self.schedule_density_drag = schedule_density_drag
        self.dynamic_volatility = dynamic_volatility
        self.pace_sd_scaling = pace_sd_scaling
        self.overtime_risk_adjustment = overtime_risk_adjustment
        self.logistic_scale = logistic_scale
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.elo_home_advantage = elo_home_advantage
        self.spread_version = spread_version
        self.total_version = total_version
        self.moneyline_version = moneyline_version
        self.sport = "nba"
        self.league = "NBA"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NBAStructuralV7Engine:
        return cls(
            margin_sd=float(artifact.get("margin_sd", 14.0)),
            total_sd=float(artifact.get("total_sd", 18.0)),
            baseline_points_per_team=float(artifact.get("baseline_points_per_team", 113.8)),
            league_pace=float(artifact.get("league_pace", 99.2)),
            home_court_points=float(artifact.get("home_court_points", 1.75)),
            elo_weight=float(artifact.get("elo_weight", 0.70)),
            trend_weight=float(artifact.get("trend_weight", 0.30)),
            rest_weight=float(artifact.get("rest_weight", 0.15)),
            b2b_penalty_home=float(artifact.get("b2b_penalty_home", 2.0)),
            b2b_penalty_away=float(artifact.get("b2b_penalty_away", 1.5)),
            b2b_pace_drag=float(artifact.get("b2b_pace_drag", 0.8)),
            schedule_density_drag=bool(artifact.get("schedule_density_drag", True)),
            dynamic_volatility=bool(artifact.get("dynamic_volatility", True)),
            pace_sd_scaling=bool(artifact.get("pace_sd_scaling", True)),
            overtime_risk_adjustment=bool(artifact.get("overtime_risk_adjustment", True)),
            logistic_scale=float(artifact.get("logistic_scale", 11.8)),
            offseason_gap_days=float(artifact.get("offseason_gap_days", 60.0)),
            offseason_retention=float(artifact.get("offseason_retention", 0.40)),
            isolate_postseason=bool(artifact.get("isolate_postseason", True)),
            elo_home_advantage=float(artifact.get("elo_home_advantage", 38.0)),
            spread_version=str(artifact.get("spread_version", NBA_SPREAD_V7_VERSION)),
            total_version=str(artifact.get("total_version", NBA_TOTAL_V7_VERSION)),
            moneyline_version=str(artifact.get("moneyline_version", NBA_MONEYLINE_V7_VERSION)),
        )

    def predict_games(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        elo = build_elo(
            history,
            "nba",
            offseason_gap_days=int(self.offseason_gap_days),
            home_advantage=self.elo_home_advantage,
        )
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )
        baseline = trend.league_baseline or self.baseline_points_per_team

        predictions: list[GamePrediction] = []
        sample = min(len(history), 200)

        for game in upcoming:
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)

            away_off = away_trend.offense["hl10"] if away_trend.games_played > 0 else baseline
            home_off = home_trend.offense["hl10"] if home_trend.games_played > 0 else baseline
            away_def = away_trend.defense["hl10"] if away_trend.games_played > 0 else baseline
            home_def = home_trend.defense["hl10"] if home_trend.games_played > 0 else baseline

            away_points = (away_off + home_def) / 2.0
            home_points = (home_off + away_def) / 2.0

            r_home = elo.rating(game.home_team) + elo.home_advantage
            r_away = elo.rating(game.away_team)
            elo_margin = (r_home - r_away) / 28.0
            home_win = elo.expected_home_win(game.home_team, game.away_team)

            # Rest disparity calculation
            game_start_dt = parse_utc(game.event_start_utc)
            home_prev = [p for p in history if p.home_team == game.home_team or p.away_team == game.home_team]
            away_prev = [p for p in history if p.home_team == game.away_team or p.away_team == game.away_team]
            home_prev.sort(key=lambda p: p.start)
            away_prev.sort(key=lambda p: p.start)

            home_rest = (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0 if home_prev else 3.0
            away_rest = (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0 if away_prev else 3.0
            rest_diff = min(5.0, max(-5.0, home_rest - away_rest))
            home_b2b = home_rest <= 1.25
            away_b2b = away_rest <= 1.25

            home_3in4 = False
            away_3in4 = False
            if self.schedule_density_drag and game_start_dt is not None:
                h_4d = [g for g in home_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
                a_4d = [g for g in away_prev if 0 < (game_start_dt - g.start).total_seconds() <= 4.2 * 86400]
                home_3in4 = len(h_4d) >= 2
                away_3in4 = len(a_4d) >= 2

            # Fatigue adjustments
            if home_b2b and self.b2b_penalty_home > 0:
                home_points -= self.b2b_penalty_home
            if away_b2b and self.b2b_penalty_away > 0:
                away_points -= self.b2b_penalty_away
            if home_3in4 and not home_b2b:
                home_points -= 0.80
            if away_3in4 and not away_b2b:
                away_points -= 1.20

            # Dynamic pace calculation
            est_possessions = self.league_pace
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

            if (home_3in4 or away_3in4) and self.schedule_density_drag:
                expected_total -= 0.60
            if home_b2b and away_b2b:
                expected_total -= 1.00

            # Replacement-aware availability adjustment on total (15% net factor)
            if game.availability_total_loss > 0:
                expected_total -= 0.15 * game.availability_total_loss

            trend_margin = (home_points - away_points) + self.home_court_points
            expected_margin = (
                self.elo_weight * elo_margin + self.trend_weight * trend_margin + self.rest_weight * rest_diff
            )
            # Full margin impact from star/starter availability differential
            expected_margin += game.availability_points_gap

            # Overtime close game adjustment
            margin_proxy = abs(expected_margin)
            if self.overtime_risk_adjustment:
                if margin_proxy <= 3.5:
                    expected_total += 1.20
                elif margin_proxy <= 6.5:
                    expected_total += 0.60

            # Dynamic volatility & SD scaling
            effective_margin_sd = self.margin_sd
            if self.dynamic_volatility:
                c_away = away_trend.consistency or 0.05
                c_home = home_trend.consistency or 0.05
                avg_c = (c_away + c_home) / 2.0
                effective_margin_sd = max(
                    12.0, min(17.5, self.margin_sd * (1.0 + 0.25 * (1.0 - avg_c / 0.055)))
                )
            if self.pace_sd_scaling:
                effective_margin_sd = max(
                    10.0, min(20.0, effective_margin_sd * math.sqrt(est_possessions / self.league_pace))
                )

            effective_total_sd = max(
                13.0, min(24.0, self.total_sd * math.sqrt(est_possessions / self.league_pace))
            )
            uncertainty = max(0.03, min(0.20, 0.20 - 0.005 * sample))

            # 1. Moneyline
            p_margin_home = 1.0 / (1.0 + 10.0 ** (-expected_margin / self.logistic_scale))
            p_home = self.elo_weight * home_win + (1.0 - self.elo_weight) * p_margin_home
            p_h = max(0.0001, min(0.9999, p_home))
            p_a = max(0.0001, min(0.9999, 1.0 - p_home))

            ml_base: GamePredictionBase = {
                "event_id": game.event_id,
                "event_start_utc": game.event_start_utc,
                "league": self.league,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.moneyline_version,
                "feature_basis": {
                    "elo_home": round(elo.rating(game.home_team), 1),
                    "elo_away": round(elo.rating(game.away_team), 1),
                    "expected_margin": round(expected_margin, 2),
                    "expected_total": round(expected_total, 2),
                    "effective_margin_sd": round(effective_margin_sd, 2),
                    "est_possessions": round(est_possessions, 1),
                    "history_games": len(history),
                },
            }
            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    probabilities={"home": round(p_h, 6), "away": round(p_a, 6)},
                    rationale=f"NBA v7 coherent ML: margin {expected_margin:+.1f}, win prob {p_h * 100:.1f}%.",
                    **ml_base,
                )
            )

            # 2. Point Spread
            if game.spread_away_line is not None:
                away_cover = _normal_cdf(game.spread_away_line, expected_margin, effective_margin_sd)
                spread_base: GamePredictionBase = {
                    "event_id": game.event_id,
                    "event_start_utc": game.event_start_utc,
                    "league": self.league,
                    "away_team": game.away_team,
                    "home_team": game.home_team,
                    "uncertainty": round(uncertainty, 4),
                    "model_version": self.spread_version,
                    "feature_basis": {
                        "elo_home": round(elo.rating(game.home_team), 1),
                        "elo_away": round(elo.rating(game.away_team), 1),
                        "expected_margin": round(expected_margin, 2),
                        "effective_margin_sd": round(effective_margin_sd, 2),
                        "est_possessions": round(est_possessions, 1),
                        "history_games": len(history),
                    },
                }
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
                        rationale=f"NBA v7 spread: margin {expected_margin:+.1f}, sd {effective_margin_sd:.1f}.",
                        **spread_base,
                    )
                )

            # 3. Total
            if game.total_line is not None:
                under_prob = _normal_cdf(game.total_line, expected_total, effective_total_sd)
                over_prob = 1.0 - under_prob
                total_base: GamePredictionBase = {
                    "event_id": game.event_id,
                    "event_start_utc": game.event_start_utc,
                    "league": self.league,
                    "away_team": game.away_team,
                    "home_team": game.home_team,
                    "uncertainty": round(uncertainty, 4),
                    "model_version": self.total_version,
                    "feature_basis": {
                        "expected_total": round(expected_total, 2),
                        "effective_total_sd": round(effective_total_sd, 2),
                        "est_possessions": round(est_possessions, 1),
                        "history_games": len(history),
                    },
                }
                predictions.append(
                    GamePrediction(
                        market_type="total",
                        inference_inputs={
                            "mean": expected_total,
                            "sd": effective_total_sd,
                            "line": game.total_line,
                        },
                        line=game.total_line,
                        probabilities={"over": round(over_prob, 6), "under": round(under_prob, 6)},
                        rationale=f"NBA v7 total: proj {expected_total:.1f}, sd {effective_total_sd:.1f}.",
                        **total_base,
                    )
                )

        return predictions


class NBASpreadModelV7:
    """Dedicated NBA Point Spread Model v7."""

    def __init__(self, **kwargs: Any) -> None:
        self.engine = NBAStructuralV7Engine(**kwargs)
        self.version = self.engine.spread_version
        self.sport = "nba"
        self.league = "NBA"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NBASpreadModelV7:
        return cls(**artifact)

    def predict_spreads(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        return [p for p in self.engine.predict_games(history, upcoming) if p.market_type == "spread"]

    predict_games = predict_spreads


class NBATotalModelV7:
    """Dedicated NBA Total Points Model v7."""

    def __init__(self, **kwargs: Any) -> None:
        self.engine = NBAStructuralV7Engine(**kwargs)
        self.version = self.engine.total_version
        self.sport = "nba"
        self.league = "NBA"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NBATotalModelV7:
        return cls(**artifact)

    def predict_totals(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        return [p for p in self.engine.predict_games(history, upcoming) if p.market_type == "total"]

    predict_games = predict_totals


class NBAMoneylineModelV7:
    """Dedicated NBA Coherent Moneyline Model v7."""

    def __init__(self, **kwargs: Any) -> None:
        self.engine = NBAStructuralV7Engine(**kwargs)
        self.version = self.engine.moneyline_version
        self.sport = "nba"
        self.league = "NBA"

    @classmethod
    def from_artifact(cls, artifact: dict[str, Any]) -> NBAMoneylineModelV7:
        return cls(**artifact)

    def predict_moneyline(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        return [p for p in self.engine.predict_games(history, upcoming) if p.market_type == "moneyline"]

    predict_moneylines = predict_moneyline
    predict_games = predict_moneyline
