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
from math import erf, sqrt

from ..domain import parse_utc
from ..features.base import GameRecord
from ..features.elo_ratings import build_elo
from ..features.trends import TrendEngine
from .base import GamePrediction, GamePredictionBase


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5
    raw = 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))
    return max(0.0001, min(0.9999, raw))


@dataclass(frozen=True)
class UpcomingGame:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    spread_away_line: float | None = None
    total_line: float | None = None


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
        elo = build_elo(history, self._sport)
        trend = TrendEngine(history)
        baseline = trend.league_baseline or 82.0
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
            home_prev = [g for g in history if g.home_team == game.home_team or g.away_team == game.home_team]
            away_prev = [g for g in history if g.home_team == game.away_team or g.away_team == game.away_team]

            if self.rest_weight != 0.0:
                try:
                    game_start_dt = parse_utc(game.event_start_utc)
                    home_rest = (
                        (game_start_dt - home_prev[-1].start).total_seconds() / 86400.0 if home_prev else 3.0
                    )
                    away_rest = (
                        (game_start_dt - away_prev[-1].start).total_seconds() / 86400.0 if away_prev else 3.0
                    )
                    rest_diff = min(5.0, max(-5.0, home_rest - away_rest))
                except (ValueError, TypeError):
                    rest_diff = 0.0

            if self.elo_weight > 0.0 or self.rest_weight != 0.0:
                expected_margin = (
                    self.elo_weight * elo_margin
                    + self.trend_weight * trend_margin
                    + self.rest_weight * rest_diff
                )
            else:
                expected_margin = trend_margin

            # Composite total prediction
            raw_trend_total = away_points + home_points
            if self.team_total_weight > 0.0:
                home_10 = home_prev[-10:] if home_prev else []
                away_10 = away_prev[-10:] if away_prev else []
                home_tot = (
                    sum(g.home_score + g.away_score for g in home_10) / len(home_10)
                    if home_10
                    else (baseline * 2.0)
                )
                away_tot = (
                    sum(g.home_score + g.away_score for g in away_10) / len(away_10)
                    if away_10
                    else (baseline * 2.0)
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
                    "expected_margin": round(expected_margin, 2),
                    "expected_total": round(expected_total, 2),
                    "history_games": len(history),
                },
            }
            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    probabilities={"home": round(home_win, 6), "away": round(1 - home_win, 6)},
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
                        line=game.spread_away_line,
                        probabilities={
                            "away": round(away_cover, 6),
                            "home": round(1 - away_cover, 6),
                            "away_cover": round(away_cover, 6),
                            "home_cover": round(1 - away_cover, 6),
                        },
                        rationale=(
                            f"Projected margin {expected_margin:+.1f} (home), sd {self.margin_sd:.1f}."
                        ),
                        **base,
                    )
                )
            if game.total_line is not None:
                over = 1 - _normal_cdf(game.total_line, expected_total, self.total_sd)
                predictions.append(
                    GamePrediction(
                        market_type="total",
                        line=game.total_line,
                        probabilities={"over": round(over, 6), "under": round(1 - over, 6)},
                        rationale=f"Projected total {expected_total:.1f}, sd {self.total_sd:.1f}.",
                        **base,
                    )
                )
        return predictions
