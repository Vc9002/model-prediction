"""Multi-Sport Shared Meta-Calibrator.

Applies post-hoc isotonic regression / Platt scaling across out-of-fold predictions
from multiple sports (MLB, NBA, WNBA, NFL, Soccer, Tennis) to correct shared systematic
miscalibration and overconfidence.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


@dataclass
class MetaCalibrationResult:
    method: str
    pre_brier: float
    post_brier: float
    pre_log_loss: float
    post_log_loss: float
    sample_size: int


class SharedMetaCalibrator:
    """Cross-sport calibration layer trained on pooled out-of-sample predictions."""

    def __init__(self, method: str = "platt") -> None:
        self.method = method
        self.platt_model: LogisticRegression | None = None
        self.isotonic_model: IsotonicRegression | None = None
        self.is_fitted = False

    def fit(
        self,
        raw_probabilities: list[float],
        true_outcomes: list[int],
        sample_weights: list[float] | None = None,
    ) -> MetaCalibrationResult:
        probs = np.array(raw_probabilities, dtype=float)
        outcomes = np.array(true_outcomes, dtype=int)
        weights = np.array(sample_weights, dtype=float) if sample_weights is not None else None
        n = len(probs)
        if n < 10:
            raise ValueError("Meta-calibrator requires at least 10 observations")

        pre_brier = float(np.average((probs - outcomes) ** 2, weights=weights))
        eps = 1e-7
        pre_ll = float(
            -np.average(
                outcomes * np.log(np.clip(probs, eps, 1 - eps))
                + (1 - outcomes) * np.log(np.clip(1 - probs, eps, 1 - eps)),
                weights=weights,
            )
        )

        if self.method == "isotonic":
            self.isotonic_model = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99)
            self.isotonic_model.fit(probs, outcomes, sample_weight=weights)
            calibrated = self.isotonic_model.predict(probs)
        else:
            # Platt scaling: Logistic regression on logit(p)
            logits = np.log(np.clip(probs, eps, 1 - eps) / (1.0 - np.clip(probs, eps, 1 - eps))).reshape(
                -1, 1
            )
            self.platt_model = LogisticRegression(C=1.0)
            self.platt_model.fit(logits, outcomes, sample_weight=weights)
            calibrated = self.platt_model.predict_proba(logits)[:, 1]

        post_brier = float(np.average((calibrated - outcomes) ** 2, weights=weights))
        post_ll = float(
            -np.average(
                outcomes * np.log(np.clip(calibrated, eps, 1 - eps))
                + (1 - outcomes) * np.log(np.clip(1 - calibrated, eps, 1 - eps)),
                weights=weights,
            )
        )

        self.is_fitted = True
        return MetaCalibrationResult(
            method=self.method,
            pre_brier=round(pre_brier, 5),
            post_brier=round(post_brier, 5),
            pre_log_loss=round(pre_ll, 5),
            post_log_loss=round(post_ll, 5),
            sample_size=n,
        )

    def calibrate(self, raw_probability: float) -> float:
        """Transform a single model probability using the fitted meta-calibrator."""
        if not self.is_fitted:
            return raw_probability
        eps = 1e-7
        p = min(1.0 - eps, max(eps, raw_probability))
        if self.method == "isotonic" and self.isotonic_model is not None:
            return float(self.isotonic_model.predict([p])[0])
        elif self.platt_model is not None:
            logit = np.array([[math.log(p / (1.0 - p))]])
            return float(self.platt_model.predict_proba(logit)[0, 1])
        return raw_probability

    def calibrate_batch(self, raw_probabilities: Sequence[float]) -> np.ndarray:
        """Transform a sequence of model probabilities vectorially for high-performance slate evaluation."""
        if not self.is_fitted:
            return np.array(raw_probabilities, dtype=float)
        eps = 1e-7
        arr = np.clip(np.array(raw_probabilities, dtype=float), eps, 1.0 - eps)
        if self.method == "isotonic" and self.isotonic_model is not None:
            return self.isotonic_model.predict(arr)
        elif self.platt_model is not None:
            logits = np.log(arr / (1.0 - arr)).reshape(-1, 1)
            return self.platt_model.predict_proba(logits)[:, 1]
        return arr
