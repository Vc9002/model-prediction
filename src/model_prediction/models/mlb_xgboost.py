"""Monotonic XGBoost model for MLB probability forecasting.

Implements gradient boosted decision trees with domain monotonic constraints:
    d(P_win) / d(Lineup xwOBA) >= 0   (monotonic increasing)
    d(P_win) / d(SP xwOBA Allowed) <= 0 (monotonic decreasing)
    d(P_win) / d(Bullpen Weakness) <= 0 (monotonic decreasing)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import xgboost as xgb
from sklearn.metrics import log_loss, roc_auc_score

# Domain monotonicity mapping for standard feature names:
# +1: monotonic increasing (higher feature value -> higher home win probability)
# -1: monotonic decreasing (higher feature value -> lower home win probability)
#  0: unconstrained
MLB_FEATURE_MONOTONICITY: dict[str, int] = {
    "elo_probability": 1,
    "trend_gap": 1,
    "park_factor": 0,
    "weather_factor": 0,
    "pitcher_era_gap": -1,
    "starter_era_gap": -1,
    "starter_fip_gap": -1,
    "starter_kbb_gap": 1,
    "bullpen_weakness_gap": -1,
    "bullpen_fatigue_gap": -1,
    "offense_pit_gap": 1,
    "xwoba_gap": 1,
    "pythagorean_probability": 1,
    "log5_probability": 1,
}


@dataclass(slots=True)
class XGBoostModelMetrics:
    log_loss: float
    brier_score: float
    accuracy: float
    auc: float | None
    n_samples: int


class MonotonicMLBClassifier:
    """XGBoost classifier enforcing sabermetric monotonic constraints."""

    def __init__(
        self,
        feature_names: Sequence[str],
        n_estimators: int = 150,
        max_depth: int = 3,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        random_state: int = 42,
    ) -> None:
        self.feature_names = list(feature_names)
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.random_state = random_state

        # Build monotone constraints tuple
        constraints = tuple(MLB_FEATURE_MONOTONICITY.get(feat, 0) for feat in self.feature_names)
        self._model = xgb.XGBClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            monotone_constraints=constraints,
            eval_metric="logloss",
            random_state=self.random_state,
        )
        self.is_fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> MonotonicMLBClassifier:
        self._model.fit(X, y)
        self.is_fitted = True
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.is_fitted:
            raise RuntimeError("Model must be fitted before predict_proba.")
        probs = self._model.predict_proba(X)
        return np.clip(probs[:, 1], 1e-6, 1.0 - 1e-6)

    def evaluate(self, X: np.ndarray, y: np.ndarray) -> XGBoostModelMetrics:
        probs = self.predict_proba(X)
        ll = float(log_loss(y, probs))
        brier = float(np.mean((probs - y) ** 2))
        acc = float(np.mean((probs >= 0.5) == y))
        auc_val = float(roc_auc_score(y, probs)) if len(set(y)) > 1 else None

        return XGBoostModelMetrics(
            log_loss=round(ll, 6),
            brier_score=round(brier, 6),
            accuracy=round(acc, 4),
            auc=round(auc_val, 4) if auc_val is not None else None,
            n_samples=len(y),
        )
