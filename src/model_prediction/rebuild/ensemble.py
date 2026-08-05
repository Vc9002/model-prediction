"""Out-of-fold ensemble — equal-weight, inverse-log-loss, nonnegative stacking.

The stacker sees only out-of-fold predictions, never training predictions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy.optimize import minimize


def _logit(p: float) -> float:
    clipped = max(1e-12, min(1 - 1e-12, p))
    return np.log(clipped / (1 - clipped))


def _sigmoid(x):
    """Vectorized sigmoid — works on scalars and arrays."""
    return 1.0 / (1.0 + np.exp(-np.clip(x, -500, 500)))


def equal_weight_ensemble(prob_matrices: Sequence[Sequence[float]]) -> list[float]:
    """Simple average of all model probabilities."""
    arr = np.array(prob_matrices)  # shape: (n_models, n_samples)
    return arr.mean(axis=0).tolist()


def inverse_log_loss_weights(
    prob_matrices: Sequence[Sequence[float]],
    y_true: Sequence[int],
) -> list[float]:
    """Weight models by inverse of their individual log loss.

    w_i ∝ 1 / (log_loss_i + epsilon)
    """
    arr = np.array(prob_matrices)
    eps = 1e-6
    n_models, n_samples = arr.shape
    losses = np.zeros(n_models)
    for i in range(n_models):
        probs = np.clip(arr[i], eps, 1 - eps)
        losses[i] = -np.mean(
            np.array(y_true) * np.log(probs) + (1 - np.array(y_true)) * np.log(1 - probs)
        )
    weights = 1.0 / (losses + eps)
    weights /= weights.sum()
    weighted = np.dot(weights, arr)
    return weighted.tolist()


def logistic_stacking(
    prob_matrices: Sequence[Sequence[float]],
    y_true: Sequence[int],
) -> tuple[list[float], np.ndarray]:
    """Nonnegative constrained stacking on logits.

    Returns (ensemble_probs, weights).
    Weights are constrained to be nonnegative and sum to 1.
    """
    arr = np.array(prob_matrices)  # (n_models, n_samples)
    n_models, n_samples = arr.shape
    yt = np.array(y_true)

    # Stack on logits
    logit_arr = np.array([[_logit(p) for p in row] for row in arr])  # (n_models, n_samples)

    def objective(w: np.ndarray) -> float:
        stacked_logits = np.dot(w, logit_arr)
        probs = np.clip(_sigmoid(stacked_logits), 1e-12, 1 - 1e-12)
        return float(-np.mean(yt * np.log(probs) + (1 - yt) * np.log(1 - probs)))

    constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    bounds = [(0.0, 1.0) for _ in range(n_models)]
    w0 = np.ones(n_models) / n_models

    result = minimize(objective, w0, method="SLSQP", bounds=bounds, constraints=constraints)
    weights = result.x
    weights = np.maximum(weights, 0)
    weights /= weights.sum()

    stacked_logits = np.dot(weights, logit_arr)
    probs = _sigmoid(stacked_logits).tolist()
    return probs, weights


class Ensemble:
    """Chronological out-of-fold ensemble.

    Usage:
        ens = Ensemble()
        ens.add_model("statistical", stat_probs)
        ens.add_model("gradient_boosting", gbm_probs)
        result = ens.fit(oof_probs_dict, y_true)
        cal_probs = ens.predict(oof_probs_dict)
    """

    def __init__(self, method: str = "logistic_stacking") -> None:
        self.method = method
        self.weights: dict[str, float] = {}
        self._fitted = False

    def add_model(self, name: str, oof_probs: Sequence[float]) -> None:
        """Register a model's out-of-fold predictions. Not used during predict."""
        pass

    def fit(
        self, oof_probs: dict[str, Sequence[float]], y_true: Sequence[int],
    ) -> Ensemble:
        """Fit ensemble weights from out-of-fold predictions."""
        if len(oof_probs) < 1:
            return self
        names = list(oof_probs.keys())
        matrices = [oof_probs[n] for n in names]

        if self.method == "equal_weight":
            weights = np.ones(len(names)) / len(names)
        elif self.method == "inverse_log_loss":
            weights_arr = np.array(inverse_log_loss_weights(matrices, y_true))
            weights = weights_arr / weights_arr.sum()
        elif self.method == "logistic_stacking":
            _, weights = logistic_stacking(matrices, y_true)
        else:
            weights = np.ones(len(names)) / len(names)

        self.weights = {name: float(w) for name, w in zip(names, weights)}
        self._fitted = True
        return self

    def predict(self, probs: dict[str, float]) -> float:
        """Predict one probability from model-level probabilities."""
        if not self._fitted:
            return np.mean(list(probs.values())) if probs else 0.5
        total = 0.0
        for name, p in probs.items():
            total += self.weights.get(name, 0.0) * p
        return total
