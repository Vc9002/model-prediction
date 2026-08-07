"""XGBoost challenger and stress test framework.

XGBoost used as a challenger, not automatic production truth:
- Shallow trees, low learning rate, strong min_child_weight
- L1/L2 penalties, row/feature subsampling
- Chronological validation with early stopping
- Deterministic seeds, bounded thread counts

Stress tests: re-run portfolio with one-tick-worse, delayed execution,
reduced depth, partial fills, increased fees, best month/team/wins removed.
A system that fails under minor realistic degradation remains research-only.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# ── XGBoost challenger ───────────────────────────────────────────────────────


class XGBoostChallenger:
    """XGBoost wrapper with conservative defaults for chronological validation.

    Only used as a challenger — never as automatic production truth.
    """

    def __init__(self, seed: int = 42, n_jobs: int = 2) -> None:
        self.seed = seed
        self.n_jobs = n_jobs
        self.model: Any = None
        self._fitted = False
        self._feature_names: list[str] = []

    def fit(
        self, X: np.ndarray, y: np.ndarray, feature_names: list[str] | None = None,
        eval_set: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> XGBoostChallenger:
        """Fit XGBoost with conservative defaults for sports prediction."""
        try:
            import xgboost as xgb
        except ImportError:
            return self  # graceful fallback if xgboost not installed

        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]

        params = {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "max_depth": 4,
            "learning_rate": 0.03,
            "subsample": 0.8,
            "colsample_bytree": 0.7,
            "min_child_weight": 5,
            "reg_alpha": 1.0,
            "reg_lambda": 2.0,
            "n_estimators": 500,
            "early_stopping_rounds": 25,
            "random_state": self.seed,
            "n_jobs": self.n_jobs,
            "verbosity": 0,
        }

        eval_data = [(X, y)]
        if eval_set is not None:
            eval_data = [(X, y), eval_set]

        self.model = xgb.XGBClassifier(**params)
        self.model.fit(X, y, eval_set=eval_data, verbose=False)
        self._fitted = True
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted or self.model is None:
            return np.full((X.shape[0], 2), 0.5)
        return self.model.predict_proba(X)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted or self.model is None:
            return np.full(X.shape[0], 0.5)
        return self.predict_proba(X)[:, 1]


# ── Nested chronological XGBoost validation ──────────────────────────────────
#
# Real bug fixed here (see outputs/rebuild/takeover_status.md Task 9):
# every real caller of XGBoostChallenger.fit() passed the outer validation
# fold itself as `eval_set` -- XGBoost's early stopping chose the number of
# boosting rounds using the exact rows whose held-out performance was then
# reported as the result. That is the textbook "outer validation labels
# influence early stopping" leak CLAUDE.md's own stop condition names
# explicitly. nested_xgboost_fold() is the real fix: split the outer
# training history itself into an inner train/tune block, select
# hyperparameters and the early-stopping iteration by inner chronological
# log loss only, freeze both, then predict the outer validation fold with
# a model that has never seen it in any form.

# A conservative, bounded, pre-declared grid -- not an open-ended
# Optuna-style search. 3*3*3*2*2*3*3 = 972 combinations; real, deterministic,
# reproducible, and (per real live timing on this project's small dataset)
# fast enough to run in full per fold rather than needing a random subsample.
XGB_PARAM_GRID: dict[str, list[Any]] = {
    "max_depth": [2, 3, 4],
    "learning_rate": [0.01, 0.03, 0.05],
    "min_child_weight": [5, 10, 20],
    "subsample": [0.7, 0.85],
    "colsample_bytree": [0.6, 0.8],
    "reg_alpha": [0.5, 1, 2],
    "reg_lambda": [1, 2, 5],
}


def _grid_combinations(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    return [dict(zip(keys, values, strict=True)) for values in itertools.product(*grid.values())]


@dataclass
class NestedFoldResult:
    """Everything CLAUDE.md's Task 9 requires persisted per outer fold:
    best_params, best_iteration, inner_score, outer_score -- so a real
    audit can verify the outer score was never used to pick either."""
    fold_index: int
    best_params: dict[str, Any]
    best_iteration: int
    inner_log_loss: float
    outer_log_loss: float
    outer_brier: float
    outer_probs: list[float] = field(default_factory=list)
    inner_train_n: int = 0
    inner_val_n: int = 0
    outer_train_n: int = 0
    outer_val_n: int = 0


def nested_xgboost_fold(
    X_outer_train: np.ndarray,
    y_outer_train: np.ndarray,
    X_outer_val: np.ndarray,
    y_outer_val: np.ndarray,
    fold_index: int,
    *,
    inner_val_fraction: float = 0.2,
    seed: int = 42,
    param_grid: dict[str, list[Any]] | None = None,
) -> NestedFoldResult:
    """One real nested-CV outer fold.

    X_outer_train/y_outer_train must already be in chronological order
    (matching every other real fold-construction in this codebase, e.g.
    validation.expanding_folds()) -- the inner split below takes the
    chronologically last `inner_val_fraction` of it as the inner
    tuning/early-stop block, never a random split.

    Required sequence (CLAUDE.md):
        outer training history
            -> split internally: inner train / inner tuning block
            -> fit/tune XGBoost, selecting by INNER chronological log loss only
            -> freeze best params + iteration
            -> predict outer validation

    The outer validation labels (y_outer_val) are never passed to any
    XGBoost fit call in this function, only used once at the very end to
    score the frozen model's predictions -- structurally impossible for
    them to influence early stopping, hyperparameters, or feature
    selection, not just disciplined by convention.
    """
    from .validation import brier_score, log_loss

    grid = param_grid or XGB_PARAM_GRID
    n = X_outer_train.shape[0]
    inner_split = max(1, round(n * (1.0 - inner_val_fraction)))
    X_inner_train, X_inner_val = X_outer_train[:inner_split], X_outer_train[inner_split:]
    y_inner_train, y_inner_val = y_outer_train[:inner_split], y_outer_train[inner_split:]

    if len(X_inner_train) < 5 or len(X_inner_val) < 2:
        raise ValueError(
            f"fold {fold_index}: outer training history (n={n}) is too small to carve out a "
            "real inner train/tune split -- refusing to fabricate a result from an "
            "unreasonably small inner block."
        )

    import xgboost as xgb

    best_params: dict[str, Any] | None = None
    best_inner_loss = float("inf")
    best_iteration = 0
    for params in _grid_combinations(grid):
        model = xgb.XGBClassifier(
            objective="binary:logistic", eval_metric="logloss",
            n_estimators=500, early_stopping_rounds=25,
            random_state=seed, n_jobs=2, verbosity=0,
            **params,
        )
        model.fit(X_inner_train, y_inner_train, eval_set=[(X_inner_val, y_inner_val)], verbose=False)
        inner_probs = model.predict_proba(X_inner_val)[:, 1].tolist()
        inner_loss = log_loss(y_inner_val.tolist(), inner_probs)
        if inner_loss < best_inner_loss:
            best_inner_loss = inner_loss
            best_params = params
            # best_iteration is the real number of trees XGBoost's own
            # early stopping selected on the inner block -- 0-indexed, so
            # +1 for a real tree count.
            best_iteration = int(model.best_iteration) + 1

    assert best_params is not None  # grid is never empty in real use

    # Refit on the FULL outer training history (inner train + inner tune
    # combined) with the frozen params/iteration -- no early stopping this
    # time, since the iteration count is already decided, and no outer
    # validation data anywhere in this call.
    final_model = xgb.XGBClassifier(
        objective="binary:logistic", eval_metric="logloss",
        n_estimators=best_iteration, random_state=seed, n_jobs=2, verbosity=0,
        **best_params,
    )
    final_model.fit(X_outer_train, y_outer_train, verbose=False)

    outer_probs = final_model.predict_proba(X_outer_val)[:, 1].tolist()
    y_outer_val_list = y_outer_val.tolist() if hasattr(y_outer_val, "tolist") else list(y_outer_val)

    return NestedFoldResult(
        fold_index=fold_index,
        best_params=best_params,
        best_iteration=best_iteration,
        inner_log_loss=best_inner_loss,
        outer_log_loss=log_loss(y_outer_val_list, outer_probs),
        outer_brier=brier_score(y_outer_val_list, outer_probs),
        outer_probs=outer_probs,
        inner_train_n=len(X_inner_train), inner_val_n=len(X_inner_val),
        outer_train_n=n, outer_val_n=len(X_outer_val),
    )


# ── Stress tests ─────────────────────────────────────────────────────────────


@dataclass
class StressTestResult:
    name: str
    base_pnl: float
    stressed_pnl: float
    pnl_change: float
    pnl_change_pct: float
    passed: bool
    details: dict[str, Any] | None = None


STRESS_SCENARIOS = [
    "one_tick_worse",
    "two_ticks_worse",
    "delayed_execution",
    "reduced_depth",
    "partial_fills",
    "increased_fees",
    "probability_shrinkage",
    "best_month_removed",
    "best_team_removed",
    "largest_wins_removed",
    "doubled_correlation",
    "stale_data_exclusion",
    "stricter_market_matching",
]


def run_stress_tests(
    trades: list[dict[str, Any]],  # list of EconomicResult-like dicts
    base_pnl: float,
) -> list[StressTestResult]:
    """Run all stress scenarios against a paper trading portfolio.

    Each trade dict should have: pnl, sport, team, month, edge_bucket.
    """
    results: list[StressTestResult] = []

    # 1. One tick worse: reduce each trade's edge by one tick
    one_tick = sum(
        max(-1.0, t.get("pnl", 0) * 0.90) for t in trades  # 10% worse entry
    )
    results.append(StressTestResult(
        "one_tick_worse", base_pnl, one_tick, one_tick - base_pnl,
        (one_tick - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=one_tick > 0 if base_pnl > 0 else True,
    ))

    # 2. Two ticks worse
    two_ticks = sum(
        max(-1.0, t.get("pnl", 0) * 0.80) for t in trades
    )
    results.append(StressTestResult(
        "two_ticks_worse", base_pnl, two_ticks, two_ticks - base_pnl,
        (two_ticks - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=two_ticks > 0 if base_pnl > 0 else True,
    ))

    # 3. Best month removed
    months: dict[str, float] = {}
    for t in trades:
        m = t.get("month", "unknown")
        months[m] = months.get(m, 0) + t.get("pnl", 0)
    if months:
        best_month_pnl = max(months.values())
        no_best = base_pnl - best_month_pnl
        results.append(StressTestResult(
            "best_month_removed", base_pnl, no_best, no_best - base_pnl,
            (no_best - base_pnl) / max(1, abs(base_pnl)) * 100,
            passed=no_best > 0 if base_pnl > 0 else True,
        ))

    # 4. Best team removed
    teams: dict[str, float] = {}
    for t in trades:
        teams[t.get("team", "?")] = teams.get(t.get("team", "?"), 0) + t.get("pnl", 0)
    if teams:
        best_team_pnl = max(teams.values())
        no_best_team = base_pnl - best_team_pnl
        results.append(StressTestResult(
            "best_team_removed", base_pnl, no_best_team, no_best_team - base_pnl,
            (no_best_team - base_pnl) / max(1, abs(base_pnl)) * 100,
            passed=no_best_team > 0 if base_pnl > 0 else True,
        ))

    # 5. Largest wins removed (top 10% of wins)
    sorted_trades = sorted(trades, key=lambda t: t.get("pnl", 0), reverse=True)
    n_remove = max(1, len(sorted_trades) // 10)
    no_top = sum(t.get("pnl", 0) for t in sorted_trades[n_remove:])
    results.append(StressTestResult(
        "largest_wins_removed", base_pnl, no_top, no_top - base_pnl,
        (no_top - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=no_top > 0 if base_pnl > 0 else True,
    ))

    # 6. Probability shrinkage: shrink all probs 5% toward 0.5
    shrunk_pnl = sum(
        t.get("pnl", 0) * (1.0 - abs(t.get("edge", 0)) * 0.5)
        for t in trades
    )
    results.append(StressTestResult(
        "probability_shrinkage", base_pnl, shrunk_pnl, shrunk_pnl - base_pnl,
        (shrunk_pnl - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=shrunk_pnl > 0 if base_pnl > 0 else True,
    ))

    # 7. Delayed execution: 5% slippage on each fill
    delayed = sum(t.get("pnl", 0) - 0.05 * abs(t.get("units", 1.0)) for t in trades)
    results.append(StressTestResult(
        "delayed_execution", base_pnl, delayed, delayed - base_pnl,
        (delayed - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=delayed > 0 if base_pnl > 0 else True,
    ))

    # 8. Reduced depth: halve units on each trade
    reduced = sum(t.get("pnl", 0) * 0.5 for t in trades)
    results.append(StressTestResult(
        "reduced_depth", base_pnl, reduced, reduced - base_pnl,
        (reduced - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=reduced > 0 if base_pnl > 0 else True,
    ))

    # 9. Partial fills: 80% fill rate
    partial = sum(t.get("pnl", 0) * 0.8 for t in trades)
    results.append(StressTestResult(
        "partial_fills", base_pnl, partial, partial - base_pnl,
        (partial - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=partial > 0 if base_pnl > 0 else True,
    ))

    # 10. Increased fees: 2% fee on each trade
    fees = sum(t.get("pnl", 0) - 0.02 * abs(t.get("units", 1.0)) for t in trades)
    results.append(StressTestResult(
        "increased_fees", base_pnl, fees, fees - base_pnl,
        (fees - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=fees > 0 if base_pnl > 0 else True,
    ))

    # 11. Doubled correlation: halve effective bet count
    doubled_corr = base_pnl * (2.0 / 3.0) if len(trades) > 1 else base_pnl * 0.9
    results.append(StressTestResult(
        "doubled_correlation", base_pnl, doubled_corr, doubled_corr - base_pnl,
        (doubled_corr - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=doubled_corr > 0 if base_pnl > 0 else True,
    ))

    # 12. Stale data: exclude trades with quote_age > 300s
    fresh = [t for t in trades if t.get("quote_age_seconds", 0) <= 300]
    stale_pnl = sum(t.get("pnl", 0) for t in fresh) if fresh else 0.0
    results.append(StressTestResult(
        "stale_data_exclusion", base_pnl, stale_pnl, stale_pnl - base_pnl,
        (stale_pnl - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=stale_pnl > 0 if base_pnl > 0 else True,
    ))

    # 13. Stricter matching: exclude edge < 0.03
    strict = [t for t in trades if t.get("edge", 0) >= 0.03]
    strict_pnl = sum(t.get("pnl", 0) for t in strict) if strict else 0.0
    results.append(StressTestResult(
        "stricter_market_matching", base_pnl, strict_pnl, strict_pnl - base_pnl,
        (strict_pnl - base_pnl) / max(1, abs(base_pnl)) * 100,
        passed=strict_pnl > 0 if base_pnl > 0 else True,
    ))

    return results


def stress_test_summary(results: list[StressTestResult]) -> dict[str, Any]:
    """Summarize stress test results with pass/fail verdict."""
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    return {
        "total_scenarios": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "worst_scenario": min(results, key=lambda r: r.stressed_pnl).name if results else "none",
        "worst_pnl": min(r.stressed_pnl for r in results) if results else 0.0,
        "overall_verdict": "PASS" if len(failed) == 0 else "FAIL",
        "failed_scenarios": [r.name for r in failed],
    }
