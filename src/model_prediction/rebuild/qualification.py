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
    coverage: float  # fraction of events with valid predictions
    n_events: int
    n_calls: int
    n_unique_dates: int
    bootstrap_log_loss_ci: tuple[float, float] = (0.0, 0.0)
    seasonal_stability: float = 1.0  # max log-loss difference across seasons
    pit_violations: int = 0
    train_serve_parity: float = 1.0  # ratio of serve log-loss to train log-loss
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified,
            "log_loss": self.log_loss,
            "brier": self.brier,
            "ece": self.ece,
            "calibration_slope": self.calibration_slope,
            "n_events": self.n_events,
            "n_calls": self.n_calls,
            "pit_violations": self.pit_violations,
            "failures": self.failures,
        }


@dataclass
class EconomicQualification:
    """Evidence for executable economic value."""

    qualified: bool
    cost_adjusted_return: float  # return after spread, fees, slippage
    clv_mean: float  # mean closing line value
    clv_positive_frac: float  # fraction with positive CLV
    n_trades: int
    n_unique_dates: int
    bootstrap_return_ci: tuple[float, float] = (0.0, 0.0)
    bootstrap_clv_ci: tuple[float, float] = (0.0, 0.0)
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    concentration_ratio: float = 0.0  # fraction of PnL from top team
    depth_sufficient: bool = True
    contract_match_success: float = 1.0
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified,
            "cost_adjusted_return": self.cost_adjusted_return,
            "clv_mean": self.clv_mean,
            "n_trades": self.n_trades,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "sport": self.sport,
            "market_type": self.market_type,
            "status": self.status,
            "predictive": self.predictive.to_dict() if self.predictive else None,
            "economic": self.economic.to_dict() if self.economic else None,
        }
