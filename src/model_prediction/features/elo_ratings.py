"""Elo ratings with configurable K-factor, home-field advantage, and margin scaling.

Computed chronologically from cached completed games only — zero look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import log
from typing import Any, Iterable

from .base import FeatureContext, GameRecord, register_feature


DEFAULT_ELO = 1500.0

# Per-sport tuning; falls back to the generic row.
ELO_CONFIG: dict[str, dict[str, float]] = {
    "mlb":     {"k": 4.0,  "home_advantage": 24.0, "offseason_regression": 0.00},
    "nba":     {"k": 20.0, "home_advantage": 70.0, "offseason_regression": 0.35},
    "wnba":    {"k": 20.0, "home_advantage": 60.0, "offseason_regression": 0.40},
    "nfl":     {"k": 20.0, "home_advantage": 55.0, "offseason_regression": 0.50},
    "soccer":  {"k": 20.0, "home_advantage": 60.0, "offseason_regression": 0.50},
    "tennis":  {"k": 32.0, "home_advantage": 0.0,  "offseason_regression": 0.50},
    "generic": {"k": 20.0, "home_advantage": 50.0, "offseason_regression": 0.50},
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


def build_elo(games: Iterable[GameRecord], sport: str,
              offseason_regression: float | None = None,
              offseason_gap_days: int = 90) -> EloBook:
    """Build chronological Elo ratings with offseason regression.

    When more than ``offseason_gap_days`` pass between consecutive games for
    the same sport (or when a new season is detected), all team ratings are
    regressed ``offseason_regression`` fraction toward DEFAULT_ELO before the
    next game is processed.

    Args:
        games: game records sorted chronologically by start time
        sport: sport key for config lookup
        offseason_regression: fraction to pull toward DEFAULT_ELO (0.0=none, 1.0=full reset)
        offseason_gap_days: consecutive-day gap that triggers regression
    """
    config = ELO_CONFIG.get(sport.lower(), ELO_CONFIG["generic"])
    if offseason_regression is None:
        offseason_regression = float(config.get("offseason_regression", 0.5))
    book = EloBook(ratings={}, k=config["k"], home_advantage=config["home_advantage"])
    sorted_games = sorted(games, key=lambda item: item.start)
    if not sorted_games:
        return book

    last_game_date: datetime | None = None
    for game in sorted_games:
        game_date = _parse_game_date(game)
        if game_date is not None and last_game_date is not None:
            gap = (game_date - last_game_date).days
            if gap > offseason_gap_days:
                _apply_offseason_regression(book, offseason_regression)

        book.update(game)

        if game_date is not None:
            last_game_date = game_date

    return book


def _parse_game_date(game: GameRecord) -> datetime | None:
    """Parse a game's start time as a date for gap detection."""
    try:
        ts = str(game.start).replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _apply_offseason_regression(book: EloBook, regression: float) -> None:
    """Regress all team ratings toward DEFAULT_ELO by the given fraction."""
    if regression <= 0 or not book.ratings:
        return
    target = DEFAULT_ELO
    factor = 1.0 - regression
    for team in list(book.ratings.keys()):
        book.ratings[team] = target * regression + book.ratings[team] * factor


@register_feature("ratings")
def elo_snapshot(context: FeatureContext) -> dict[str, Any]:
    book = build_elo(context.games, context.sport)
    return {
        "k": book.k,
        "home_advantage": book.home_advantage,
        "ratings": {team: round(rating, 2) for team, rating in sorted(book.ratings.items())},
    }
