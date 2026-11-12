"""Conservative probability and threshold selection (Part 3-F/G).

Conservative probability: validated lower-bound estimate incorporating model bootstrap
uncertainty, calibration uncertainty, player/lineup uncertainty, data quality,
and model disagreement. A trade proceeds in paper simulation only when the
conservative probability clears executable ask + fees + slippage + safety margin.

Threshold selection: select trade thresholds on economic validation set only.
Possible dimensions: lower-bound edge, expected value, residual score, quote age,
liquidity, model uncertainty, missingness, horizon.
Apply frozen threshold once to untouched economic test. Record every trial.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConservativeProbability:
    """Lower-bound probability estimate with uncertainty sources enumerated."""

    raw_probability: float
    conservative_probability: float
    uncertainty_components: dict[str, float] = field(default_factory=dict)
    # Component breakdown
    model_bootstrap_uncertainty: float = 0.0
    calibration_uncertainty: float = 0.0
    player_lineup_uncertainty: float = 0.0
    data_quality_uncertainty: float = 0.0
    model_disagreement: float = 0.0
    # Derived
    lower_bound: float = 0.0
    safety_margin: float = 0.02  # additional safety margin beyond uncertainty

    def __post_init__(self) -> None:
        if self.lower_bound == 0.0:
            total_uncertainty = (
                self.model_bootstrap_uncertainty
                + self.calibration_uncertainty
                + self.player_lineup_uncertainty
                + self.data_quality_uncertainty
                + self.model_disagreement
            )
            self.lower_bound = max(0.01, self.raw_probability - total_uncertainty - self.safety_margin)
            self.conservative_probability = self.lower_bound


# ── Threshold selection ──────────────────────────────────────────────────────


@dataclass
class TradeThreshold:
    """A frozen threshold for trade eligibility on the economic validation set."""

    min_cost_adjusted_edge: float = 0.03
    min_expected_value: float = 0.0
    min_residual_score: float = 0.5
    max_quote_age_seconds: float = 300.0
    min_depth_units: float = 1.0
    max_model_uncertainty: float = 0.15
    max_missingness_frac: float = 0.3  # max fraction of features missing
    horizon: str = "mid"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_cost_adjusted_edge": self.min_cost_adjusted_edge,
            "min_expected_value": self.min_expected_value,
            "min_residual_score": self.min_residual_score,
            "max_quote_age_seconds": self.max_quote_age_seconds,
            "min_depth_units": self.min_depth_units,
            "max_model_uncertainty": self.max_model_uncertainty,
            "max_missingness_frac": self.max_missingness_frac,
            "horizon": self.horizon,
        }

    def filter(
        self,
        cost_adjusted_edge: float,
        expected_value: float = 0.0,
        residual_score: float = 1.0,
        quote_age_seconds: float = 0.0,
        depth_units: float = 100.0,
        model_uncertainty: float = 0.0,
        missingness_frac: float = 0.0,
        horizon: str = "mid",
    ) -> tuple[bool, str]:
        """Apply the frozen threshold to a candidate trade. Returns (pass, reason)."""
        if cost_adjusted_edge < self.min_cost_adjusted_edge:
            return False, f"edge {cost_adjusted_edge:.4f} < {self.min_cost_adjusted_edge}"
        if expected_value < self.min_expected_value:
            return False, f"EV {expected_value:.4f} < {self.min_expected_value}"
        if residual_score < self.min_residual_score:
            return False, f"residual {residual_score:.3f} < {self.min_residual_score}"
        if quote_age_seconds > self.max_quote_age_seconds:
            return False, f"quote age {quote_age_seconds:.0f}s > {self.max_quote_age_seconds}"
        if depth_units < self.min_depth_units:
            return False, f"depth {depth_units:.1f} < {self.min_depth_units}"
        if model_uncertainty > self.max_model_uncertainty:
            return False, f"uncertainty {model_uncertainty:.3f} > {self.max_model_uncertainty}"
        if missingness_frac > self.max_missingness_frac:
            return False, f"missingness {missingness_frac:.2f} > {self.max_missingness_frac}"
        if horizon != self.horizon and self.horizon != "any":
            return False, f"horizon {horizon} != {self.horizon}"
        return True, "ok"
