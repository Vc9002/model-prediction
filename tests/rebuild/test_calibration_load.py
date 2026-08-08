"""Tests for load_calibrator() -- MLB-2 (multi-sport execution spec): live
inference must reconstruct a fitted calibrator directly from a persisted
artifact's method/parameters, with no refit and no training data, so "the
calibrator" is a fixed, frozen thing every live prediction reuses -- not
implicitly refit per request.
"""

from __future__ import annotations

import pytest

from model_prediction.rebuild.calibration import (
    IdentityCalibrator,
    PlattCalibrator,
    TemperatureScaling,
    load_calibrator,
)


class TestLoadCalibrator:
    def test_temperature_round_trips_exactly(self):
        loaded = load_calibrator("temperature", {"temperature": 10.0})
        assert isinstance(loaded, TemperatureScaling)
        assert loaded.temperature == 10.0

    def test_platt_round_trips_exactly(self):
        loaded = load_calibrator("platt", {"intercept": 0.05, "slope": 0.9})
        assert isinstance(loaded, PlattCalibrator)
        assert loaded.intercept == 0.05
        assert loaded.slope == 0.9

    def test_identity_round_trips(self):
        loaded = load_calibrator("identity", {})
        assert isinstance(loaded, IdentityCalibrator)
        assert loaded.transform(0.6) == 0.6

    def test_loaded_temperature_calibrator_transforms_like_a_freshly_fit_one(self):
        # Real, no-refit reconstruction must produce the identical real
        # transform a fresh TemperatureScaling(temperature=T) would.
        fresh = TemperatureScaling(temperature=2.5)
        loaded = load_calibrator("temperature", {"temperature": 2.5})
        for p in (0.1, 0.35, 0.5, 0.72, 0.95):
            assert loaded.transform(p) == pytest.approx(fresh.transform(p))

    def test_isotonic_is_not_reconstructible_and_fails_loudly(self):
        with pytest.raises(ValueError, match="isotonic"):
            load_calibrator("isotonic", {})

    def test_unknown_method_fails_loudly(self):
        with pytest.raises(ValueError):
            load_calibrator("not_a_real_method", {})
