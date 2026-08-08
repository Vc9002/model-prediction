"""A genuinely basic, sport-agnostic Elo win-probability model.

Not a replacement for the sport-specific distributions CLAUDE.md's Part 2
calls for (NBA/WNBA's possessions x efficiency model in models/basketball.py,
NFL's drive-outcome model, etc.) -- those need real feature pipelines
(Four Factors, EPA, xG...) that don't exist yet for any sport but MLB. This
is the "basic prediction, not an advanced model" building block explicitly
requested for NBA/WNBA/NFL/Soccer/Tennis's foundation pipelines: a standard
logistic Elo rating fit purely from real historical final scores (which
every ESPN scoreboard collector already produces, sport-agnostically), with
no sport-specific feature engineering. Moneyline win probability only --
Elo alone has no principled way to price spread/total markets, so those are
correctly left unpriced by callers rather than faked (see
basic_sport_pipeline.py's forecast construction).

CLAUDE.md itself names Elo as the CONTROL-tier model for exactly these
sports (NFL: "Retain Elo only as a prior or benchmark"; tennis: "overall
Elo; surface Elo"; KBO/NPB: "tie-aware Elo") -- this is that control, not a
shortcut around building one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import polars as pl

DEFAULT_INITIAL_RATING = 1500.0
DEFAULT_K_FACTOR = 20.0
DEFAULT_HOME_ADVANTAGE = 65.0  # Elo points, standard-ish magnitude across sports


@dataclass
class EloModel:
    """Standard logistic Elo, fit chronologically from real final scores.
    A tie (home_score == away_score, e.g. real soccer draws) updates both
    teams toward 0.5 actual score rather than being skipped -- skipping
    would silently discard real information a win/loss-only Elo variant
    wouldn't need to handle."""

    k_factor: float = DEFAULT_K_FACTOR
    home_advantage: float = DEFAULT_HOME_ADVANTAGE
    initial_rating: float = DEFAULT_INITIAL_RATING
    ratings: dict[str, float] = field(default_factory=dict)
    games_fit: int = 0

    def _get(self, team: str) -> float:
        return self.ratings.get(team, self.initial_rating)

    def _expected(self, rating_a: float, rating_b: float) -> float:
        return 1.0 / (1.0 + 10.0 ** (-(rating_a - rating_b) / 400.0))

    def fit(self, games: pl.DataFrame) -> EloModel:
        """games must be sorted chronologically ascending (caller's
        responsibility, matching MLBTwoHeadModel/train_through's own
        walk-forward convention) and carry home_team/away_team/home_score/
        away_score. Real gap, disclosed: this refits every team's rating
        from scratch on every call rather than persisting/loading rating
        state between runs -- the same "no artifact-reload mechanism yet"
        gap train_through()'s own docstring discloses for MLB, not unique
        to this model."""
        for row in games.iter_rows(named=True):
            home, away = row["home_team"], row["away_team"]
            home_rating, away_rating = self._get(home), self._get(away)
            expected_home = self._expected(home_rating + self.home_advantage, away_rating)
            if row["home_score"] > row["away_score"]:
                actual_home = 1.0
            elif row["home_score"] < row["away_score"]:
                actual_home = 0.0
            else:
                actual_home = 0.5
            delta = self.k_factor * (actual_home - expected_home)
            self.ratings[home] = home_rating + delta
            self.ratings[away] = away_rating - delta
            self.games_fit += 1
        return self

    def predict(self, home_team: str, away_team: str) -> tuple[float, float]:
        """Real home/away win probability from current ratings. Unseen
        teams get initial_rating -- an honest 50/50-ish prior (adjusted
        only by home_advantage), not a guess at their real strength."""
        home_rating, away_rating = self._get(home_team), self._get(away_team)
        home_prob = self._expected(home_rating + self.home_advantage, away_rating)
        return home_prob, 1.0 - home_prob

    def predicted_winner(self, home_team: str, away_team: str) -> Literal["home", "away"]:
        home_prob, away_prob = self.predict(home_team, away_team)
        return "home" if home_prob >= away_prob else "away"
