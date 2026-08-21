"""Tests for the real conservative-probability uncertainty components
(uncertainty.py, CLAUDE.md's next-phase Task 16): model_disagreement,
calibration_uncertainty, missingness_penalty, and their composition into a
real conservative_probability.
"""

from __future__ import annotations

import numpy as np
import pytest

from model_prediction.rebuild.uncertainty import (
    CRITICAL_AVAILABILITY_FLAGS,
    MAX_MISSINGNESS_PENALTY,
    MISSING_FLAG_PENALTY,
    calibration_uncertainty,
    compose_conservative_probability,
    missingness_penalty,
    model_disagreement,
)


class TestModelDisagreement:
    def test_real_spread_across_model_families(self):
        # CLAUDE.md's own example.
        d = model_disagreement({"two_head": 0.57, "direct_xgb": 0.66, "coherent_xgb": 0.59})
        assert d == pytest.approx(0.09, abs=1e-9)

    def test_identical_predictions_have_zero_disagreement(self):
        assert model_disagreement({"a": 0.6, "b": 0.6, "c": 0.6}) == 0.0

    def test_single_model_has_zero_disagreement_not_an_error(self):
        assert model_disagreement({"a": 0.6}) == 0.0

    def test_empty_has_zero_disagreement(self):
        assert model_disagreement({}) == 0.0


class TestMissingnessPenalty:
    def test_no_missing_flags_has_zero_penalty(self):
        row = {flag: 1.0 for flag in CRITICAL_AVAILABILITY_FLAGS}
        penalty, missing = missingness_penalty(row)
        assert penalty == 0.0
        assert missing == []

    def test_one_missing_flag_real_penalty(self):
        row = dict.fromkeys(CRITICAL_AVAILABILITY_FLAGS, 1.0)
        row["weather_availability"] = 0.0
        penalty, missing = missingness_penalty(row)
        assert penalty == pytest.approx(MISSING_FLAG_PENALTY)
        assert missing == ["weather_availability"]

    def test_penalty_is_capped_not_unbounded(self):
        row = dict.fromkeys(CRITICAL_AVAILABILITY_FLAGS, 0.0)
        penalty, missing = missingness_penalty(row)
        assert penalty == MAX_MISSINGNESS_PENALTY
        assert len(missing) == len(CRITICAL_AVAILABILITY_FLAGS)

    def test_missing_key_entirely_counts_as_unavailable(self):
        # A row that doesn't even carry the flag (e.g. an older schema
        # version) must fail closed as "missing," not silently pass as
        # "available."
        penalty, missing = missingness_penalty({})
        assert penalty == MAX_MISSINGNESS_PENALTY
        assert set(missing) == set(CRITICAL_AVAILABILITY_FLAGS)


class TestCalibrationUncertainty:
    def test_below_sample_floor_returns_zero_not_a_guess(self):
        assert calibration_uncertainty(0.6, [0.5, 0.6], [1, 0], "platt") == 0.0

    def test_real_bootstrap_produces_a_nonnegative_real_spread(self):
        rng = np.random.default_rng(0)
        n = 200
        probs = rng.uniform(0.1, 0.9, n).tolist()
        labels = (rng.uniform(0, 1, n) < np.array(probs)).astype(int).tolist()
        result = calibration_uncertainty(0.6, probs, labels, "platt", n_bootstrap=50)
        assert result >= 0.0

    def test_deterministic_with_the_same_seed(self):
        rng = np.random.default_rng(1)
        n = 200
        probs = rng.uniform(0.1, 0.9, n).tolist()
        labels = (rng.uniform(0, 1, n) < np.array(probs)).astype(int).tolist()
        r1 = calibration_uncertainty(0.6, probs, labels, "platt", n_bootstrap=30, seed=7)
        r2 = calibration_uncertainty(0.6, probs, labels, "platt", n_bootstrap=30, seed=7)
        assert r1 == r2

    def test_identity_method_has_zero_real_uncertainty(self):
        # identity always returns its input unchanged regardless of the
        # resampled calibration-fitting data -- real, zero-variance case.
        rng = np.random.default_rng(2)
        n = 100
        probs = rng.uniform(0.1, 0.9, n).tolist()
        labels = (rng.uniform(0, 1, n) < np.array(probs)).astype(int).tolist()
        result = calibration_uncertainty(0.6, probs, labels, "identity", n_bootstrap=30)
        assert result == pytest.approx(0.0, abs=1e-9)


class TestComposeConservativeProbability:
    def test_no_uncertainty_at_all_equals_bootstrap_lower(self):
        result = compose_conservative_probability(
            calibrated_probability=0.6,
            bootstrap_lower=0.55,
            bootstrap_upper=0.65,
            model_disagreement=0.0,
            calibration_uncertainty=0.0,
            missingness_penalty=0.0,
        )
        assert result.conservative_probability == pytest.approx(0.55)
        assert result.probability_lower == pytest.approx(0.55)

    def test_real_disagreement_widens_the_bound(self):
        base = compose_conservative_probability(
            calibrated_probability=0.6,
            bootstrap_lower=0.55,
            bootstrap_upper=0.65,
            model_disagreement=0.0,
            calibration_uncertainty=0.0,
            missingness_penalty=0.0,
        )
        widened = compose_conservative_probability(
            calibrated_probability=0.6,
            bootstrap_lower=0.55,
            bootstrap_upper=0.65,
            model_disagreement=0.10,
            calibration_uncertainty=0.0,
            missingness_penalty=0.0,
        )
        assert widened.conservative_probability < base.conservative_probability
        assert widened.probability_upper > base.probability_upper

    def test_missingness_penalty_lowers_the_conservative_probability(self):
        result = compose_conservative_probability(
            calibrated_probability=0.6,
            bootstrap_lower=0.55,
            bootstrap_upper=0.65,
            model_disagreement=0.0,
            calibration_uncertainty=0.0,
            missingness_penalty=0.04,
            missing_flags=["weather_availability", "home_sp_availability"],
        )
        assert result.conservative_probability == pytest.approx(0.51)
        assert result.missing_flags == ["weather_availability", "home_sp_availability"]

    def test_result_is_always_clipped_to_a_real_probability(self):
        result = compose_conservative_probability(
            calibrated_probability=0.6,
            bootstrap_lower=0.1,
            bootstrap_upper=0.2,
            model_disagreement=0.5,
            calibration_uncertainty=0.3,
            missingness_penalty=MAX_MISSINGNESS_PENALTY,
        )
        assert 0.0 <= result.conservative_probability <= 1.0
        assert 0.0 <= result.probability_upper <= 1.0

    def test_unavailable_lineup_uncertainty_contributes_nothing_not_fabricated(self):
        with_none = compose_conservative_probability(
            calibrated_probability=0.6,
            bootstrap_lower=0.55,
            bootstrap_upper=0.65,
            model_disagreement=0.0,
            calibration_uncertainty=0.0,
            missingness_penalty=0.0,
            lineup_uncertainty=None,
        )
        with_zero = compose_conservative_probability(
            calibrated_probability=0.6,
            bootstrap_lower=0.55,
            bootstrap_upper=0.65,
            model_disagreement=0.0,
            calibration_uncertainty=0.0,
            missingness_penalty=0.0,
            lineup_uncertainty=0.0,
        )
        assert with_none.conservative_probability == with_zero.conservative_probability
        assert with_none.lineup_uncertainty is None

    def test_real_lineup_uncertainty_when_present_widens_further(self):
        result = compose_conservative_probability(
            calibrated_probability=0.6,
            bootstrap_lower=0.55,
            bootstrap_upper=0.65,
            model_disagreement=0.0,
            calibration_uncertainty=0.0,
            missingness_penalty=0.0,
            lineup_uncertainty=0.05,
        )
        assert result.conservative_probability == pytest.approx(0.50)

    def test_raw_probability_defaults_to_calibrated_when_not_given(self):
        result = compose_conservative_probability(
            calibrated_probability=0.6,
            bootstrap_lower=0.55,
            bootstrap_upper=0.65,
            model_disagreement=0.0,
            calibration_uncertainty=0.0,
            missingness_penalty=0.0,
        )
        assert result.raw_probability == pytest.approx(0.6)
