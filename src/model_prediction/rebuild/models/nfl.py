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
        self.drives_scaler = StandardScaler()
        self.epa_scaler = StandardScaler()
        self._fitted = False
        self.rng = np.random.default_rng(seed)

    def fit(self, data: dict[str, np.ndarray]) -> NFLModel:
        self.drives_model.fit(self.drives_scaler.fit_transform(data["drives_X"]), data["drives_y"])
        self.home_epa_model.fit(self.epa_scaler.fit_transform(data["home_epa_X"]), data["home_epa_y"])
        self.away_epa_model.fit(self.epa_scaler.fit_transform(data["away_epa_X"]), data["away_epa_y"])
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

        Xd = self.drives_scaler.transform(drives_features.reshape(1, -1))
        drives = max(15, self.drives_model.predict(Xd)[0])

        Xh = self.epa_scaler.transform(home_epa_features.reshape(1, -1))
        Xa = self.epa_scaler.transform(away_epa_features.reshape(1, -1))
        home_epa = self.home_epa_model.predict(Xh)[0]
        away_epa = self.away_epa_model.predict(Xa)[0]

        epa_diff = home_epa - away_epa
        td_shift = np.tanh(epa_diff * 2) * 0.05

        # Simulate multiple games, derive win prob from distribution
        n_sim = 1000
        home_scores = np.zeros(n_sim)
        away_scores = np.zeros(n_sim)
        n_drives = int(drives)

        for i in range(n_sim):
            # Each drive is one multinomial outcome (not independent binomials)
            # Adjust category probabilities by EPA differential
            probs = np.array([self.OUTCOME_PROB[o] for o in self.DRIVE_OUTCOMES])
            probs[2] += td_shift  # touchdown
            probs[1] += td_shift * 0.5  # field goal
            probs[0] -= td_shift * 1.5  # reduce no_score to keep sum ~1
            probs = np.clip(probs, 0.01, 0.95)
            probs /= probs.sum()

            for _team in range(2):
                outcomes = self.rng.choice(len(self.DRIVE_OUTCOMES), size=n_drives, p=probs)
                pts = sum(self.OUTCOME_POINTS[self.DRIVE_OUTCOMES[o]] for o in outcomes)
                if _team == 0:
                    home_scores[i] = pts
                else:
                    away_scores[i] = pts

        home_win_prob = float((home_scores > away_scores).mean() + 0.5 * (home_scores == away_scores).mean())
        home_score = float(home_scores.mean())
        away_score = float(away_scores.mean())

        return NFLPrediction(
            event_id=event_id, home_score=home_score, away_score=away_score,
            home_win_prob=home_win_prob, total=home_score + away_score,
            spread=home_score - away_score,
        )
