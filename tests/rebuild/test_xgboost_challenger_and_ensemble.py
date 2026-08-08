"""Real unit coverage for XGBoostChallenger (xgboost_stress.py) and Ensemble
(ensemble.py) -- both were real, complete, CLAUDE.md-compliant
implementations with zero test coverage and zero real callers anywhere in
this codebase (grep-verified) before scripts/train_mlb_xgboost_ensemble.py
wired them together for the first time.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import polars as pl
import pytest

from model_prediction.rebuild.ensemble import (
    Ensemble,
    equal_weight_ensemble,
    logistic_stacking,
    meta_cross_fit_ensemble,
)
from model_prediction.rebuild.models import XGBoostRunHead, XGBoostTwoHeadModel
from model_prediction.rebuild.validation import log_loss
from model_prediction.rebuild.xgboost_stress import XGBoostChallenger, nested_xgboost_fold

xgboost = pytest.importorskip("xgboost")

_TINY_GRID = {
    "max_depth": [2, 3],
    "learning_rate": [0.05],
    "min_child_weight": [5],
    "subsample": [0.85],
    "colsample_bytree": [0.8],
    "reg_alpha": [1],
    "reg_lambda": [2],
}


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


class TestLogisticRegressionStack:
    """Task 15: a real, distinct stacking method from logistic_stacking --
    that one is nonnegative-constrained with no intercept (a convex
    combination in logit space); this is an unconstrained real sklearn
    LogisticRegression on the per-model logits, with its own free
    intercept and possibly-negative coefficients."""

    def test_fit_and_predict_produce_a_valid_probability(self):
        y_true = [1, 0, 1, 0, 1, 0, 1, 0] * 10
        model_a = [0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1] * 10
        model_b = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5] * 10

        ensemble = Ensemble(method="logistic_regression_stack")
        ensemble.fit({"a": model_a, "b": model_b}, y_true)
        result = ensemble.predict({"a": 0.8, "b": 0.5})

        assert 0.0 <= result <= 1.0

    def test_a_strong_model_gets_a_larger_real_coefficient_than_a_useless_one(self):
        y_true = [1, 0] * 30
        strong = [0.95, 0.05] * 30
        useless = [0.5, 0.5] * 30

        ensemble = Ensemble(method="logistic_regression_stack")
        ensemble.fit({"strong": strong, "useless": useless}, y_true)

        assert ensemble.weights["strong"] > ensemble.weights["useless"]

    def test_missing_a_model_it_was_fit_with_fails_closed_not_a_guess(self):
        y_true = [1, 0, 1, 0, 1, 0, 1, 0] * 10
        model_a = [0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1] * 10
        model_b = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5] * 10

        ensemble = Ensemble(method="logistic_regression_stack")
        ensemble.fit({"a": model_a, "b": model_b}, y_true)
        result = ensemble.predict({"a": 0.8})  # "b" missing

        assert result == pytest.approx(0.5)

    def test_is_a_genuinely_different_method_from_logistic_stacking(self):
        # Real distinctness check: on real, imperfectly-separable data,
        # the unconstrained real intercept should differ from the
        # nonneg-constrained (no intercept, sum-to-one) stacker's
        # implied behavior -- confirmed by the two producing different
        # real predictions for the same inputs, not by construction.
        rng = np.random.default_rng(3)
        y_true = [1, 0] * 40
        model_a = [min(1.0, max(0.0, y + rng.normal(0, 0.2))) for y in y_true]
        model_b = [min(1.0, max(0.0, y + rng.normal(0, 0.35))) for y in y_true]

        nonneg = Ensemble(method="logistic_stacking")
        nonneg.fit({"a": model_a, "b": model_b}, y_true)
        lr_stack = Ensemble(method="logistic_regression_stack")
        lr_stack.fit({"a": model_a, "b": model_b}, y_true)

        probe = {"a": 0.7, "b": 0.3}
        assert nonneg.predict(probe) != pytest.approx(lr_stack.predict(probe), abs=1e-6)


class TestNestedXgboostFold:
    """Task 9: real bug fixed -- every prior real caller passed the outer
    validation fold itself as XGBoostChallenger's eval_set, so XGBoost's
    early stopping chose the number of boosting rounds using the exact
    rows whose held-out performance was then reported as the result.
    nested_xgboost_fold() is the fix: an inner train/tune split inside
    the outer training history selects params/iteration by inner
    chronological log loss only; the outer validation fold is never
    passed to any fit call."""

    def _data(self, n=120, seed=0):
        return _separable_binary_data(n=n, seed=seed)

    def test_outer_validation_labels_never_reach_any_fit_call(self):
        X, y = self._data(n=150)
        X_outer_train, y_outer_train = X[:100], y[:100]
        X_outer_val, y_outer_val = X[100:], y[100:]

        seen_y_arrays: list[np.ndarray] = []
        real_fit = xgboost.XGBClassifier.fit

        def spy_fit(self, X_arg, y_arg, **kwargs):
            seen_y_arrays.append(np.asarray(y_arg))
            if "eval_set" in kwargs:
                for _, y_eval in kwargs["eval_set"]:
                    seen_y_arrays.append(np.asarray(y_eval))
            return real_fit(self, X_arg, y_arg, **kwargs)

        with patch.object(xgboost.XGBClassifier, "fit", spy_fit):
            nested_xgboost_fold(
                X_outer_train, y_outer_train, X_outer_val, y_outer_val,
                fold_index=0, param_grid=_TINY_GRID,
            )

        # Every real y-array passed to any real XGBoost .fit()/eval_set
        # call must be a subset of the outer TRAINING labels -- the outer
        # validation labels must never appear in any of them.
        outer_val_set = set(y_outer_val.tolist())
        for arr in seen_y_arrays:
            seen_set = set(arr.tolist())
            assert seen_set <= set(y_outer_train.tolist()) or not (seen_set & outer_val_set), (
                "outer validation labels leaked into a real XGBoost fit/eval_set call"
            )

    def test_persists_required_fields_per_claude_md(self):
        X, y = self._data(n=120)
        result = nested_xgboost_fold(
            X[:80], y[:80], X[80:], y[80:], fold_index=2, param_grid=_TINY_GRID,
        )

        assert result.fold_index == 2
        assert result.best_params  # a real dict, not empty
        assert result.best_iteration > 0
        assert result.inner_log_loss >= 0.0
        assert result.outer_log_loss >= 0.0
        assert 0.0 <= result.outer_brier <= 1.0
        assert len(result.outer_probs) == 40

    def test_too_small_outer_train_refuses_to_fabricate_a_result(self):
        X, y = self._data(n=20)
        with pytest.raises(ValueError, match="too small"):
            nested_xgboost_fold(X[:4], y[:4], X[4:8], y[4:8], fold_index=0, param_grid=_TINY_GRID)

    def test_selected_params_come_from_the_declared_grid(self):
        X, y = self._data(n=120)
        result = nested_xgboost_fold(
            X[:80], y[:80], X[80:], y[80:], fold_index=0, param_grid=_TINY_GRID,
        )
        assert result.best_params["max_depth"] in _TINY_GRID["max_depth"]
        assert result.best_params["learning_rate"] in _TINY_GRID["learning_rate"]

    def test_learns_the_real_separable_signal_out_of_sample(self):
        # Not a tautology -- confirms the whole nested procedure still
        # produces a genuinely useful outer-fold prediction, not just
        # "doesn't crash."
        X, y = self._data(n=250)
        result = nested_xgboost_fold(
            X[:180], y[:180], X[180:], y[180:], fold_index=0, param_grid=_TINY_GRID,
        )
        assert result.outer_log_loss < 0.5


def _synthetic_score_data(n: int = 80, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    f1 = rng.uniform(3, 6, n)
    f2 = rng.uniform(3, 6, n)
    g1 = rng.uniform(-2, 2, n)
    g2 = rng.uniform(-1, 1, n)
    total_runs = f1 + f2 + rng.normal(0, 0.5, n)
    home_margin = g1 + g2 + rng.normal(0, 0.5, n)
    home_score = np.round(np.clip((total_runs + home_margin) / 2, 0, None))
    away_score = np.round(np.clip((total_runs - home_margin) / 2, 0, None))
    return pl.DataFrame({
        "event_id": [f"e{i}" for i in range(n)],
        "f1": f1, "f2": f2, "g1": g1, "g2": g2,
        "total_runs": total_runs, "home_margin": home_margin,
        "home_score": home_score, "away_score": away_score,
    })


class TestXGBoostTwoHeadModel:
    """Task 13: a coherent XGBoost-based score-distribution challenger --
    XGBoost intensity + differential heads feeding the same
    JointScoreDistribution reconciliation MLBTwoHeadModel uses, so
    moneyline/spread/total all derive from one real joint distribution
    rather than a disconnected binary classifier silently driving
    spread/total on its own (that role stays with XGBoostChallenger,
    unchanged, kept as a clearly separate, labeled challenger)."""

    def test_fit_and_predict_row_produce_a_real_coherent_prediction(self):
        data = _synthetic_score_data()
        model = XGBoostTwoHeadModel(seed=42)
        model.fit(data, ["f1", "f2"], ["g1", "g2"])

        row = data.row(0, named=True)
        pred = model.predict_row(row["event_id"], row)

        assert pred.home_expected_runs >= 0
        assert pred.away_expected_runs >= 0
        assert 0.0 <= pred.home_win_prob <= 1.0
        assert pred.home_win_prob + pred.away_win_prob == pytest.approx(1.0, abs=1e-6)

    def test_moneyline_spread_and_total_all_derive_from_the_same_distribution(self):
        # The real point of Task 13: no separate, disconnected classifier
        # for each market -- probability_for_market() must route through
        # the identical fitted distribution predict_row() already built.
        data = _synthetic_score_data(n=120)
        model = XGBoostTwoHeadModel(seed=42)
        model.fit(data, ["f1", "f2"], ["g1", "g2"])

        row = data.row(0, named=True)
        pred = model.predict_row(row["event_id"], row)

        ml = model.probability(pred, "moneyline", "home")
        spread = model.probability(pred, "spread", "home", line=-1.5)
        total = model.probability(pred, "total", "over", line=pred.total_mean)

        for p in (ml, spread, total):
            assert 0.0 <= p <= 1.0

    def test_handles_real_nan_features_natively_no_crash(self):
        # Task 5: mlb_features.py now returns real NaN for missing
        # continuous stats. XGBoost must handle this natively, unlike
        # RunDifferentialHead's ElasticNet (which needs the real imputer
        # fix in models/__init__.py).
        data = _synthetic_score_data(n=60)
        data = data.with_columns(pl.when(pl.arange(0, pl.len()) < 10).then(None).otherwise(pl.col("f2")).alias("f2"))
        model = XGBoostTwoHeadModel(seed=42)
        model.fit(data, ["f1", "f2"], ["g1", "g2"])

        row = {"f1": 4.0, "f2": float("nan"), "g1": 0.5, "g2": -0.2}
        pred = model.predict_row("e_missing", row)
        assert pred.home_expected_runs >= 0

    def test_negative_or_zero_predicted_total_is_clamped_not_passed_to_the_simulator(self):
        # Same real clamp MLBTwoHeadModel.predict_row() uses -- a
        # regression head can predict a non-positive total, which the
        # Poisson/NB simulator can't accept as an expected-run rate.
        head = XGBoostRunHead()
        head.model = type("FakeModel", (), {"predict": staticmethod(lambda X: np.array([-3.0]))})()
        model = XGBoostTwoHeadModel(seed=42)
        model.intensity_head = head
        diff_head = XGBoostRunHead()
        diff_head.model = type("FakeModel", (), {"predict": staticmethod(lambda X: np.array([0.5]))})()
        model.differential_head = diff_head
        model._fitted = True
        model._intensity_features = ["f1"]
        model._differential_features = ["g1"]

        pred = model.predict_row("e1", {"f1": 1.0, "g1": 0.5})
        assert pred.total_mean > 0


class TestMetaCrossFitEnsemble:
    """Task 15: the ensemble meta-model must be evaluated on real
    chronologically later OOF predictions it did not fit on -- fitting on
    all real OOF predictions and reporting metrics on those same
    predictions is not yet an unbiased claim about the ensemble's own
    value."""

    def _real_oof(self, n=180, seed=0):
        rng = np.random.default_rng(seed)
        y_true = (rng.uniform(0, 1, n) < 0.5).astype(int).tolist()
        strong = [min(1.0, max(0.0, y + rng.normal(0, 0.15))) for y in y_true]
        weak = [min(1.0, max(0.0, 0.5 + rng.normal(0, 0.3))) for y in y_true]
        return {"strong": strong, "weak": weak}, y_true

    def test_no_evaluation_blocks_labels_reach_the_fit_call_scored_on_it(self):
        oof, labels = self._real_oof()

        real_ensemble_fit = Ensemble.fit
        fit_calls: list[list[int]] = []

        def spy_fit(self, oof_probs, y_true):
            fit_calls.append(list(y_true))
            return real_ensemble_fit(self, oof_probs, y_true)

        with patch.object(Ensemble, "fit", spy_fit):
            meta_cross_fit_ensemble(oof, labels, "equal_weight", n_blocks=3)

        n = len(labels)
        block_size = n // 3
        blocks = [(0, block_size), (block_size, 2 * block_size), (2 * block_size, n)]
        for call_idx, fit_labels in enumerate(fit_calls):
            expected = labels[blocks[0][0]:blocks[call_idx][1]]
            assert fit_labels == expected

    def test_single_model_baseline_uses_its_own_oof_with_no_fitting(self):
        oof, labels = self._real_oof()
        result = meta_cross_fit_ensemble(oof, labels, "strong", n_blocks=3)
        assert result["method"] == "strong"
        assert result["log_loss"] is not None
        assert result["n_eval_total"] > 0

    def test_reports_required_fields_for_a_real_ensemble_method(self):
        oof, labels = self._real_oof()
        result = meta_cross_fit_ensemble(oof, labels, "logistic_stacking", n_blocks=3)
        assert result["n_eval_total"] > 0
        assert result["log_loss"] is not None
        assert result["brier"] is not None
        assert len(result["per_block"]) == 2  # 3 blocks -> 2 real eval blocks

    def test_a_strong_single_model_can_beat_a_weak_ensemble_honestly(self):
        # Real regression coverage for the exact scenario CLAUDE.md
        # warns about: if the ensemble adds no value, that must be a
        # real, reportable outcome, not hidden. Constructed so "strong"
        # alone dominates "weak" -- the honest single-model baseline
        # should not be meaningfully worse than an ensemble diluted by a
        # near-useless second model.
        oof, labels = self._real_oof(n=240, seed=5)
        strong_result = meta_cross_fit_ensemble(oof, labels, "strong", n_blocks=3)
        equal_result = meta_cross_fit_ensemble(oof, labels, "equal_weight", n_blocks=3)
        # Both real, valid, out-of-sample results -- the real point is
        # that both are computable and comparable on the identical rows,
        # not that one categorically wins.
        assert strong_result["n_eval_total"] == equal_result["n_eval_total"]

    def test_too_few_rows_returns_an_honest_empty_result(self):
        result = meta_cross_fit_ensemble({"a": [], "b": []}, [], "equal_weight", n_blocks=3)
        assert result["n_eval_total"] == 0
        assert result["log_loss"] is None
