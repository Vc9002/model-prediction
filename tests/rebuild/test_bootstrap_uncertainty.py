"""Tests for BootstrapMLBEnsemble (real conservative_probability bounds,
CLAUDE.md's `bootstrap_uncertainty` requirement).

Replaces the flat 3% haircut previously used in mlb_shadow_run.py's
build_forecast() for probability_lower/probability_upper, and fixes a
second, separate real gap: spread and total markets had *no* conservative
haircut at all before this — decision.py's decide_team_market()/
decide_total() priced them directly off the raw point-estimate probability.
See outputs/rebuild/takeover_status.md for the live verification.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from model_prediction.rebuild.models import BootstrapMLBEnsemble, JointScoreDistribution

INTENSITY_FEATURES = ["f1", "f2"]
DIFFERENTIAL_FEATURES = ["g1", "g2"]


def _synthetic_training_data(n: int = 60, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    f1 = rng.uniform(3, 6, n)
    f2 = rng.uniform(3, 6, n)
    g1 = rng.uniform(-2, 2, n)
    g2 = rng.uniform(-1, 1, n)
    total_runs = f1 + f2 + rng.normal(0, 0.5, n)
    home_margin = g1 + g2 + rng.normal(0, 0.5, n)
    return pl.DataFrame(
        {
            "f1": f1,
            "f2": f2,
            "g1": g1,
            "g2": g2,
            "total_runs": total_runs,
            "home_margin": home_margin,
        }
    )


class TestFitting:
    def test_unfitted_ensemble_reports_not_fitted(self):
        ensemble = BootstrapMLBEnsemble(n_bootstrap=5, seed=1)
        assert ensemble.fitted is False

    def test_fitting_produces_the_requested_number_of_replicates(self):
        ensemble = BootstrapMLBEnsemble(n_bootstrap=7, seed=1)
        ensemble.fit(_synthetic_training_data(), INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        assert ensemble.fitted is True
        assert len(ensemble._replicates) == 7

    def test_market_probability_bounds_on_unfitted_ensemble_fails_closed(self):
        ensemble = BootstrapMLBEnsemble(n_bootstrap=5, seed=1)
        distribution = JointScoreDistribution(seed=1)
        row = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2, "event_id": "e1"}
        with pytest.raises(RuntimeError):
            ensemble.market_probability_bounds(row, distribution, "moneyline", "home")


class TestMarketProbabilityBounds:
    def test_lower_bound_never_exceeds_upper_bound(self):
        ensemble = BootstrapMLBEnsemble(n_bootstrap=20, seed=1)
        ensemble.fit(_synthetic_training_data(), INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        distribution = JointScoreDistribution(seed=1)
        row = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2, "event_id": "e1"}

        lower, upper = ensemble.market_probability_bounds(row, distribution, "moneyline", "home")

        assert 0.0 <= lower <= upper <= 1.0

    def test_bounds_are_real_data_driven_not_a_fixed_haircut(self):
        # The whole point of this class: two rows the model treats very
        # differently must not collapse to the same fixed +/-3% haircut
        # width -- the bound width is a real function of prediction
        # uncertainty, not a constant.
        ensemble = BootstrapMLBEnsemble(n_bootstrap=20, seed=1)
        ensemble.fit(_synthetic_training_data(n=15, seed=2), INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        distribution = JointScoreDistribution(seed=1)

        row_in_range = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2, "event_id": "e1"}
        row_extrapolated = {"f1": 50.0, "f2": 50.0, "g1": 20.0, "g2": 20.0, "event_id": "e2"}

        lower_a, upper_a = ensemble.market_probability_bounds(row_in_range, distribution, "moneyline", "home")
        lower_b, upper_b = ensemble.market_probability_bounds(
            row_extrapolated, distribution, "moneyline", "home"
        )

        width_a = upper_a - lower_a
        width_b = upper_b - lower_b
        assert width_a != pytest.approx(width_b, abs=1e-6), (
            "bound width must reflect real prediction disagreement across bootstrap "
            "replicates, not be identical regardless of input -- a fixed haircut would "
            "produce the same width for both rows"
        )

    def test_deterministic_given_the_same_seed(self):
        data = _synthetic_training_data()
        row = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2, "event_id": "e1"}

        ensemble1 = BootstrapMLBEnsemble(n_bootstrap=10, seed=99)
        ensemble1.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        bounds1 = ensemble1.market_probability_bounds(
            row, JointScoreDistribution(seed=1), "moneyline", "home"
        )

        ensemble2 = BootstrapMLBEnsemble(n_bootstrap=10, seed=99)
        ensemble2.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        bounds2 = ensemble2.market_probability_bounds(
            row, JointScoreDistribution(seed=1), "moneyline", "home"
        )

        assert bounds1 == bounds2, "identical seed and identical data must reproduce identical bounds"

    def test_works_uniformly_for_spread_and_total_not_just_moneyline(self):
        # Real gap fixed: before this class existed, spread/total markets
        # had zero conservative haircut of any kind (see decision.py) --
        # this proves the same bootstrap machinery prices all three market
        # types without special-casing.
        ensemble = BootstrapMLBEnsemble(n_bootstrap=15, seed=1)
        ensemble.fit(_synthetic_training_data(), INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        distribution = JointScoreDistribution(seed=1)
        row = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2, "event_id": "e1"}

        spread_lower, spread_upper = ensemble.market_probability_bounds(
            row,
            distribution,
            "spread",
            "home",
            line=-1.5,
        )
        total_lower, total_upper = ensemble.market_probability_bounds(
            row,
            distribution,
            "total",
            "over",
            line=8.5,
        )

        assert 0.0 <= spread_lower <= spread_upper <= 1.0
        assert 0.0 <= total_lower <= total_upper <= 1.0


class TestXGBoostHeadFamily:
    """MLB-1/MLB-5 (multi-sport execution spec): the live model switched
    from sklearn heads to XGBoost heads (the frozen mlb_moneyline_v2
    combination) -- bootstrapping the wrong head family would silently
    measure a different model's uncertainty than the one actually running
    live."""

    def test_xgboost_head_family_fits_xgboost_replicates(self):
        from model_prediction.rebuild.models import XGBoostRunHead

        ensemble = BootstrapMLBEnsemble(n_bootstrap=5, seed=1, head_family="xgboost")
        ensemble.fit(_synthetic_training_data(), INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        assert ensemble.fitted is True
        assert len(ensemble._replicates) == 5
        assert isinstance(ensemble._replicates[0][0], XGBoostRunHead)
        assert isinstance(ensemble._replicates[0][1], XGBoostRunHead)

    def test_xgboost_head_family_produces_real_valid_bounds(self):
        ensemble = BootstrapMLBEnsemble(n_bootstrap=10, seed=1, head_family="xgboost")
        ensemble.fit(_synthetic_training_data(), INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        distribution = JointScoreDistribution(method="negative_binomial", seed=1)
        row = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2, "event_id": "e1"}

        lower, upper = ensemble.market_probability_bounds(row, distribution, "moneyline", "home")

        assert 0.0 <= lower <= upper <= 1.0

    def test_default_head_family_is_still_sklearn(self):
        # Real backward-compatibility check: every pre-existing real caller
        # (train_mlb_uncertainty_demo.py, mlb_shadow_run.py's own prior
        # sklearn-model usage) must keep working unchanged.
        from model_prediction.rebuild.models import RunDifferentialHead, RunIntensityHead

        ensemble = BootstrapMLBEnsemble(n_bootstrap=3, seed=1)
        ensemble.fit(_synthetic_training_data(), INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        assert isinstance(ensemble._replicates[0][0], RunIntensityHead)
        assert isinstance(ensemble._replicates[0][1], RunDifferentialHead)
