"""Shared basketball engine behind the NBA and WNBA models.

Independent probabilities from cached games only: Elo drives the moneyline,
trend-engine scoring levels drive spread and total via a normal approximation
of margin and combined score. Market prices never enter this model.

NBA and WNBA models are shadow-qualified via v2 learned artifacts. The base
BasketballModel class is research-only; qualification is tracked per-sport
in config/model.yaml and the per-sport registration files.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt
from typing import Sequence

from ..features.base import GameRecord
from ..features.elo_ratings import build_elo
from ..features.trends import TrendEngine
from .base import GamePrediction


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5
    return 0.5 * (1 + erf((x - mean) / (sd * sqrt(2))))


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
    ) -> None:
        self._sport = sport
        self._version = version
        self.margin_sd = margin_sd
        self.total_sd = total_sd
        self.league = league

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
        baseline = trend.league_baseline
        predictions: list[GamePrediction] = []
        for game in upcoming:
            home_win = elo.expected_home_win(game.home_team, game.away_team)
            away_trend = trend.team_trend(game.away_team)
            home_trend = trend.team_trend(game.home_team)
            sample = min(away_trend.games_played, home_trend.games_played)
            # Expected points per team from opponent-adjusted hl10 levels.
            away_points = (away_trend.offense["hl10"] + home_trend.defense["hl10"]) / 2 or baseline
            home_points = (home_trend.offense["hl10"] + away_trend.defense["hl10"]) / 2 or baseline
            expected_margin = home_points - away_points + 2.0  # small home-court points bump
            expected_total = away_points + home_points
            uncertainty = max(0.03, min(0.20, 0.20 - 0.005 * sample))
            base = {
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
