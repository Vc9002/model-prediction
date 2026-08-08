"""Tennis model — surface Elo rating with serve/return logistic blend.

Uses standard Elo (default K=32) with surface-specific rating tracks.
Surface match count tracked per player for dynamic blend weight.
Serve/return model fits logistic coefficients from match data
when serve/return stats are available; falls back to Elo-only otherwise.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression


@dataclass
class TennisPrediction:
    match_id: str
    player_a_win_prob: float
    player_b_win_prob: float
    surface: str
    uncertainty: float = 0.05
    model_version: str = "tennis-elo-sr-v2"

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id, "player_a_win_prob": self.player_a_win_prob,
            "surface": self.surface,
        }


class TennisEloManager:
    """Elo rating tracker with surface-specific ratings.

    Uses standard K=32. Surface match counts track actual matches per surface
    per player for dynamic blend weighting (more surface experience = more
    weight on the surface-specific rating).
    """

    def __init__(self, k: float = 32.0, surface_k_boost: float = 8.0) -> None:
        self.k = k
        self.surface_k_boost = surface_k_boost
        self.ratings: dict[str, float] = defaultdict(lambda: 1500.0)
        self.surface_ratings: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(lambda: 1500.0))
        self.surface_match_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._fitted = False

    def expected_win(self, player_a: str, player_b: str, surface: str) -> float:
        overall_a = self.ratings[player_a]
        overall_b = self.ratings[player_b]
        surface_a = self.surface_ratings[player_a][surface]
        surface_b = self.surface_ratings[player_b][surface]
        n_a = self.surface_match_count[player_a].get(surface, 0)
        n_b = self.surface_match_count[player_b].get(surface, 0)
        surface_weight = min(0.6, 0.1 + 0.025 * min(n_a, n_b))
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
        self.surface_match_count[winner][surface] += 1
        self.surface_match_count[loser][surface] += 1
        self._fitted = True

    def fit(self, matches: list[dict[str, Any]]) -> TennisEloManager:
        for m in sorted(matches, key=lambda x: x.get("date", "")):
            self.update(m["winner"], m["loser"], m.get("surface", "hard"))
        return self


class ServeReturnModel:
    """Logistic model on serve/return points won differential.

    Fits coefficients from match data when serve/return stats are available.
    Falls back to reasonable defaults (spw ~0.013, rpw ~0.011) with small sample.
    """

    def __init__(self) -> None:
        self.coef_spw: float = 0.013
        self.coef_rpw: float = 0.011
        self.intercept: float = 0.0
        self._fitted = False

    def win_probability(self, player_a_spw: float, player_a_rpw: float,
                        player_b_spw: float, player_b_rpw: float) -> float:
        diff_spw = player_a_spw - player_b_spw
        diff_rpw = player_a_rpw - player_b_rpw
        score = self.intercept + self.coef_spw * diff_spw + self.coef_rpw * diff_rpw
        return float(1.0 / (1.0 + np.exp(-score)))

    def fit(self, matches: list[dict[str, Any]]) -> ServeReturnModel:
        """Fit logistic coefficients from serve/return differentials."""
        X_list: list[list[float]] = []
        y_list: list[int] = []
        for m in matches:
            a_spw = m.get("a_spw", m.get("winner_spw"))
            a_rpw = m.get("a_rpw", m.get("winner_rpw"))
            b_spw = m.get("b_spw", m.get("loser_spw"))
            b_rpw = m.get("b_rpw", m.get("loser_rpw"))
            if None not in (a_spw, a_rpw, b_spw, b_rpw):
                X_list.append([a_spw - b_spw, a_rpw - b_rpw])
                y_list.append(1)
                X_list.append([b_spw - a_spw, b_rpw - a_rpw])
                y_list.append(0)

        if len(X_list) < 50:
            self._fitted = True
            return self

        X = np.array(X_list)
        y = np.array(y_list)
        lr = LogisticRegression(penalty="l2", C=10.0, solver="lbfgs", max_iter=1000)
        lr.fit(X, y)
        self.intercept = float(lr.intercept_[0])
        self.coef_spw = float(lr.coef_[0, 0])
        self.coef_rpw = float(lr.coef_[0, 1])
        self._fitted = True
        return self


class TennisModel:
    """Combined tennis model: surface Elo + serve/return logistic.

    When serve/return data is available and the model has been fitted with
    sufficient data, probabilities are blended: 40% Elo + 60% serve/return.
    Otherwise, uses Elo only.
    """

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
        if self.serve_return._fitted and self.serve_return.coef_spw != 0.013:
            prob = 0.4 * elo_prob + 0.6 * sr_prob
        else:
            prob = elo_prob
        return TennisPrediction(match_id=match_id, player_a_win_prob=float(prob),
                                player_b_win_prob=float(1 - prob), surface=surface)
