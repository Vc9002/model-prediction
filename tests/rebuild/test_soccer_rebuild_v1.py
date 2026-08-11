"""Tests for the soccer rebuild Dixon-Coles Poisson model."""

from __future__ import annotations

import math

import numpy as np
import polars as pl
import pytest

from model_prediction.rebuild.soccer.elo import (
    DixonColesModel,
    DixonColesParams,
    _bivariate_poisson_logpmf,
    _score_matrix_probability,
    _tau,
)


class TestTauCorrection:
    """Dixon-Coles tau correction for low-scoring draws."""

    def test_tau_00_formula(self) -> None:
        """tau(0,0) = 1 - lambda_h * lambda_a * rho."""
        rho = 0.05
        lambda_h, lambda_a = 1.2, 0.8
        expected = 1.0 - lambda_h * lambda_a * rho
        assert _tau(0, 0, lambda_h, lambda_a, rho) == pytest.approx(expected)

    def test_tau_10_formula(self) -> None:
        """tau(1,0) = 1 + lambda_a * rho."""
        rho = 0.05
        lambda_h, lambda_a = 1.2, 0.8
        expected = 1.0 + lambda_a * rho
        assert _tau(1, 0, lambda_h, lambda_a, rho) == pytest.approx(expected)

    def test_tau_01_formula(self) -> None:
        """tau(0,1) = 1 + lambda_h * rho."""
        rho = 0.05
        lambda_h, lambda_a = 1.2, 0.8
        expected = 1.0 + lambda_h * rho
        assert _tau(0, 1, lambda_h, lambda_a, rho) == pytest.approx(expected)

    def test_tau_11_formula(self) -> None:
        """tau(1,1) = 1 - rho."""
        rho = 0.05
        lambda_h, lambda_a = 1.2, 0.8
        expected = 1.0 - rho
        assert _tau(1, 1, lambda_h, lambda_a, rho) == pytest.approx(expected)

    def test_tau_identity_for_high_scores(self) -> None:
        """tau = 1 for scorelines beyond (0,0),(1,0),(0,1),(1,1)."""
        rho = 0.05
        lambda_h, lambda_a = 1.2, 0.8
        for h, a in [(2, 0), (0, 2), (2, 1), (1, 2), (2, 2), (3, 3), (5, 0), (0, 5)]:
            assert _tau(h, a, lambda_h, lambda_a, rho) == 1.0


class TestBivariatePoissonLogPMF:
    """Log-probability computation for Dixon-Coles bivariate Poisson."""

    def test_returns_finite(self) -> None:
        """Log-probability should be finite for reasonable inputs."""
        lp = _bivariate_poisson_logpmf(1, 0, 1.2, 0.8, 0.05)
        assert math.isfinite(lp)
        assert lp < 0  # log probability is negative

    def test_00_scoreline_valid(self) -> None:
        """Log-probability for 0-0 scoreline should be finite."""
        lp = _bivariate_poisson_logpmf(0, 0, 1.0, 1.0, 0.05)
        assert math.isfinite(lp)
        assert lp < 0


class TestScoreMatrixProbability:
    """P(HOME), P(DRAW), P(AWAY) from score probability matrix."""

    def test_probabilities_sum_to_one(self) -> None:
        """P(HOME) + P(DRAW) + P(AWAY) should sum to 1."""
        for lambda_h in [0.5, 1.0, 1.5, 2.0]:
            for lambda_a in [0.5, 1.0, 1.5, 2.0]:
                p_h, p_d, p_a = _score_matrix_probability(lambda_h, lambda_a, 0.05)
                assert p_h + p_d + p_a == pytest.approx(1.0, abs=1e-6)

    def test_home_advantage_increases_home_probability(self) -> None:
        """With equal teams, higher home lambda should increase P(home)."""
        # Equal lambdas -> more draws
        p_h1, _p_d1, _p_a1 = _score_matrix_probability(1.0, 1.0, 0.0)
        # Higher home lambda
        p_h2, _p_d2, _p_a2 = _score_matrix_probability(1.5, 1.0, 0.0)
        assert p_h2 > p_h1  # home win more likely when home is stronger

    def test_symmetric_with_equal_lambdas(self) -> None:
        """With equal lambdas and no rho, P(home) ≈ P(away)."""
        p_h, _p_d, p_a = _score_matrix_probability(1.0, 1.0, 0.0)
        assert p_h == pytest.approx(p_a, abs=1e-6)

    def test_rho_affects_low_scores(self) -> None:
        """Nonzero rho should change the probability distribution."""
        p_h1, p_d1, _p_a1 = _score_matrix_probability(1.0, 1.0, 0.0)
        p_h2, p_d2, _p_a2 = _score_matrix_probability(1.0, 1.0, 0.1)
        # Distribution changes with rho
        assert not (p_h1 == pytest.approx(p_h2) and p_d1 == pytest.approx(p_d2))


class TestDixonColesParams:
    """Parameter storage and serialization."""

    def test_roundtrip(self) -> None:
        """Params should survive to_dict/from_dict roundtrip."""
        params = DixonColesParams(
            team_attack={"A": 0.5, "B": -0.5},
            team_defense={"A": -0.2, "B": 0.2},
            league_baseline={"eng.1": 0.3},
            home_advantage=0.25,
            rho=0.05,
        )
        d = params.to_dict()
        restored = DixonColesParams.from_dict(d)
        assert restored.team_attack == params.team_attack
        assert restored.team_defense == params.team_defense
        assert restored.league_baseline == params.league_baseline
        assert restored.home_advantage == pytest.approx(params.home_advantage)
        assert restored.rho == pytest.approx(params.rho)


class TestDixonColesModel:
    """Model fitting and prediction tests."""

    def _synthetic_data(
        self, n_matches: int = 200, seed: int = 42
    ) -> pl.DataFrame:
        """Generate synthetic completed matches.

        Teams A,B,C,D with A being strongest, D weakest.
        Home advantage of ~0.25. League baseline ~0.3.
        """
        rng = np.random.default_rng(seed)
        teams = ["TeamA", "TeamB", "TeamC", "TeamD"]
        attack = {"TeamA": 0.3, "TeamB": 0.1, "TeamC": -0.1, "TeamD": -0.3}
        defense = {"TeamA": -0.2, "TeamB": 0.0, "TeamC": 0.0, "TeamD": 0.2}
        home_adv = 0.25
        league_base = 0.3

        rows = []
        for _ in range(n_matches):
            hi = rng.integers(0, 4)
            ai = rng.integers(0, 4)
            while ai == hi:
                ai = rng.integers(0, 4)
            ht = teams[hi]
            at = teams[ai]
            lambda_h = np.exp(league_base + attack[ht] + defense[at] + home_adv)
            lambda_a = np.exp(league_base + attack[at] + defense[ht])
            hg = rng.poisson(lambda_h)
            ag = rng.poisson(lambda_a)
            rows.append({
                "home_team_name": ht,
                "away_team_name": at,
                "home_score": hg,
                "away_score": ag,
                "competition_id": "test.1",
            })
        return pl.DataFrame(rows)

    def test_fit_synthetic_data(self) -> None:
        """Model should fit synthetic data and recover home advantage sign."""
        df = self._synthetic_data(300)
        model = DixonColesModel()
        params = model.fit(df, verbose=False)
        assert params.home_advantage > 0, "Home advantage should be positive"
        assert -1.0 <= params.rho <= 1.0, "Rho should be in [-1, 1]"

    def test_attack_defense_sum_to_zero(self) -> None:
        """Post-fit, attack and defense should each sum to approximately zero."""
        df = self._synthetic_data(300)
        model = DixonColesModel()
        params = model.fit(df, verbose=False)
        attack_sum = sum(params.team_attack.values())
        defense_sum = sum(params.team_defense.values())
        assert attack_sum == pytest.approx(0.0, abs=1e-8)
        assert defense_sum == pytest.approx(0.0, abs=1e-8)

    def test_home_advantage_positive(self) -> None:
        """Home advantage should be positive on real-looking data."""
        df = self._synthetic_data(300)
        model = DixonColesModel()
        params = model.fit(df, verbose=False)
        assert params.home_advantage > 0

    def test_rho_in_range(self) -> None:
        """Fitted rho should be in [-1, 1]."""
        df = self._synthetic_data(300)
        model = DixonColesModel()
        params = model.fit(df, verbose=False)
        assert -1.0 <= params.rho <= 1.0

    def test_predict_sums_to_one(self) -> None:
        """Predict output should sum to 1."""
        df = self._synthetic_data(200)
        model = DixonColesModel()
        model.fit(df, verbose=False)
        p_h, p_d, p_a = model.predict("TeamA", "TeamB", "test.1")
        assert p_h + p_d + p_a == pytest.approx(1.0, abs=1e-6)

    def test_predict_better_team_favored(self) -> None:
        """TeamA (stronger) vs TeamD (weaker) should favor TeamA."""
        df = self._synthetic_data(200)
        model = DixonColesModel()
        model.fit(df, verbose=False)
        p_h, _p_d, p_a = model.predict("TeamA", "TeamD", "test.1")
        assert p_h > p_a, "Stronger home team should be favored"

    def test_predict_batch(self) -> None:
        """predict_batch should return correct columns."""
        df = self._synthetic_data(200)
        model = DixonColesModel()
        model.fit(df, verbose=False)
        test_df = pl.DataFrame({
            "home_team_name": ["TeamA", "TeamC"],
            "away_team_name": ["TeamD", "TeamB"],
            "competition_id": ["test.1", "test.1"],
        })
        result = model.predict_batch(test_df)
        assert "p_home" in result.columns
        assert "p_draw" in result.columns
        assert "p_away" in result.columns
        assert result.height == 2
        # Each row's probabilities sum to 1
        for row in result.iter_rows(named=True):
            total = row["p_home"] + row["p_draw"] + row["p_away"]
            assert total == pytest.approx(1.0, abs=1e-6)

    def test_unfitted_predict_raises(self) -> None:
        """Predict before fit should raise RuntimeError."""
        model = DixonColesModel()
        with pytest.raises(RuntimeError, match="not fitted"):
            model.predict("TeamA", "TeamB")

    def test_missing_columns_raises(self) -> None:
        """Fit with missing columns should raise ValueError."""
        bad_df = pl.DataFrame({"home_team_name": ["A"]})
        model = DixonColesModel()
        with pytest.raises(ValueError, match="Missing required columns"):
            model.fit(bad_df)
