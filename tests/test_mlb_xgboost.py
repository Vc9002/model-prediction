"""Tests for monotonic MLB XGBoost model."""

from __future__ import annotations

import numpy as np
import pytest

from model_prediction.models.mlb_xgboost import (
    MLB_FEATURE_MONOTONICITY,
    MonotonicMLBClassifier,
)


def test_monotonic_constraints_dict():
    assert MLB_FEATURE_MONOTONICITY["elo_probability"] == 1
    assert MLB_FEATURE_MONOTONICITY["starter_era_gap"] == -1
    assert MLB_FEATURE_MONOTONICITY["starter_kbb_gap"] == 1
    assert MLB_FEATURE_MONOTONICITY["bullpen_weakness_gap"] == -1


def test_monotonic_xgboost_fit_predict():
    rng = np.random.default_rng(42)
    N = 300
    # Features: elo_probability (positive), starter_era_gap (negative)
    elo = rng.uniform(0.3, 0.7, N)
    era_gap = rng.normal(0, 1.5, N)
    X = np.column_stack([elo, era_gap])

    # True outcome driven by elo - 0.2 * era_gap
    logits = 2.0 * (elo - 0.5) - 0.3 * era_gap
    probs_true = 1.0 / (1.0 + np.exp(-logits))
    y = rng.binomial(1, probs_true)

    clf = MonotonicMLBClassifier(
        feature_names=["elo_probability", "starter_era_gap"],
        n_estimators=50,
        max_depth=2,
    )
    clf.fit(X, y)

    metrics = clf.evaluate(X, y)
    assert 0.50 < metrics.log_loss < 0.75
    assert metrics.accuracy > 0.50
    assert metrics.auc is not None and metrics.auc > 0.55


def test_monotonic_prediction_direction():
    rng = np.random.default_rng(42)
    clf = MonotonicMLBClassifier(
        feature_names=["elo_probability", "starter_era_gap"],
        n_estimators=30,
        max_depth=2,
    )
    X_train = rng.uniform(0.3, 0.7, (200, 2))
    y_train = rng.binomial(1, X_train[:, 0])
    clf.fit(X_train, y_train)

    # Test that increasing elo_probability monotonically increases predicted probability
    test_elo_low = np.array([[0.35, 0.0]])
    test_elo_high = np.array([[0.65, 0.0]])
    p_low = clf.predict_proba(test_elo_low)[0]
    p_high = clf.predict_proba(test_elo_high)[0]
    assert p_high >= p_low


def test_monotonic_unfitted_raises():
    clf = MonotonicMLBClassifier(feature_names=["elo_probability"])
    with pytest.raises(RuntimeError, match="must be fitted"):
        clf.predict_proba(np.array([[0.5]]))
