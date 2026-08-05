"""NFL drive-based model — expected drives × drive outcome distribution → score.

Retains Elo as a prior, not the complete model. Conditions on QB state, early-down
efficiency, protection, pace, injuries, weather.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.preprocessing import StandardScaler


@dataclass
class NFLPrediction:
    event_id: str
    home_score: float
    away_score: float
    home_win_prob: float
    total: float
    spread: float
    uncertainty: float = 0.06
    model_version: str = "nfl-drive-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "home_score": self.home_score,
            "away_score": self.away_score, "home_win_prob": self.home_win_prob,
            "total": self.total, "spread": self.spread,
        }


class NFLModel:
    """NFL model: expected drives → drive outcome probabilities → coherent scores."""

    DRIVE_OUTCOMES = ["no_score", "field_goal", "touchdown", "safety"]
    OUTCOME_POINTS = {"no_score": 0, "field_goal": 3, "touchdown": 7, "safety": 2}
    OUTCOME_PROB = {"no_score": 0.65, "field_goal": 0.12, "touchdown": 0.21, "safety": 0.02}

    def __init__(self, seed: int = 42) -> None:
        self.drives_model = HistGradientBoostingRegressor(
            max_iter=100, max_depth=3, learning_rate=0.05,
            min_samples_leaf=20, random_state=seed,
        )
        self.home_epa_model = Ridge(alpha=1.0)
        self.away_epa_model = Ridge(alpha=1.0)
        self.scaler = StandardScaler()
        self._fitted = False
        self.rng = np.random.default_rng(seed)

    def fit(self, data: dict[str, np.ndarray]) -> NFLModel:
        self.drives_model.fit(self.scaler.fit_transform(data["drives_X"]), data["drives_y"])
        self.home_epa_model.fit(self.scaler.fit_transform(data["home_epa_X"]), data["home_epa_y"])
        self.away_epa_model.fit(self.scaler.fit_transform(data["away_epa_X"]), data["away_epa_y"])
        self._fitted = True
        return self

    def predict(
        self, event_id: str,
        drives_features: np.ndarray,
        home_epa_features: np.ndarray,
        away_epa_features: np.ndarray,
    ) -> NFLPrediction:
        if not self._fitted:
            raise RuntimeError("NFL model not fitted")

        Xd = self.scaler.transform(drives_features.reshape(1, -1))
        drives = max(15, self.drives_model.predict(Xd)[0])

        Xh = self.scaler.transform(home_epa_features.reshape(1, -1))
        Xa = self.scaler.transform(away_epa_features.reshape(1, -1))
        home_epa = self.home_epa_model.predict(Xh)[0]
        away_epa = self.away_epa_model.predict(Xa)[0]

        # Adjust outcome probabilities by EPA differential
        epa_diff = home_epa - away_epa
        td_shift = np.tanh(epa_diff * 2) * 0.05  # bounded shift
        home_score = 0.0
        away_score = 0.0

        for outcome, base_p in self.OUTCOME_PROB.items():
            points = self.OUTCOME_POINTS[outcome]
            # Simulate drives for each team
            home_success = self.rng.binomial(1, min(0.95, base_p + td_shift), int(drives))
            away_success = self.rng.binomial(1, min(0.95, base_p - td_shift), int(drives))
            home_score += home_success.sum() * points
            away_score += away_success.sum() * points

        home_score = float(max(0, home_score))
        away_score = float(max(0, away_score))
        spread = home_score - away_score
        home_win_prob = float(1.0 / (1.0 + np.exp(-spread / 7.0)))

        return NFLPrediction(
            event_id=event_id, home_score=home_score, away_score=away_score,
            home_win_prob=home_win_prob, total=home_score + away_score, spread=spread,
        )
