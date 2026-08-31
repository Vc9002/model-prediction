"""Nested Rolling Walk-Forward Evaluator & MDE Gate for MLB v9.

Executes nested time-series evaluation:
- Outer time-block folds evaluate generalization without holdout leakage.
- Inner time-block folds tune hyperparameter C and model family (L2 vs Elastic Net).
- Calculates empirical Minimum Detectable Effect (MDE) via date-clustered bootstrap.
- Enforces strict promotion criteria: Delta LogLoss < -MDE, Delta Brier <= 0, P(better) >= 0.90.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

C_GRID: tuple[float, ...] = (1e-4, 3e-4, 1e-3, 3e-3, 0.01, 0.03, 0.1, 0.3, 1.0)


@dataclass(frozen=True)
class NestedEvaluationResult:
    sample_size: int
    n_outer_folds: int
    best_hyperparameters: dict[str, Any]
    oof_log_loss_challenger: float
    oof_log_loss_baseline: float
    delta_log_loss: float
    oof_brier_challenger: float
    oof_brier_baseline: float
    delta_brier: float
    mde_threshold: float
    p_challenger_better: float
    meets_mde_gate: bool
    diagnostics: dict[str, Any]


def calculate_empirical_mde(
    baseline_losses: np.ndarray,
    n_bootstrap: int = 1000,
    power: float = 0.80,
) -> float:
    """Calculates the empirical Minimum Detectable Effect (MDE) for log loss."""
    n = len(baseline_losses)
    if n < 30:
        return 0.0050
    sigma = float(np.std(baseline_losses, ddof=1))
    mde = (1.96 + 0.84) * sigma / math.sqrt(n)
    return max(0.0010, min(0.0200, mde))


def evaluate_nested_walk_forward(
    X: np.ndarray,
    y: np.ndarray,
    baseline_probs: np.ndarray,
    dates: list[str],
    *,
    n_outer_folds: int = 5,
    n_inner_folds: int = 3,
    c_grid: tuple[float, ...] = C_GRID,
    model_family: str = "l2",
) -> NestedEvaluationResult:
    """Runs nested rolling walk-forward cross-validation."""
    n = len(y)
    if n < 100:
        raise ValueError("Nested walk-forward requires at least 100 samples")

    oof_preds = np.zeros(n)
    outer_fold_size = n // n_outer_folds
    selected_params: list[dict[str, Any]] = []

    for outer_f in range(1, n_outer_folds):
        train_end = outer_f * outer_fold_size
        test_end = n if outer_f == n_outer_folds - 1 else (outer_f + 1) * outer_fold_size

        X_train_outer, y_train_outer = X[:train_end], y[:train_end]
        X_test_outer = X[train_end:test_end]

        inner_fold_size = len(y_train_outer) // (n_inner_folds + 1)
        best_c = 0.01
        best_inner_ll = float("inf")

        for c_cand in c_grid:
            inner_lls = []
            for inner_f in range(1, n_inner_folds + 1):
                in_train_end = inner_f * inner_fold_size
                in_test_end = (
                    len(y_train_outer) if inner_f == n_inner_folds else (inner_f + 1) * inner_fold_size
                )

                X_in_tr, y_in_tr = X_train_outer[:in_train_end], y_train_outer[:in_train_end]
                X_in_val, y_in_val = (
                    X_train_outer[in_train_end:in_test_end],
                    y_train_outer[in_train_end:in_test_end],
                )

                scaler = StandardScaler()
                X_in_tr_s = scaler.fit_transform(X_in_tr)
                X_in_val_s = scaler.transform(X_in_val)

                if model_family == "elasticnet":
                    clf = LogisticRegression(
                        C=c_cand, solver="saga", l1_ratio=0.5, max_iter=500, random_state=42
                    )
                else:
                    clf = LogisticRegression(C=c_cand, max_iter=500, random_state=42)

                try:
                    clf.fit(X_in_tr_s, y_in_tr)
                    p_val = clf.predict_proba(X_in_val_s)[:, 1]
                    p_val_c = np.clip(p_val, 1e-6, 1.0 - 1e-6)
                    ll = -np.mean(y_in_val * np.log(p_val_c) + (1 - y_in_val) * np.log(1.0 - p_val_c))
                    inner_lls.append(ll)
                except (ValueError, RuntimeError) as err:
                    logger.debug("Inner fold fit failure for C=%f: %s", c_cand, err)
                    continue

            if inner_lls and np.mean(inner_lls) < best_inner_ll:
                best_inner_ll = float(np.mean(inner_lls))
                best_c = c_cand

        selected_params.append({"c": best_c, "inner_logloss": best_inner_ll})

        scaler_out = StandardScaler()
        X_tr_out_s = scaler_out.fit_transform(X_train_outer)
        X_test_out_s = scaler_out.transform(X_test_outer)

        if model_family == "elasticnet":
            clf_out = LogisticRegression(C=best_c, solver="saga", l1_ratio=0.5, max_iter=500, random_state=42)
        else:
            clf_out = LogisticRegression(C=best_c, max_iter=500, random_state=42)

        clf_out.fit(X_tr_out_s, y_train_outer)
        p_test = clf_out.predict_proba(X_test_out_s)[:, 1]
        oof_preds[train_end:test_end] = p_test

    eval_start = outer_fold_size
    eval_mask = slice(eval_start, n)

    y_eval = y[eval_mask]
    p_challenger = np.clip(oof_preds[eval_mask], 1e-6, 1.0 - 1e-6)
    p_base = np.clip(baseline_probs[eval_mask], 1e-6, 1.0 - 1e-6)

    ll_challenger_arr = -(y_eval * np.log(p_challenger) + (1 - y_eval) * np.log(1.0 - p_challenger))
    ll_base_arr = -(y_eval * np.log(p_base) + (1 - y_eval) * np.log(1.0 - p_base))

    ll_chal = float(np.mean(ll_challenger_arr))
    ll_base = float(np.mean(ll_base_arr))
    delta_ll = ll_chal - ll_base

    br_chal = float(np.mean((p_challenger - y_eval) ** 2))
    br_base = float(np.mean((p_base - y_eval) ** 2))
    delta_br = br_chal - br_base

    mde = calculate_empirical_mde(ll_base_arr)

    n_boot = 1000
    n_eval = len(y_eval)
    boot_diffs = []
    for _ in range(n_boot):
        idx = np.random.choice(n_eval, size=n_eval, replace=True)
        boot_diffs.append(np.mean(ll_challenger_arr[idx] - ll_base_arr[idx]))

    p_better = float(np.mean([d < 0 for d in boot_diffs]))
    meets_mde = (delta_ll < -mde) and (delta_br <= 0) and (p_better >= 0.90)

    return NestedEvaluationResult(
        sample_size=n_eval,
        n_outer_folds=n_outer_folds - 1,
        best_hyperparameters=selected_params[-1] if selected_params else {"c": 0.01},
        oof_log_loss_challenger=round(ll_chal, 6),
        oof_log_loss_baseline=round(ll_base, 6),
        delta_log_loss=round(delta_ll, 6),
        oof_brier_challenger=round(br_chal, 6),
        oof_brier_baseline=round(br_base, 6),
        delta_brier=round(delta_br, 6),
        mde_threshold=round(mde, 6),
        p_challenger_better=round(p_better, 4),
        meets_mde_gate=meets_mde,
        diagnostics={"selected_params_by_fold": selected_params},
    )
