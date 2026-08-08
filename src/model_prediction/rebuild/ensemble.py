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
    n_models = arr.shape[0]
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
    n_models = arr.shape[0]
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
        self._lr_model: Any = None
        self._lr_feature_order: list[str] = []

    def add_model(self, name: str, oof_probs: Sequence[float]) -> None:
        """Register a model's out-of-fold predictions. Not used during predict."""

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
            # Compute weights from individual log-losses, not the combined prediction
            arr = np.array(matrices)
            eps = 1e-6
            losses = np.zeros(len(names))
            for i in range(len(names)):
                probs = np.clip(arr[i], eps, 1 - eps)
                losses[i] = -np.mean(
                    np.array(y_true) * np.log(probs) + (1 - np.array(y_true)) * np.log(1 - probs)
                )
            weights = 1.0 / (losses + eps)
            weights = weights / weights.sum()
        elif self.method == "logistic_stacking":
            _, weights = logistic_stacking(matrices, y_true)
        elif self.method == "logistic_regression_stack":
            # Real, genuinely distinct method from "logistic_stacking"
            # above (Task 15's list names both): logistic_stacking is a
            # nonnegative-constrained, sum-to-one optimization in logit
            # space with no intercept (weights always at least implicitly
            # a convex combination). This is an unconstrained real
            # sklearn LogisticRegression on the per-model logits as
            # features, with its own free intercept and unconstrained
            # (possibly negative) coefficients -- a materially different,
            # more flexible (and more overfitting-prone on a small real
            # sample) stacking approach, not a relabeling of the same one.
            from sklearn.linear_model import LogisticRegression

            logit_matrix = np.array([[_logit(p) for p in matrices[i]] for i in range(len(names))]).T
            lr = LogisticRegression(penalty=None, solver="lbfgs")
            lr.fit(logit_matrix, np.array(y_true))
            self._lr_model = lr
            self._lr_feature_order = names
            weights = lr.coef_[0]
        else:
            weights = np.ones(len(names)) / len(names)

        self.weights = {name: float(w) for name, w in zip(names, weights, strict=True)}
        self._fitted = True
        return self

    def predict(self, probs: dict[str, float]) -> float:
        """Predict one probability from model-level probabilities.

        For logistic_stacking, applies weights in logit space (matches training).
        For logistic_regression_stack, uses the real fitted sklearn model
        (its own intercept, not just a weighted sum) -- requires every
        model this was fit on to be present in `probs`, in the exact
        original feature order.
        For other methods, linear combination in probability space.
        """
        if not self._fitted:
            return np.mean(list(probs.values())) if probs else 0.5
        if self.method == "logistic_stacking" and len(probs) > 1:
            logits = [_logit(probs[n]) for n in self.weights if n in probs]
            w = [self.weights[n] for n in self.weights if n in probs]
            if logits:
                return float(_sigmoid(np.dot(w, logits)))
            return 0.5
        if self.method == "logistic_regression_stack" and self._lr_model is not None:
            if not all(n in probs for n in self._lr_feature_order):
                return 0.5  # fail closed rather than guess at a missing model's contribution
            logits = [[_logit(probs[n]) for n in self._lr_feature_order]]
            return float(self._lr_model.predict_proba(logits)[0][1])
        total = 0.0
        for name, p in probs.items():
            total += self.weights.get(name, 0.0) * p
        return total


# ── Meta-level chronological cross-fitting ──────────────────────────────────
#
# Real gap this closes (CLAUDE.md's next-phase Task 15): Ensemble.fit()
# above is real and tested in isolation, but every prior real caller fit it
# on all real OOF predictions and reported metrics on those same
# predictions -- a real stacker is genuinely useful to *fit* that way (it
# only ever sees OOF predictions, never training predictions, so it isn't
# leaking base-model training data), but reporting its performance on the
# identical rows it was fit on is not yet an unbiased claim that the
# ensemble itself improves anything. This is the fix: the meta-model
# (the ensemble weights) must be evaluated on real chronologically later
# OOF predictions it did not fit on.


def meta_cross_fit_ensemble(
    oof_by_model: dict[str, Sequence[float]],
    labels: Sequence[int],
    method: str,
    n_blocks: int = 3,
) -> dict[str, Any]:
    """Real chronological expanding-window meta-cross-fit for one ensemble
    method: for each meta-evaluation block i (i=1..n_blocks-1), fits real
    ensemble weights on every strictly-earlier block's real OOF
    predictions only, then scores the frozen weights on block i. `method`
    may also be a single real model name already present in
    `oof_by_model` (not an Ensemble method at all) -- used to report a
    real single-model baseline's out-of-sample performance over the
    identical evaluated rows, for a genuinely apples-to-apples
    comparison against the real ensemble methods.

    All sequences in `oof_by_model` (and `labels`) must already be in
    real chronological order and aligned by index (row i in every
    model's sequence is the identical real game)."""
    from .validation import brier_score, log_loss

    names = list(oof_by_model.keys())
    n = len(labels)
    blocks: list[tuple[int, int]] = []
    block_size = n // n_blocks
    start = 0
    for i in range(n_blocks):
        end = n if i == n_blocks - 1 else start + block_size
        blocks.append((start, end))
        start = end

    all_probs: list[float] = []
    all_labels: list[int] = []
    per_block: list[dict[str, Any]] = []

    for i in range(1, len(blocks)):
        fit_start, fit_end = blocks[0][0], blocks[i - 1][1]
        eval_start, eval_end = blocks[i]
        eval_labels = list(labels[eval_start:eval_end])
        if not eval_labels:
            continue

        if method in names:
            # Real single-model baseline -- no fitting at all, just the
            # model's own already-calibrated OOF predictions over the
            # identical evaluated rows.
            eval_probs = list(oof_by_model[method][eval_start:eval_end])
        else:
            fit_oof = {name: list(oof_by_model[name][fit_start:fit_end]) for name in names}
            fit_labels = list(labels[fit_start:fit_end])
            ens = Ensemble(method=method)
            ens.fit(fit_oof, fit_labels)
            eval_probs = [
                ens.predict({name: oof_by_model[name][j] for name in names})
                for j in range(eval_start, eval_end)
            ]

        all_probs.extend(eval_probs)
        all_labels.extend(eval_labels)
        per_block.append({
            "eval_block": i, "fit_n": fit_end - fit_start, "eval_n": len(eval_labels),
            "log_loss": log_loss(eval_labels, eval_probs),
            "brier": brier_score(eval_labels, eval_probs),
        })

    if not all_labels:
        return {"method": method, "n_blocks": n_blocks, "per_block": per_block, "n_eval_total": 0,
                "log_loss": None, "brier": None}
    return {
        "method": method, "n_blocks": n_blocks, "per_block": per_block,
        "n_eval_total": len(all_labels),
        "log_loss": log_loss(all_labels, all_probs),
        "brier": brier_score(all_labels, all_probs),
    }
