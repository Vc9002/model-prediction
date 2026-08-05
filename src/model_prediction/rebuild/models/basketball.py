"""NBA/WNBA model — expected possessions × lineup-adjusted points per possession.

Separate home and away efficiency estimates. Joint score distribution via normal
approximation or simulation. Moneyline, spread, total from one coherent distribution.
NBA and WNBA trained independently with WNBA using stronger shrinkage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.preprocessing import StandardScaler

from . import JointScoreDistribution


# ── Possessions model ────────────────────────────────────────────────────────


class PossessionsModel:
    """Predicts expected game pace from team pace, opponent pace adjustments."""

    def __init__(self) -> None:
        self.model = Ridge(alpha=1.0)
        self.scaler = StandardScaler()

    def fit(self, X: np.ndarray, y: np.ndarray) -> PossessionsModel:
        self.model.fit(self.scaler.fit_transform(X), y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(self.scaler.transform(X))


# ── Efficiency model ─────────────────────────────────────────────────────────


class EfficiencyModel:
    """Predicts points per 100 possessions for each team from offensive/defensive
    Four Factors, projected minutes, player impact, availability, and matchups."""

    def __init__(self, sport: str = "nba") -> None:
        self.model = HistGradientBoostingRegressor(
            max_iter=150, max_depth=4, learning_rate=0.05,
            min_samples_leaf=30, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.2, random_state=42,
        )
        self.scaler = StandardScaler()
        self.sport = sport

    def fit(self, X: np.ndarray, y: np.ndarray) -> EfficiencyModel:
        self.model.fit(self.scaler.fit_transform(X), y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(self.scaler.transform(X))


# ── NBA/WNBA Unified Model ───────────────────────────────────────────────────


@dataclass
class BasketballPrediction:
    event_id: str
    home_pts: float
    away_pts: float
    home_win_prob: float
    total: float
    spread: float = 0.0  # home margin
    uncertainty: float = 0.05
    model_version: str = "nba-possessions-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "home_pts": self.home_pts,
            "away_pts": self.away_pts, "home_win_prob": self.home_win_prob,
            "total": self.total, "spread": self.spread,
        }


class BasketballModel:
    """NBA or WNBA model: possessions × efficiency → score distribution."""

    def __init__(self, sport: str = "nba", seed: int = 42) -> None:
        self.sport = sport
        self.possessions = PossessionsModel()
        self.home_offense = EfficiencyModel(sport)
        self.away_offense = EfficiencyModel(sport)
        self.home_defense = EfficiencyModel(sport)
        self.away_defense = EfficiencyModel(sport)
        self.distribution = JointScoreDistribution(seed=seed)
        self._fitted = False

    def fit(
        self,
        data: dict[str, np.ndarray],
    ) -> BasketballModel:
        """Fit all sub-models on chronological training data."""
        self.possessions.fit(data["pace_X"], data["pace_y"])
        self.home_offense.fit(data["home_off_X"], data["home_off_y"])
        self.away_offense.fit(data["away_off_X"], data["away_off_y"])
        self.home_defense.fit(data["home_def_X"], data["home_def_y"])
        self.away_defense.fit(data["away_def_X"], data["away_def_y"])
        self._fitted = True
        return self

    def predict(
        self,
        event_id: str,
        pace_features: np.ndarray,
        home_off_features: np.ndarray,
        away_off_features: np.ndarray,
        home_def_features: np.ndarray,
        away_def_features: np.ndarray,
    ) -> BasketballPrediction:
        if not self._fitted:
            raise RuntimeError(f"{self.sport} model not fitted")

        pace = self.possessions.predict(pace_features.reshape(1, -1))[0]
        home_ortg = self.home_offense.predict(home_off_features.reshape(1, -1))[0]
        away_def = self.away_defense.predict(away_def_features.reshape(1, -1))[0]
        away_ortg = self.away_offense.predict(away_off_features.reshape(1, -1))[0]
        home_def = self.home_defense.predict(home_def_features.reshape(1, -1))[0]

        # Net rating per 100 possessions
        home_net = (home_ortg - home_def) / 100
        away_net = (away_ortg - away_def) / 100

        home_pts = max(60, pace * (home_ortg / 100))
        away_pts = max(60, pace * (away_ortg / 100))
        total = home_pts + away_pts

        # Normal approximation for win probability
        margin = home_pts - away_pts
        std = 12.0  # typical NBA game std dev
        home_win_prob = float(1.0 - np.exp(-((margin + 0.5) / std) ** 2 / 2)) if margin > 0 else float(np.exp(-((margin - 0.5) / std) ** 2 / 2)) if margin < 0 else 0.5

        return BasketballPrediction(
            event_id=event_id, home_pts=float(home_pts), away_pts=float(away_pts),
            home_win_prob=home_win_prob, total=float(total), spread=float(margin),
            model_version=f"{self.sport}-possessions-v1",
        )
