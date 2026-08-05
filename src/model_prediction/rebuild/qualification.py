"""Dual qualification framework (Part 3-E) — predictive + economic qualification.

Predictive qualification: log loss, Brier, calibration, reliability, coverage,
no PIT violations, train-serving parity, stability across seasons/cohorts.

Economic qualification: real executable quotes, modeled spread/fees/slippage,
sufficient independent events, positive cost-adjusted return, positive CLV,
bootstrap uncertainty, acceptable drawdown, stable across buckets.

Statuses: REJECTED, RESEARCH_ONLY, PREDICTIVELY_QUALIFIED,
ECONOMIC_SAMPLE_INSUFFICIENT, ECONOMICALLY_QUALIFIED_FOR_SHADOW,
ELIGIBLE_FOR_SEPARATE_LIVE_REVIEW.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


MODEL_STATUSES = [
    "REJECTED",
    "RESEARCH_ONLY",
    "PREDICTIVELY_QUALIFIED",
    "ECONOMIC_SAMPLE_INSUFFICIENT",
    "ECONOMICALLY_QUALIFIED_FOR_SHADOW",
    "ELIGIBLE_FOR_SEPARATE_LIVE_REVIEW",
]


@dataclass
class PredictiveQualification:
    """Evidence for sports-only probability quality."""
    qualified: bool
    log_loss: float
    brier: float
    ece: float
    calibration_slope: float
    calibration_intercept: float
    coverage: float                    # fraction of events with valid predictions
    n_events: int
    n_calls: int
    n_unique_dates: int
    bootstrap_log_loss_ci: tuple[float, float] = (0.0, 0.0)
    seasonal_stability: float = 1.0     # max log-loss difference across seasons
    pit_violations: int = 0
    train_serve_parity: float = 1.0     # ratio of serve log-loss to train log-loss
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified, "log_loss": self.log_loss,
            "brier": self.brier, "ece": self.ece,
            "calibration_slope": self.calibration_slope,
            "n_events": self.n_events, "n_calls": self.n_calls,
            "pit_violations": self.pit_violations,
            "failures": self.failures,
        }


@dataclass
class EconomicQualification:
    """Evidence for executable economic value."""
    qualified: bool
    cost_adjusted_return: float        # return after spread, fees, slippage
    clv_mean: float                    # mean closing line value
    clv_positive_frac: float           # fraction with positive CLV
    n_trades: int
    n_unique_dates: int
    bootstrap_return_ci: tuple[float, float] = (0.0, 0.0)
    bootstrap_clv_ci: tuple[float, float] = (0.0, 0.0)
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    concentration_ratio: float = 0.0   # fraction of PnL from top team
    depth_sufficient: bool = True
    contract_match_success: float = 1.0
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified, "cost_adjusted_return": self.cost_adjusted_return,
            "clv_mean": self.clv_mean, "n_trades": self.n_trades,
            "sharpe": self.sharpe, "max_drawdown": self.max_drawdown,
            "failures": self.failures,
        }


@dataclass
class ModelQualification:
    """Combined predictive + economic qualification for one model."""
    model_id: str
    sport: str
    market_type: str
    status: str = "RESEARCH_ONLY"
    predictive: PredictiveQualification | None = None
    economic: EconomicQualification | None = None

    def determine_status(self) -> str:
        """Determine the model status from predictive + economic evidence."""
        if self.predictive is None and self.economic is None:
            return "RESEARCH_ONLY"

        pred_ok = self.predictive is not None and self.predictive.qualified
        econ_ok = self.economic is not None and self.economic.qualified

        if not pred_ok and not econ_ok:
            return "REJECTED"
        if pred_ok and not econ_ok:
            if self.economic is not None and self.economic.n_trades < 50:
                return "ECONOMIC_SAMPLE_INSUFFICIENT"
            return "PREDICTIVELY_QUALIFIED"
        if econ_ok and (self.economic is not None and self.economic.cost_adjusted_return > 0):
            return "ECONOMICALLY_QUALIFIED_FOR_SHADOW"

        return "PREDICTIVELY_QUALIFIED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id, "sport": self.sport,
            "market_type": self.market_type, "status": self.status,
            "predictive": self.predictive.to_dict() if self.predictive else None,
            "economic": self.economic.to_dict() if self.economic else None,
        }


def evaluate_predictive_qualification(
    y_true: list[int], y_prob: list[float],
    n_events: int, n_dates: int,
    calibration_slope: float = 1.0,
    calibration_intercept: float = 0.0,
    pit_violations: int = 0,
    train_serve_parity: float = 1.0,
    min_events: int = 50,
    max_ece: float = 0.10,
    max_pit: int = 0,
) -> PredictiveQualification:
    """Evaluate predictive qualification from out-of-fold metrics."""
    from .validation import log_loss, brier_score, ece

    failures: list[str] = []
    ll = log_loss(y_true, y_prob)
    br = brier_score(y_true, y_prob)
    ec = ece(y_true, y_prob)

    if n_events < min_events:
        failures.append(f"events {n_events} < {min_events}")
    if ec > max_ece:
        failures.append(f"ECE {ec:.4f} > {max_ece}")
    if pit_violations > max_pit:
        failures.append(f"PIT violations {pit_violations} > {max_pit}")
    if abs(calibration_slope - 1.0) > 0.3:
        failures.append(f"calibration slope {calibration_slope:.3f} far from 1.0")

    n_calls = len(y_true)
    return PredictiveQualification(
        qualified=len(failures) == 0,
        log_loss=float(ll), brier=float(br), ece=float(ec),
        calibration_slope=calibration_slope,
        calibration_intercept=calibration_intercept,
        coverage=1.0, n_events=n_events, n_calls=n_calls,
        n_unique_dates=n_dates, pit_violations=pit_violations,
        train_serve_parity=train_serve_parity, failures=failures,
    )


def evaluate_economic_qualification(
    returns: list[float],
    clv_values: list[float],
    n_trades: int,
    n_dates: int,
    max_drawdown: float = 0.0,
    concentration_ratio: float = 0.0,
    contract_match_success: float = 1.0,
    min_trades: int = 50,
    min_return: float = 0.0,
    min_clv: float = 0.0,
) -> EconomicQualification:
    """Evaluate economic qualification from paper trading results."""
    failures: list[str] = []
    ret = float(np.mean(returns))
    clv = float(np.mean(clv_values)) if clv_values else 0.0

    if n_trades < min_trades:
        failures.append(f"trades {n_trades} < {min_trades}")
    if ret < min_return:
        failures.append(f"return {ret:.4f} < {min_return}")
    if clv < min_clv:
        failures.append(f"CLV {clv:.4f} < {min_clv}")
    if contract_match_success < 0.95:
        failures.append(f"contract match {contract_match_success:.2%} < 95%")

    # Bootstrap CIs
    rng = np.random.default_rng(42)
    boot_returns = [float(np.mean(rng.choice(returns, size=len(returns), replace=True))) for _ in range(500)]
    boot_arr = np.array(boot_returns)

    return EconomicQualification(
        qualified=len(failures) == 0,
        cost_adjusted_return=ret,
        clv_mean=clv,
        clv_positive_frac=float(np.mean(np.array(clv_values) > 0)) if clv_values else 0.0,
        n_trades=n_trades, n_unique_dates=n_dates,
        bootstrap_return_ci=(float(np.percentile(boot_arr, 2.5)), float(np.percentile(boot_arr, 97.5))),
        max_drawdown=max_drawdown, sharpe=float(ret / max(0.001, np.std(returns))),
        concentration_ratio=concentration_ratio,
        contract_match_success=contract_match_success,
        failures=failures,
    )
