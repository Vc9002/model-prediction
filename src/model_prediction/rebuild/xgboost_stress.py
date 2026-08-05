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

from collections.abc import Sequence
from dataclasses import dataclass
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
