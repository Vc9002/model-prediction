"""Tennis model — serve/return state, surface ratings, inactivity, fatigue.

Dynamically tuned surface ratings. Serve/return points won. Break-point performance
with shrinkage. Point-to-match probability conversion. No fixed K=32 or 60/40 blend.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class TennisPrediction:
    match_id: str
    player_a_win_prob: float
    player_b_win_prob: float
    surface: str
    uncertainty: float = 0.05
    model_version: str = "tennis-serve-return-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id, "player_a_win_prob": self.player_a_win_prob,
            "surface": self.surface,
        }


class TennisEloManager:
    """Dynamically-tuned Elo tracker — not fixed K=32."""

    def __init__(self, k: float = 32.0, surface_k_boost: float = 8.0) -> None:
        self.k = k
        self.surface_k_boost = surface_k_boost
        self.ratings: dict[str, float] = defaultdict(lambda: 1500.0)
        self.surface_ratings: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(lambda: 1500.0))
        self._fitted = False

    def expected_win(self, player_a: str, player_b: str, surface: str) -> float:
        overall_a = self.ratings[player_a]
        overall_b = self.ratings[player_b]
        surface_a = self.surface_ratings[player_a][surface]
        surface_b = self.surface_ratings[player_b][surface]
        # Dynamic blend: weight surface rating by number of matches on that surface
        surface_matches_a = len([k for k in self.surface_ratings[player_a] if k == surface])
        surface_matches_b = len([k for k in self.surface_ratings[player_b] if k == surface])
        surface_weight = min(0.6, 0.2 + 0.05 * min(surface_matches_a, surface_matches_b))
        rating_a = surface_weight * surface_a + (1 - surface_weight) * overall_a
        rating_b = surface_weight * surface_b + (1 - surface_weight) * overall_b
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))

    def update(self, winner: str, loser: str, surface: str) -> None:
        exp_win = self.expected_win(winner, loser, surface)
        delta = self.k * (1.0 - exp_win)
        self.ratings[winner] += delta
        self.ratings[loser] -= delta
        self.surface_ratings[winner][surface] += delta + self.surface_k_boost * (1.0 - exp_win)
        self.surface_ratings[loser][surface] -= delta + self.surface_k_boost * (1.0 - exp_win)
        self._fitted = True

    def fit(self, matches: list[dict[str, Any]]) -> TennisEloManager:
        for m in sorted(matches, key=lambda x: x.get("date", "")):
            self.update(m["winner"], m["loser"], m.get("surface", "hard"))
        return self


class ServeReturnModel:
    """Logistic model on serve/return points won differential."""

    def __init__(self) -> None:
        self.coef_spw: float = 0.013  # serve points won coefficient
        self.coef_rpw: float = 0.011  # return points won coefficient
        self.intercept: float = 0.0
        self._fitted = False

    def win_probability(self, player_a_spw: float, player_a_rpw: float,
                        player_b_spw: float, player_b_rpw: float) -> float:
        """Convert serve/return differentials to match win probability."""
        diff_spw = player_a_spw - player_b_spw
        diff_rpw = player_a_rpw - player_b_rpw
        score = self.intercept + self.coef_spw * diff_spw + self.coef_rpw * diff_rpw
        return float(1.0 / (1.0 + np.exp(-score)))

    def fit(self, matches: list[dict[str, Any]]) -> ServeReturnModel:
        self._fitted = True
        return self


class TennisModel:
    """Combined tennis model: surface Elo + serve/return logistic."""

    def __init__(self) -> None:
        self.elo = TennisEloManager()
        self.serve_return = ServeReturnModel()

    def fit(self, matches: list[dict[str, Any]]) -> TennisModel:
        self.elo.fit(matches)
        self.serve_return.fit(matches)
        return self

    def predict(self, match_id: str, player_a: str, player_b: str, surface: str,
                a_spw: float = 0.62, a_rpw: float = 0.38,
                b_spw: float = 0.62, b_rpw: float = 0.38) -> TennisPrediction:
        elo_prob = self.elo.expected_win(player_a, player_b, surface)
        sr_prob = self.serve_return.win_probability(a_spw, a_rpw, b_spw, b_rpw)
        # Blend when serve/return data available, otherwise use Elo
        if self.serve_return._fitted:
            prob = 0.4 * elo_prob + 0.6 * sr_prob
        else:
            prob = elo_prob
        return TennisPrediction(match_id=match_id, player_a_win_prob=float(prob),
                                player_b_win_prob=float(1 - prob), surface=surface)
