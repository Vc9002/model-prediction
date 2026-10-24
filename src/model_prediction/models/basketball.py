"""Shared basketball engine behind the NBA and WNBA models.

Independent probabilities from cached games only: Elo drives the moneyline,
trend-engine scoring levels drive spread and total via a normal approximation
of margin and combined score. Market prices never enter this model.

NBA and WNBA models are shadow-qualified via v2 learned artifacts. The base
BasketballModel class is research-only; qualification is tracked per-sport
in config/model.yaml and the per-sport registration files.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import erf, isnan, sqrt
from typing import Any

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.elo_ratings import build_elo
from ..features.trends import TrendEngine, ewm_level
from .base import GamePrediction, GamePredictionBase


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if isnan(x) or isnan(mean) or isnan(sd):
        raise ValueError(f"NaN passed to _normal_cdf: x={x}, mean={mean}, sd={sd}")
    if sd <= 0:
        return 0.5 if x == mean else (0.9999 if x > mean else 0.0001)
    raw = 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    return max(0.0001, min(0.9999, raw))


def _weighted_rolling_total(
    prev_games: Sequence[GameRecord],
    baseline_tot: float,
    gap_days_threshold: float | None,
    retention: float,
) -> float:
    if not prev_games:
        return baseline_tot
    recent = prev_games[-10:]
    if gap_days_threshold is None or len(recent) <= 1:
        return sum(g.home_score + g.away_score for g in recent) / len(recent)
    scores = [float(g.home_score + g.away_score) for g in recent]
    dates = [g.start for g in recent]
    return ewm_level(
        scores,
        half_life=5.0,
        baseline=baseline_tot,
        prior=3.0,
        dates=dates,
        offseason_gap_days=gap_days_threshold,
        offseason_retention=retention,
    )


@dataclass(frozen=True)
class UpcomingGame:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    spread_away_line: float | None = None
    total_line: float | None = None
    availability_points_gap: float = 0.0
    availability_total_loss: float = 0.0
    league: str = "NBA"


class BasketballModel:
    """One unified model; league-specific constants come from the constructor."""

    def __init__(
        self,
        sport: str,
        version: str,
        margin_sd: float,
        total_sd: float,
        league: str,
        elo_weight: float = 0.0,
        trend_weight: float = 1.0,
        rest_weight: float = 0.0,
        home_court_points: float = 2.0,
        trend_total_weight: float = 1.0,
        team_total_weight: float = 0.0,
        baseline_points_per_team: float | None = None,
        offseason_gap_days: float | None = None,
        offseason_retention: float = 0.50,
        isolate_postseason: bool = False,
        elo_home_advantage: float | None = None,
        possession_model: bool = False,
        b2b_penalty_away: float = 0.0,
        b2b_penalty_home: float = 0.0,
        b2b_pace_drag: float = 0.0,
        coherent_moneyline: bool = False,
    ) -> None:
        self._sport = sport
        self._version = version
        self.margin_sd = margin_sd
        self.total_sd = total_sd
        self.league = league
        self.elo_weight = elo_weight
        self.trend_weight = trend_weight
        self.rest_weight = rest_weight
        self.home_court_points = home_court_points
        self.trend_total_weight = trend_total_weight
        self.team_total_weight = team_total_weight
        self.baseline_points_per_team = baseline_points_per_team
        self.offseason_gap_days = offseason_gap_days
        self.offseason_retention = offseason_retention
        self.isolate_postseason = isolate_postseason
        self.elo_home_advantage = elo_home_advantage
        self.possession_model = possession_model
        self.b2b_penalty_away = b2b_penalty_away
        self.b2b_penalty_home = b2b_penalty_home
        self.b2b_pace_drag = b2b_pace_drag
        self.coherent_moneyline = coherent_moneyline

    @property
    def version(self) -> str:
        return self._version

    @property
    def sport(self) -> str:
        return self._sport

    def predict_games(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingGame],
    ) -> list[GamePrediction]:
        elo = build_elo(history, self._sport, home_advantage=self.elo_home_advantage)
        trend = TrendEngine(
            history,
            offseason_gap_days=self.offseason_gap_days,
            offseason_retention=self.offseason_retention,
            isolate_postseason=self.isolate_postseason,
        )
        default_baseline = (
            self.baseline_points_per_team
            if self.baseline_points_per_team is not None
            else (114.2 if str(self._sport).lower() == "nba" else 82.0)
        )
        baseline = trend.league_baseline or default_baseline
        predictions: list[GamePrediction] = []
        for game in upcoming:
            home_win = elo.expected_home_win(game.home_team, game.away_team)
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)
            sample = min(away_trend.games_played, home_trend.games_played)
            # Expected points per team from opponent-adjusted hl10 levels.
            away_points = (away_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2 or baseline
            home_points = (home_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2 or baseline
            trend_margin = (home_points - away_points) + self.home_court_points

            # Elo margin (home rating with HCA minus away rating)
            r_home = elo.rating(game.home_team) + elo.home_advantage
            r_away = elo.rating(game.away_team)
            elo_margin = (r_home - r_away) / 28.0

            # Rest disparity & recent games
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
            if (
                self.rest_weight != 0.0
                or self.b2b_penalty_away > 0.0
                or self.b2b_penalty_home > 0.0
                or self.b2b_pace_drag > 0.0
            ):
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

            if home_b2b and self.b2b_penalty_home > 0.0:
                home_points -= self.b2b_penalty_home
            if away_b2b and self.b2b_penalty_away > 0.0:
                away_points -= self.b2b_penalty_away
            trend_margin = (home_points - away_points) + self.home_court_points

            if self.elo_weight > 0.0 or self.rest_weight != 0.0:
                expected_margin = (
                    self.elo_weight * elo_margin
                    + self.trend_weight * trend_margin
                    + self.rest_weight * rest_diff
                )
            else:
                expected_margin = trend_margin

            expected_margin += game.availability_points_gap

            # Composite total prediction
            raw_trend_total = away_points + home_points
            if self.possession_model:
                league_pace = 99.2 if str(self._sport).lower() == "nba" else 81.4
                pace_adj = 0.0
                if rest_diff > 2.0:
                    pace_adj += 0.5
                elif rest_diff < -2.0:
                    pace_adj -= 0.5
                if (home_b2b or away_b2b) and self.b2b_pace_drag > 0.0:
                    pace_adj -= self.b2b_pace_drag
                est_possessions = league_pace + pace_adj
                ortg_away = (away_points / league_pace) * 100.0
                ortg_home = (home_points / league_pace) * 100.0
                expected_total = est_possessions * (ortg_away + ortg_home) / 100.0
            elif self.team_total_weight > 0.0:
                home_tot = _weighted_rolling_total(
                    home_prev, baseline * 2.0, self.offseason_gap_days, self.offseason_retention
                )
                away_tot = _weighted_rolling_total(
                    away_prev, baseline * 2.0, self.offseason_gap_days, self.offseason_retention
                )
                team_avg_total = (home_tot + away_tot) / 2.0
                w_league = max(0.0, 1.0 - self.trend_total_weight - self.team_total_weight)
                expected_total = (
                    self.trend_total_weight * raw_trend_total
                    + self.team_total_weight * team_avg_total
                    + w_league * (baseline * 2.0)
                )
            else:
                expected_total = raw_trend_total

            expected_total -= game.availability_total_loss

            uncertainty = max(0.03, min(0.20, 0.20 - 0.005 * sample))
            base: GamePredictionBase = {
                "event_id": game.event_id,
                "event_start_utc": game.event_start_utc,
                "league": self.league,
                "away_team": game.away_team,
                "home_team": game.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self._version,
                "feature_basis": {
                    "elo_home": round(elo.rating(game.home_team), 1),
                    "elo_away": round(elo.rating(game.away_team), 1),
                    "away_points_hl10": round(away_points, 2),
                    "home_points_hl10": round(home_points, 2),
                    "elo_margin": round(elo_margin, 2),
                    "trend_margin": round(trend_margin, 2),
                    "rest_diff": round(rest_diff, 1),
                    "availability_points_gap": round(game.availability_points_gap, 2),
                    "availability_total_loss": round(game.availability_total_loss, 2),
                    "expected_margin": round(expected_margin, 2),
                    "expected_total": round(expected_total, 2),
                    "history_games": len(history),
                },
            }
            if self.coherent_moneyline:
                p_margin_home = 1.0 - _normal_cdf(0.0, expected_margin, self.margin_sd)
                p_home = self.elo_weight * home_win + (1.0 - self.elo_weight) * p_margin_home
            else:
                p_home = home_win
            p_h_clamped = max(0.0001, min(0.9999, p_home))
            p_a_clamped = max(0.0001, min(0.9999, 1.0 - p_home))
            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    probabilities={"home": round(p_h_clamped, 6), "away": round(p_a_clamped, 6)},
                    rationale=(
                        f"Elo {elo.rating(game.home_team):.0f} vs {elo.rating(game.away_team):.0f} "
                        f"with home advantage {elo.home_advantage:.0f}."
                    ),
                    **base,
                )
            )
            if game.spread_away_line is not None:
                # P(away covers) = P(margin_home < away_line)
                away_cover = _normal_cdf(game.spread_away_line, expected_margin, self.margin_sd)
                predictions.append(
                    GamePrediction(
                        market_type="spread",
                        inference_inputs={
                            "mean": expected_margin,
                            "sd": self.margin_sd,
                            "line": game.spread_away_line,
                        },
                        line=game.spread_away_line,
                        probabilities={
                            "away": round(away_cover, 6),
                            "home": round(1 - away_cover, 6),
                            "away_cover": round(away_cover, 6),
                            "home_cover": round(1 - away_cover, 6),
                        },
                        rationale=(
                            f"Projected margin {expected_margin:+.1f} (home), sd {self.margin_sd:.1f}."
                            + (
                                f" Availability adj: {game.availability_points_gap:+.1f}."
                                if game.availability_points_gap != 0.0
                                else ""
                            )
                        ),
                        **base,
                    )
                )
            if game.total_line is not None:
                over = 1 - _normal_cdf(game.total_line, expected_total, self.total_sd)
                predictions.append(
                    GamePrediction(
                        market_type="total",
                        inference_inputs={
                            "mean": expected_total,
                            "sd": self.total_sd,
                            "line": game.total_line,
                        },
                        line=game.total_line,
                        probabilities={"over": round(over, 6), "under": round(1 - over, 6)},
                        rationale=(
                            f"Projected total {expected_total:.1f}, sd {self.total_sd:.1f}."
                            + (
                                f" Availability loss: -{game.availability_total_loss:.1f}."
                                if game.availability_total_loss != 0.0
                                else ""
                            )
                        ),
                        **base,
                    )
                )
        return predictions


def __getattr__(name: str) -> Any:
    if name in {
        "BasketballLeagueConfig",
        "BasketballLeagueRegistry",
        "DedicatedBasketballLeagueModel",
        "EuroLeagueModel",
        "NBAModel",
        "NCAABModel",
        "WNBAModel",
        "WNCAABModel",
        "euroleague_model",
        "get_basketball_model",
        "ncaab_model",
        "wncaab_model",
    }:
        import importlib

        mod = importlib.import_module(".basketball_leagues", package=__package__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BasketballModel",
    "UpcomingGame",
]
