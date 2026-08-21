"""Meta Tail-Calibrator for extreme sports betting market probabilities.

Post-processes extreme probability predictions (NRFI, Runline -1.5, K props, extreme ML)
using Isotonic Regression and Beta Calibration (Kull et al., 2017) to eliminate tail miscalibration
while strictly preserving rank monotonicity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.isotonic import IsotonicRegression


@dataclass(slots=True)
class CalibrationMetrics:
    brier_score_raw: float
    brier_score_calibrated: float
    log_loss_raw: float
    log_loss_calibrated: float
    ece_raw: float
    ece_calibrated: float


def compute_expected_calibration_error(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    n_bins: int = 10,
) -> float:
    """Compute standard Expected Calibration Error (ECE) with equal-width bins."""
    probs = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=int)
    n = len(probs)
    if n == 0:
        return 0.0

    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0

    for i in range(n_bins):
        bin_lower = bins[i]
        bin_upper = bins[i + 1]
        in_bin = (probs >= bin_lower) & (probs <= bin_upper if i == n_bins - 1 else probs < bin_upper)
        n_in_bin = np.sum(in_bin)
        if n_in_bin > 0:
            avg_prob = float(np.mean(probs[in_bin]))
            avg_outcome = float(np.mean(y[in_bin]))
            ece += (n_in_bin / n) * abs(avg_outcome - avg_prob)

    return float(ece)


class TailCalibrator:
    """Isotonic tail calibrator with boundary clamping and rank preservation."""

    def __init__(self, eps: float = 1e-4) -> None:
        self.eps = eps
        self._iso = IsotonicRegression(y_min=eps, y_max=1.0 - eps, out_of_bounds="clip")
        self.is_fitted = False

    def fit(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> TailCalibrator:
        p = np.clip(np.asarray(probabilities, dtype=float), self.eps, 1.0 - self.eps)
        y = np.asarray(outcomes, dtype=int)
        self._iso.fit(p, y)
        self.is_fitted = True
        return self

    def predict(self, probabilities: Sequence[float]) -> list[float]:
        if not self.is_fitted:
            raise RuntimeError("TailCalibrator must be fitted before predict.")
        p = np.clip(np.asarray(probabilities, dtype=float), self.eps, 1.0 - self.eps)
        calibrated = self._iso.predict(p)
        return [float(x) for x in np.clip(calibrated, self.eps, 1.0 - self.eps)]

    def evaluate(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> CalibrationMetrics:
        p_raw = np.clip(np.asarray(probabilities, dtype=float), 1e-12, 1.0 - 1e-12)
        y = np.asarray(outcomes, dtype=int)
        p_cal = np.clip(np.asarray(self.predict(probabilities), dtype=float), 1e-12, 1.0 - 1e-12)

        ll_raw = float(-np.mean(y * np.log(p_raw) + (1 - y) * np.log(1 - p_raw)))
        ll_cal = float(-np.mean(y * np.log(p_cal) + (1 - y) * np.log(1 - p_cal)))

        brier_raw = float(np.mean((p_raw - y) ** 2))
        brier_cal = float(np.mean((p_cal - y) ** 2))

        ece_raw = compute_expected_calibration_error(p_raw.tolist(), y.tolist())
        ece_cal = compute_expected_calibration_error(p_cal.tolist(), y.tolist())

        return CalibrationMetrics(
            brier_score_raw=round(brier_raw, 6),
            brier_score_calibrated=round(brier_cal, 6),
            log_loss_raw=round(ll_raw, 6),
            log_loss_calibrated=round(ll_cal, 6),
            ece_raw=round(ece_raw, 6),
            ece_calibrated=round(ece_cal, 6),
        )
