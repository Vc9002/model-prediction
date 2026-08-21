"""Unit tests for NFL probability calibration engine."""

from __future__ import annotations

import numpy as np

from model_prediction.models.nfl_calibration import (
    CalibrationMethod,
    NFLCalibrator,
)


def test_temperature_calibration_overconfidence_damping():
    # Simulate overconfident model: assigns 0.80 probability to 60% win outcomes
    np.random.seed(42)
    raw_probs = [0.80] * 50 + [0.20] * 50
    outcomes = [1 if np.random.rand() < 0.60 else 0 for _ in range(50)] + [
        1 if np.random.rand() < 0.40 else 0 for _ in range(50)
    ]

    calibrator = NFLCalibrator()
    calibrator.fit(raw_probs, outcomes, method=CalibrationMethod.TEMPERATURE)

    assert calibrator.is_fitted
    assert calibrator.temperature > 1.0  # Temperature must expand to soften probabilities

    # Calibrated probability should be softened closer to 0.50
    p_cal = calibrator.calibrate(0.80)
    assert 0.50 < p_cal < 0.80


def test_early_season_week_damping():
    calibrator = NFLCalibrator(method=CalibrationMethod.TEMPERATURE, temperature=1.0)
    calibrator.is_fitted = True

    # Week 1 should be softened more than Week 10
    p_week1 = calibrator.calibrate(0.75, week_num=1)
    p_week2 = calibrator.calibrate(0.75, week_num=2)
    p_week10 = calibrator.calibrate(0.75, week_num=10)

    assert 0.50 < p_week1 < p_week2 < p_week10 <= 0.75


def test_platt_and_isotonic_calibration():
    # Sigmoidal dataset
    raw_probs = np.linspace(0.1, 0.9, 100)
    outcomes = (raw_probs > 0.50).astype(int)

    # Platt
    cal_platt = NFLCalibrator().fit(raw_probs, outcomes, method=CalibrationMethod.PLATT)
    metrics_platt = cal_platt.evaluate(raw_probs, outcomes)
    assert metrics_platt.brier_score < 0.25
    assert metrics_platt.expected_calibration_error < 0.15

    # Isotonic
    cal_iso = NFLCalibrator().fit(raw_probs, outcomes, method=CalibrationMethod.ISOTONIC)
    metrics_iso = cal_iso.evaluate(raw_probs, outcomes)
    assert metrics_iso.brier_score < 0.25
