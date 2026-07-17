"""Elo ratings with configurable K-factor, home-field advantage, and margin scaling.

Computed chronologically from cached completed games only — zero look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log
from typing import Any, Iterable

from .base import FeatureContext, GameRecord, register_feature


DEFAULT_ELO = 1500.0

# Per-sport tuning; falls back to the generic row.
ELO_CONFIG: dict[str, dict[str, float]] = {
    "mlb": {"k": 4.0, "home_advantage": 24.0},
    "nba": {"k": 20.0, "home_advantage": 70.0},
    "wnba": {"k": 20.0, "home_advantage": 60.0},
    "nfl": {"k": 20.0, "home_advantage": 55.0},
    "soccer": {"k": 20.0, "home_advantage": 60.0},
    "tennis": {"k": 32.0, "home_advantage": 0.0},
    "generic": {"k": 20.0, "home_advantage": 50.0},
}


@dataclass
class EloBook:
    ratings: dict[str, float]
    k: float
    home_advantage: float

    def rating(self, team: str) -> float:
        return self.ratings.get(team, DEFAULT_ELO)

    def expected_home_win(self, home: str, away: str) -> float:
        difference = self.rating(home) + self.home_advantage - self.rating(away)
        return 1.0 / (1.0 + 10.0 ** (-difference / 400.0))

    def expected_neutral_win(self, first_team: str, second_team: str) -> float:
        """Venue-neutral win probability from the same point-in-time ratings."""
        difference = self.rating(first_team) - self.rating(second_team)
        return 1.0 / (1.0 + 10.0 ** (-difference / 400.0))

    def update(self, game: GameRecord) -> None:
        expected = self.expected_home_win(game.home_team, game.away_team)
        outcome = (
            1.0 if game.home_score > game.away_score else 0.0 if game.home_score < game.away_score else 0.5
        )
        margin = abs(game.margin)
        # Margin-of-victory multiplier (538-style log scaling, autocorrelation damped).
        rating_gap = self.rating(game.home_team) + self.home_advantage - self.rating(game.away_team)
        winner_gap = rating_gap if outcome >= 0.5 else -rating_gap
        multiplier = log(max(margin, 1) + 1) * (2.2 / (winner_gap * 0.001 + 2.2))
        delta = self.k * multiplier * (outcome - expected)
        self.ratings[game.home_team] = self.rating(game.home_team) + delta
        self.ratings[game.away_team] = self.rating(game.away_team) - delta


def build_elo(games: Iterable[GameRecord], sport: str) -> EloBook:
    config = ELO_CONFIG.get(sport.lower(), ELO_CONFIG["generic"])
    book = EloBook(ratings={}, k=config["k"], home_advantage=config["home_advantage"])
    for game in sorted(games, key=lambda item: item.start):
        book.update(game)
    return book


@register_feature("ratings")
def elo_snapshot(context: FeatureContext) -> dict[str, Any]:
    book = build_elo(context.games, context.sport)
    return {
        "k": book.k,
        "home_advantage": book.home_advantage,
        "ratings": {team: round(rating, 2) for team, rating in sorted(book.ratings.items())},
    }
