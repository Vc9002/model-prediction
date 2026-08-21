"""Multi-Sport Shared Meta-Calibrator.

Applies post-hoc isotonic regression / Platt scaling across out-of-fold predictions
from multiple sports (MLB, NBA, WNBA, NFL, Soccer, Tennis) to correct shared systematic
miscalibration and overconfidence.
"""

from __future__ import annotations

import math
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

    def fit(self, raw_probabilities: list[float], true_outcomes: list[int]) -> MetaCalibrationResult:
        probs = np.array(raw_probabilities, dtype=float)
        outcomes = np.array(true_outcomes, dtype=int)
        n = len(probs)
        if n < 10:
            raise ValueError("Meta-calibrator requires at least 10 observations")

        pre_brier = float(np.mean((probs - outcomes) ** 2))
        eps = 1e-7
        pre_ll = float(
            -np.mean(
                outcomes * np.log(np.clip(probs, eps, 1 - eps))
                + (1 - outcomes) * np.log(np.clip(1 - probs, eps, 1 - eps))
            )
        )

        if self.method == "isotonic":
            self.isotonic_model = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99)
            self.isotonic_model.fit(probs, outcomes)
            calibrated = self.isotonic_model.predict(probs)
        else:
            # Platt scaling: Logistic regression on logit(p)
            logits = np.log(np.clip(probs, eps, 1 - eps) / (1.0 - np.clip(probs, eps, 1 - eps))).reshape(
                -1, 1
            )
            self.platt_model = LogisticRegression(C=1.0)
            self.platt_model.fit(logits, outcomes)
            calibrated = self.platt_model.predict_proba(logits)[:, 1]

        post_brier = float(np.mean((calibrated - outcomes) ** 2))
        post_ll = float(
            -np.mean(
                outcomes * np.log(np.clip(calibrated, eps, 1 - eps))
                + (1 - outcomes) * np.log(np.clip(1 - calibrated, eps, 1 - eps))
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
