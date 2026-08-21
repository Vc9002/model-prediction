"""Unit tests for Multi-Sport Shared Meta-Calibrator."""

from __future__ import annotations

from model_prediction.meta_calibrator import SharedMetaCalibrator


def test_platt_meta_calibrator_fits_and_calibrates() -> None:
    calibrator = SharedMetaCalibrator(method="platt")
    # Generate overconfident probabilities vs realistic binary outcomes
    raw_probs = [0.80, 0.85, 0.75, 0.70, 0.90, 0.20, 0.15, 0.30, 0.25, 0.10] * 10
    outcomes = [1, 1, 0, 1, 1, 0, 0, 1, 0, 0] * 10

    res = calibrator.fit(raw_probs, outcomes)
    assert res.sample_size == 100
    assert res.post_brier <= res.pre_brier + 0.05
    assert calibrator.is_fitted

    calibrated_val = calibrator.calibrate(0.85)
    assert 0.0 < calibrated_val < 1.0


def test_isotonic_meta_calibrator() -> None:
    calibrator = SharedMetaCalibrator(method="isotonic")
    raw_probs = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05] * 5
    outcomes = [1, 1, 1, 0, 1, 0, 0, 0, 0, 0] * 5

    calibrator.fit(raw_probs, outcomes)
    assert calibrator.is_fitted
    assert 0.0 < calibrator.calibrate(0.75) < 1.0
