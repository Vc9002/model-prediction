"""Tests for meta tail calibrator module."""

from __future__ import annotations

import numpy as np
import pytest

from model_prediction.models.meta_calibrator import (
    TailCalibrator,
    compute_expected_calibration_error,
)


def test_ece_computation():
    probs = [0.1, 0.2, 0.8, 0.9]
    y = [0, 0, 1, 1]
    ece = compute_expected_calibration_error(probs, y)
    assert 0.0 <= ece < 0.20


def test_tail_calibrator_monotonicity():
    rng = np.random.default_rng(42)
    # Generate synthetic uncalibrated overconfident probabilities
    raw_p = np.linspace(0.05, 0.95, 100)
    # True underlying probabilities are softer
    true_p = 0.3 + 0.4 * raw_p
    y = rng.binomial(1, true_p)

    calibrator = TailCalibrator().fit(raw_p.tolist(), y.tolist())
    cal_p = calibrator.predict(raw_p.tolist())

    # Monotonicity test: sorted input should produce sorted calibrated output
    assert all(cal_p[i] <= cal_p[i + 1] for i in range(len(cal_p) - 1))


def test_tail_calibrator_unfitted_raises():
    calibrator = TailCalibrator()
    with pytest.raises(RuntimeError, match="must be fitted"):
        calibrator.predict([0.5, 0.6])


def test_tail_calibrator_eval_metrics():
    rng = np.random.default_rng(42)
    raw_p = np.linspace(0.1, 0.9, 200)
    y = rng.binomial(1, raw_p)

    calibrator = TailCalibrator().fit(raw_p.tolist(), y.tolist())
    metrics = calibrator.evaluate(raw_p.tolist(), y.tolist())

    assert metrics.ece_calibrated <= metrics.ece_raw + 0.02
    assert metrics.brier_score_calibrated <= metrics.brier_score_raw + 0.01
