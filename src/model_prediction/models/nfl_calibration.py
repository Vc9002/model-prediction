# NFL Pre-Season and In-Season Probability Calibration Engine.

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import exp, log
from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


class CalibrationMethod(str, Enum):
    """Supported probability calibration methods."""

    PLATT = "PLATT"
    TEMPERATURE = "TEMPERATURE"
    ISOTONIC = "ISOTONIC"
    RAW = "RAW"


def _logit(p: float, eps: float = 1e-6) -> float:
    """Compute logit log(p / (1-p)) safely bounded away from 0 and 1."""
    p_c = max(eps, min(1.0 - eps, p))
    return log(p_c / (1.0 - p_c))


def _sigmoid(x: float) -> float:
    """Compute standard sigmoid 1 / (1 + exp(-x))."""
    if x >= 40.0:
        return 1.0
    if x <= -40.0:
        return 0.0
    return 1.0 / (1.0 + exp(-x))


@dataclass(slots=True)
class CalibrationMetrics:
    """Evaluation metrics measuring post-calibration quality."""

    brier_score: float
    log_loss: float
    expected_calibration_error: float  # ECE across 10 equal bins
    sample_size: int


@dataclass(slots=True)
class NFLCalibrator:
    """NFL probability calibrator with week-specific shrinkage and multi-method support."""

    method: CalibrationMethod = CalibrationMethod.TEMPERATURE
    temperature: float = 1.0
    platt_slope: float = 1.0
    platt_intercept: float = 0.0
    is_fitted: bool = False
    _isotonic_model: Any = None

    def fit(
        self,
        raw_probs: list[float] | np.ndarray,
        outcomes: list[int] | np.ndarray,
        method: CalibrationMethod | str = CalibrationMethod.TEMPERATURE,
    ) -> NFLCalibrator:
        """Fit calibrator parameters on out-of-fold predictions."""
        p = np.asarray(raw_probs, dtype=float)
        y = np.asarray(outcomes, dtype=int)
        m = CalibrationMethod(method) if isinstance(method, str) else method
        self.method = m

        if len(p) < 10:
            self.is_fitted = True
            return self

        if m == CalibrationMethod.TEMPERATURE:
            # Optimize T via 1D grid search over NLL
            logits = np.array([_logit(prob) for prob in p])
            best_t = 1.0
            best_nll = float("inf")
            for t_cand in np.linspace(0.5, 3.0, 51):
                scaled_probs = 1.0 / (1.0 + np.exp(-logits / t_cand))
                scaled_probs = np.clip(scaled_probs, 1e-12, 1.0 - 1e-12)
                nll = -np.mean(y * np.log(scaled_probs) + (1 - y) * np.log(1 - scaled_probs))
                if nll < best_nll:
                    best_nll = nll
                    best_t = float(t_cand)
            self.temperature = round(best_t, 3)

        elif m == CalibrationMethod.PLATT:
            logits = np.array([[_logit(prob)] for prob in p])
            lr = LogisticRegression(C=1.0, max_iter=200)
            lr.fit(logits, y)
            self.platt_slope = float(lr.coef_[0][0])
            self.platt_intercept = float(lr.intercept_[0])

        elif m == CalibrationMethod.ISOTONIC:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99)
            iso.fit(p, y)
            self._isotonic_model = iso

        self.is_fitted = True
        return self

    def calibrate(self, raw_prob: float, week_num: int | None = None) -> float:
        """Calibrate a single probability with optional early-season temperature damping."""
        p = max(0.001, min(0.999, raw_prob))

        # Early season weeks (Weeks 1-3) have high noise -> apply additional temperature damping
        week_damp = 1.0
        if week_num is not None:
            if week_num == 1:
                week_damp = 1.30  # Soften toward 0.50 by 30%
            elif week_num == 2:
                week_damp = 1.20
            elif week_num == 3:
                week_damp = 1.10

        if self.method == CalibrationMethod.TEMPERATURE:
            effective_t = self.temperature * week_damp
            logit_val = _logit(p)
            return round(_sigmoid(logit_val / effective_t), 4)

        elif self.method == CalibrationMethod.PLATT:
            logit_val = _logit(p)
            scaled_logit = (self.platt_slope * logit_val + self.platt_intercept) / week_damp
            return round(_sigmoid(scaled_logit), 4)

        elif self.method == CalibrationMethod.ISOTONIC and self._isotonic_model is not None:
            cal_p = float(self._isotonic_model.predict([p])[0])
            if week_damp > 1.0:
                logit_val = _logit(cal_p)
                return round(_sigmoid(logit_val / week_damp), 4)
            return round(cal_p, 4)

        return round(p, 4)

    def evaluate(
        self,
        raw_probs: list[float] | np.ndarray,
        outcomes: list[int] | np.ndarray,
    ) -> CalibrationMetrics:
        """Compute Brier score, Log-Loss, and ECE for calibrated probabilities."""
        p_cal = np.array([self.calibrate(p) for p in raw_probs])
        y = np.asarray(outcomes, dtype=int)
        n = len(y)

        brier = float(np.mean((p_cal - y) ** 2))
        p_clipped = np.clip(p_cal, 1e-12, 1.0 - 1e-12)
        ll = float(-np.mean(y * np.log(p_clipped) + (1 - y) * np.log(1 - p_clipped)))

        # 10-bin ECE
        bins = np.linspace(0.0, 1.0, 11)
        ece = 0.0
        for i in range(10):
            bin_mask = (p_cal >= bins[i]) & (p_cal < bins[i + 1])
            if np.sum(bin_mask) > 0:
                bin_acc = np.mean(y[bin_mask])
                bin_conf = np.mean(p_cal[bin_mask])
                ece += (np.sum(bin_mask) / n) * abs(bin_acc - bin_conf)

        return CalibrationMetrics(
            brier_score=round(brier, 4),
            log_loss=round(ll, 4),
            expected_calibration_error=round(float(ece), 4),
            sample_size=n,
        )
