"""NFL drive-based model — expected drives × drive outcome distribution → score.

Separate EPA scalers per team. Home and away teams get distinct outcome distributions
adjusted by their EPA differential. Home-field advantage applied. Multiple simulations
for robust win probability.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    model_version: str = "nfl-drive-v2"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "home_score": self.home_score,
            "away_score": self.away_score, "home_win_prob": self.home_win_prob,
            "total": self.total, "spread": self.spread,
        }


class NFLModel:
    """NFL model: expected drives → drive outcome distribution → coherent scores."""

    DRIVE_OUTCOMES = ["no_score", "field_goal", "touchdown", "safety"]
    OUTCOME_POINTS = {"no_score": 0, "field_goal": 3, "touchdown": 7, "safety": 2}
    BASE_PROBS = {"no_score": 0.65, "field_goal": 0.12, "touchdown": 0.21, "safety": 0.02}
    HOME_FIELD_TD_BOOST = 0.01  # small home-field advantage on TD probability

    def __init__(self, seed: int = 42) -> None:
        self.drives_model = HistGradientBoostingRegressor(
            max_iter=100, max_depth=3, learning_rate=0.05,
            min_samples_leaf=20, random_state=seed,
        )
        self.home_epa_model = Ridge(alpha=1.0)
        self.away_epa_model = Ridge(alpha=1.0)
        self.drives_scaler = StandardScaler()
        self.home_epa_scaler = StandardScaler()
        self.away_epa_scaler = StandardScaler()
        self._fitted = False
        self.rng = np.random.default_rng(seed)

    def fit(self, data: dict[str, np.ndarray]) -> NFLModel:
        self.drives_model.fit(self.drives_scaler.fit_transform(data["drives_X"]), data["drives_y"])
        self.home_epa_model.fit(self.home_epa_scaler.fit_transform(data["home_epa_X"]), data["home_epa_y"])
        self.away_epa_model.fit(self.away_epa_scaler.fit_transform(data["away_epa_X"]), data["away_epa_y"])
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
        n_drives = int(drives)

        Xh = self.home_epa_scaler.transform(home_epa_features.reshape(1, -1))
        Xa = self.away_epa_scaler.transform(away_epa_features.reshape(1, -1))
        home_epa = self.home_epa_model.predict(Xh)[0]
        away_epa = self.away_epa_model.predict(Xa)[0]
        epa_diff = home_epa - away_epa
        td_shift = np.tanh(epa_diff * 2) * 0.05

        # Build separate outcome distributions for home and away
        home_probs = np.array([self.BASE_PROBS[o] for o in self.DRIVE_OUTCOMES])
        away_probs = np.array([self.BASE_PROBS[o] for o in self.DRIVE_OUTCOMES])
        # Home gets TD boost, away gets TD penalty proportional to EPA diff
        home_probs[2] += td_shift + self.HOME_FIELD_TD_BOOST
        away_probs[2] -= td_shift
        # Adjust other categories to keep sum ~1
        home_probs[0] -= td_shift * 1.5 + self.HOME_FIELD_TD_BOOST * 1.5
        away_probs[0] += td_shift * 1.5
        home_probs = np.clip(home_probs, 0.01, 0.95)
        away_probs = np.clip(away_probs, 0.01, 0.95)
        home_probs /= home_probs.sum()
        away_probs /= away_probs.sum()

        n_sim = 1000
        home_scores = np.zeros(n_sim)
        away_scores = np.zeros(n_sim)

        for i in range(n_sim):
            home_outcomes = self.rng.choice(len(self.DRIVE_OUTCOMES), size=n_drives, p=home_probs)
            away_outcomes = self.rng.choice(len(self.DRIVE_OUTCOMES), size=n_drives, p=away_probs)
            home_scores[i] = sum(self.OUTCOME_POINTS[self.DRIVE_OUTCOMES[o]] for o in home_outcomes)
            away_scores[i] = sum(self.OUTCOME_POINTS[self.DRIVE_OUTCOMES[o]] for o in away_outcomes)

        home_win_prob = float((home_scores > away_scores).mean() + 0.5 * (home_scores == away_scores).mean())
        home_score = float(home_scores.mean())
        away_score = float(away_scores.mean())

        return NFLPrediction(
            event_id=event_id, home_score=home_score, away_score=away_score,
            home_win_prob=home_win_prob, total=home_score + away_score,
            spread=home_score - away_score,
        )
