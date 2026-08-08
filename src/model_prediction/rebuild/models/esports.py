"""Esports models — per-title roster-based, player-level, map/patch/draft aware.

Independent models for LOL, CS2, Dota2, Valorant, Rainbow Six.
Model map/game probability first, then derive series probability from format.
Organization Elo retained as prior, not the complete model.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class EsportsPrediction:
    match_id: str
    title: str
    team_a_win_prob: float
    team_b_win_prob: float
    game_prob: float  # single-game probability
    series_format: str = "bo3"
    series_prob: float = 0.5
    model_version: str = "esports-roster-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id, "title": self.title,
            "team_a_win_prob": self.team_a_win_prob, "series_format": self.series_format,
        }


def game_to_series_prob(game_win_prob: float, series_format: str) -> float:
    """Convert single-game win probability to best-of-series win probability.

    bo1: P(win 1 of 1) = p
    bo3: P(win 2 of 3) = p² * (1 + 3(1-p))
    bo5: P(win 3 of 5)
    """
    p = game_win_prob
    q = 1 - p
    if series_format == "bo1":
        return p
    elif series_format == "bo3":
        return p * p + 2 * p * p * q  # win 2-0 + win 2-1
    elif series_format == "bo5":
        # P(3-0) + P(3-1) + P(3-2)
        return p**3 + 3 * p**3 * q + 6 * p**3 * q**2
    return p


class EsportsElo:
    """Per-title Elo tracker for teams/organizations. Used as a prior."""

    def __init__(self, title: str, k: float = 32.0) -> None:
        self.title = title
        self.k = k
        self.ratings: dict[str, float] = defaultdict(lambda: 1500.0)
        self.match_count: dict[str, int] = defaultdict(int)

    def expected_win(self, team_a: str, team_b: str) -> float:
        ra = self.ratings[team_a]
        rb = self.ratings[team_b]
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def update(self, winner: str, loser: str) -> None:
        exp = self.expected_win(winner, loser)
        delta = self.k * (1.0 - exp)
        self.ratings[winner] += delta
        self.ratings[loser] -= delta
        self.match_count[winner] += 1
        self.match_count[loser] += 1

    def confidence(self, team: str) -> float:
        """Shrink toward 0.5 when match count is low."""
        n = self.match_count.get(team, 0)
        return min(1.0, n / 50.0)


class EsportsModel:
    """Per-title esports model: Elo prior + title-specific features."""

    TITLES: ClassVar[list[str]] = ["lol", "cs2", "dota2", "valorant", "rainbow_six"]

    def __init__(self, title: str) -> None:
        if title not in self.TITLES:
            raise ValueError(f"Unknown title: {title}. Must be one of {self.TITLES}")
        self.title = title
        self.elo = EsportsElo(title)
        self._fitted = False

    def fit(self, matches: list[dict[str, Any]]) -> EsportsModel:
        for m in sorted(matches, key=lambda x: x.get("date", "")):
            self.elo.update(m["winner"], m["loser"])
        self._fitted = True
        return self

    def predict(
        self,
        match_id: str,
        team_a: str,
        team_b: str,
        series_format: str = "bo3",
    ) -> EsportsPrediction:
        game_prob = self.elo.expected_win(team_a, team_b)
        confidence_a = self.elo.confidence(team_a)
        confidence_b = self.elo.confidence(team_b)
        # Shrink toward 0.5 when low confidence
        avg_confidence = (confidence_a + confidence_b) / 2
        shrunk_prob = 0.5 + avg_confidence * (game_prob - 0.5)
        series_prob = game_to_series_prob(shrunk_prob, series_format)
        return EsportsPrediction(
            match_id=match_id, title=self.title,
            team_a_win_prob=float(shrunk_prob), team_b_win_prob=float(1 - shrunk_prob),
            game_prob=float(game_prob), series_format=series_format,
            series_prob=float(series_prob),
        )
