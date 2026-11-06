"""Dedicated NCAAB Point Spread and Coherent Moneyline Model v4.

Mirrors nba_spread_v6.py's shape, but fixes the lifetime-vs-current-season
sample-size bug found live on 2026-11-05 (basketball.py's shared `sample`
variable used TeamTrend.games_played -- cumulative across season
boundaries -- instead of TeamTrend.current_season_games_played, producing
near-max confidence on games where a team had just 1-2 new-season games).
Also widens the margin_sd distribution itself on thin-sample games
(dynamic_volatility), not just the reported uncertainty metadata, so
eligibility.py's edge/confidence gate sees a genuinely wider distribution.
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

NCAAB_SPREAD_V4_VERSION = "ncaab-spread-v4"


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if isnan(x) or isnan(mean) or isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    return max(0.0001, min(0.9999, raw))


class NCAABSpreadModelV4:
    """Independent NCAAB Spread and Moneyline prediction engine."""

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
        offseason_retention: float = 0.25,
        isolate_postseason: bool = True,
        elo_home_advantage: float = 55.0,
        version: str = NCAAB_SPREAD_V4_VERSION,
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
    def from_artifact(cls, artifact: dict[str, Any]) -> NCAABSpreadModelV4:
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
            offseason_retention=float(artifact.get("offseason_retention", 0.25)),
            isolate_postseason=bool(artifact.get("isolate_postseason", True)),
            elo_home_advantage=float(artifact.get("elo_home_advantage", 55.0)),
            version=str(artifact.get("model_version", NCAAB_SPREAD_V4_VERSION)),
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
            home_win = elo.expected_home_win(game.home_team, game.away_team)
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)
            # Fix: current-season sample, not lifetime -- a team's lifetime
            # count stays large across a season boundary while its hl10 level
            # is already dominated by 1-2 new-season games.
            sample = min(away_trend.current_season_games_played, home_trend.current_season_games_played)

            away_points = (away_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2 or baseline
            home_points = (home_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2 or baseline

            r_home = elo.rating(game.home_team) + elo.home_advantage
            r_away = elo.rating(game.away_team)
            elo_margin = (r_home - r_away) / 28.0

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

            home_short_rest = False
            away_short_rest = False
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
                # NCAAB rarely plays true back-to-backs; "short rest" here is
                # <=2 days, distinct from NBA's <=1.25-day B2B threshold.
                home_short_rest = home_rest <= 2.0
                away_short_rest = away_rest <= 2.0
            except (ValueError, TypeError):
                rest_diff = 0.0

            home_dense = False
            away_dense = False
            if self.schedule_density_drag and game_start_dt is not None:
                # NCAAB non-conference multi-team events pack 2-3 games into a
                # long weekend; look back 3 days instead of NBA's 4.2.
                h_3d = [g for g in home_prev if 0 < (game_start_dt - g.start).total_seconds() <= 3.0 * 86400]
                a_3d = [g for g in away_prev if 0 < (game_start_dt - g.start).total_seconds() <= 3.0 * 86400]
                home_dense = len(h_3d) >= 2
                away_dense = len(a_3d) >= 2

            if home_short_rest and self.rest_penalty_home > 0:
                home_points -= self.rest_penalty_home
            if away_short_rest and self.rest_penalty_away > 0:
                away_points -= self.rest_penalty_away
            if home_dense and not home_short_rest:
                home_points -= 0.60
            if away_dense and not away_short_rest:
                away_points -= 0.90

            trend_margin = (home_points - away_points) + self.home_court_points
            expected_margin = (
                self.elo_weight * elo_margin + self.trend_weight * trend_margin + self.rest_weight * rest_diff
            )
            expected_margin += game.availability_points_gap

            # Dynamic volatility: widen the ACTUAL margin_sd (not just the
            # reported uncertainty metadata) when current-season sample is
            # thin, so eligibility.py's edge gate sees a genuinely wider
            # distribution -- this is the mechanism gap the 2026-11-05
            # incident exposed (uncertainty was widened as metadata in the
            # old code's intent, but nothing downstream of the shared class
            # actually widened the distribution feeding the CDF).
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
                    "current_season_sample": sample,
                    "history_games": len(history),
                },
            }

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
                    rationale=(
                        f"Margin-coherent Elo {elo.rating(game.home_team):.0f} vs "
                        f"{elo.rating(game.away_team):.0f}, proj margin {expected_margin:+.1f}"
                    ),
                    **base,
                )
            )

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
                        rationale=f"Projected margin {expected_margin:+.1f} (home), sd {effective_margin_sd:.1f}.",
                        **base,
                    )
                )

        return predictions
