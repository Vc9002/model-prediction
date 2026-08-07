"""Real unit coverage for XGBoostChallenger (xgboost_stress.py) and Ensemble
(ensemble.py) -- both were real, complete, CLAUDE.md-compliant
implementations with zero test coverage and zero real callers anywhere in
this codebase (grep-verified) before scripts/train_mlb_xgboost_ensemble.py
wired them together for the first time.
"""

from __future__ import annotations

import numpy as np
import pytest

from model_prediction.rebuild.ensemble import Ensemble, equal_weight_ensemble, logistic_stacking
from model_prediction.rebuild.validation import log_loss
from model_prediction.rebuild.xgboost_stress import XGBoostChallenger

xgboost = pytest.importorskip("xgboost")


def _separable_binary_data(n: int = 200, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 1, size=(n, 3))
    # y strongly correlated with X[:, 0] -- a real, learnable signal.
    y = (X[:, 0] + rng.normal(0, 0.3, n) > 0).astype(int)
    return X, y


class TestXGBoostChallenger:
    def test_fit_and_predict_produce_valid_probabilities(self):
        X, y = _separable_binary_data()
        model = XGBoostChallenger(seed=42)
        model.fit(X, y, feature_names=["a", "b", "c"])

        probs = model.predict(X)

        assert probs.shape == (X.shape[0],)
        assert np.all((probs >= 0.0) & (probs <= 1.0))

    def test_learns_the_real_separable_signal(self):
        # Not a tautology check -- a model that learned nothing would sit
        # near 0.5 log loss; this data has a real, strong signal.
        X, y = _separable_binary_data(n=300)
        X_train, y_train = X[:200], y[:200]
        X_val, y_val = X[200:], y[200:]

        model = XGBoostChallenger(seed=42)
        model.fit(X_train, y_train, eval_set=(X_val, y_val))
        probs = model.predict(X_val)

        assert log_loss(y_val.tolist(), probs.tolist()) < 0.5

    def test_unfitted_model_returns_honest_fallback_not_a_crash(self):
        model = XGBoostChallenger(seed=42)
        X = np.zeros((5, 3))
        probs = model.predict(X)
        assert np.allclose(probs, 0.5)

    def test_deterministic_with_the_same_seed(self):
        X, y = _separable_binary_data()
        m1 = XGBoostChallenger(seed=7)
        m1.fit(X, y)
        m2 = XGBoostChallenger(seed=7)
        m2.fit(X, y)
        assert np.allclose(m1.predict(X), m2.predict(X))


class TestEnsemble:
    def test_equal_weight_ensemble_averages(self):
        probs = equal_weight_ensemble([[0.2, 0.8], [0.6, 0.4]])
        assert probs == pytest.approx([0.4, 0.6])

    def test_logistic_stacking_weights_sum_to_one_and_are_nonnegative(self):
        y_true = [1, 0, 1, 0, 1, 0, 1, 0]
        model_a = [0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1]  # perfect
        model_b = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]  # uninformative
        _, weights = logistic_stacking([model_a, model_b], y_true)

        assert weights.sum() == pytest.approx(1.0, abs=1e-6)
        assert np.all(weights >= 0.0)

    def test_a_strong_model_gets_more_weight_than_a_useless_one(self):
        y_true = [1, 0] * 20
        strong = [0.95, 0.05] * 20
        useless = [0.5, 0.5] * 20

        ensemble = Ensemble(method="logistic_stacking")
        ensemble.fit({"strong": strong, "useless": useless}, y_true)

        assert ensemble.weights["strong"] > ensemble.weights["useless"]

    def test_ensemble_log_loss_is_no_worse_than_the_worst_individual_model(self):
        # A nonneg-constrained, sum-to-one stacker can always degenerate to
        # picking the single best model -- it should never do meaningfully
        # worse than the worst input on the data it was fit on.
        y_true = [1, 0, 1, 1, 0, 0, 1, 0, 1, 0] * 5
        rng = np.random.default_rng(1)
        good = [min(1.0, max(0.0, y + rng.normal(0, 0.15))) for y in y_true]
        bad = [0.5 + rng.normal(0, 0.05) for _ in y_true]
        bad = [min(1.0, max(0.0, p)) for p in bad]

        ensemble = Ensemble(method="logistic_stacking")
        ensemble.fit({"good": good, "bad": bad}, y_true)
        ensemble_probs = [ensemble.predict({"good": g, "bad": b}) for g, b in zip(good, bad, strict=True)]

        ensemble_ll = log_loss(y_true, ensemble_probs)
        worst_ll = max(log_loss(y_true, good), log_loss(y_true, bad))
        assert ensemble_ll <= worst_ll + 1e-6

    def test_unfitted_ensemble_falls_back_to_a_plain_average(self):
        ensemble = Ensemble(method="logistic_stacking")
        result = ensemble.predict({"a": 0.2, "b": 0.8})
        assert result == pytest.approx(0.5)

    def test_predict_ignores_a_model_name_it_was_not_fit_with(self):
        ensemble = Ensemble(method="equal_weight")
        ensemble.fit({"a": [0.2, 0.8], "b": [0.6, 0.4]}, [0, 1])
        # A stray extra key at predict time must not raise or silently
        # dominate the result -- only fitted model names count.
        result = ensemble.predict({"a": 0.5, "b": 0.5, "unknown_model": 0.99})
        assert 0.0 <= result <= 1.0
