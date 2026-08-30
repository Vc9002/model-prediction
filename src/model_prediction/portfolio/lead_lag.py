"""Sharp-Book Lead/Lag Signal and Execution Timing Engine.

Treats sharp market consensus (Pinnacle/Circa/Consensus BBO) as the reference
price, measuring Polymarket's quote latency to optimize execution urgency:
1. URGENT_TAKER: Sharp book has moved and Polymarket quote is stale (lag >= +2.0%).
2. PASSIVE_MAKER: Polymarket is in equilibrium with sharp consensus; post inside spread.
3. ADVERSE_SELECTION_WARN: Polymarket quote leads sharp book or signals adverse flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


@dataclass(frozen=True)
class LeadLagSignal:
    market_id: str
    target_selection: str
    sharp_reference_prob: float
    polymarket_prob: float
    lag_delta: float
    sharp_velocity_bps_per_min: float
    execution_urgency: Literal["URGENT_TAKER", "PASSIVE_MAKER", "ADVERSE_SELECTION_WARN"]
    recommended_action: str
    observed_at_utc: str


class SharpLeadLagAnalyzer:
    """Measures exchange pricing latency relative to sharp reference markets."""

    def __init__(
        self,
        min_lag_taker_threshold: float = 0.020,  # 2.0% latency edge
        velocity_momentum_threshold: float = 5.0,  # 5 bps / min movement
    ) -> None:
        self.min_lag_taker_threshold = min_lag_taker_threshold
        self.velocity_momentum_threshold = velocity_momentum_threshold

    def evaluate_latency(
        self,
        market_id: str,
        target_selection: str,
        sharp_reference_prob: float,
        polymarket_prob: float,
        prior_sharp_prob: float | None = None,
        minutes_elapsed: float = 15.0,
    ) -> LeadLagSignal:
        """Evaluate lead/lag latency and determine execution urgency."""
        sharp_p = max(0.01, min(0.99, float(sharp_reference_prob)))
        poly_p = max(0.01, min(0.99, float(polymarket_prob)))
        lag_delta = round(sharp_p - poly_p, 4)

        if prior_sharp_prob is not None and minutes_elapsed > 0:
            velocity_bps = ((sharp_p - prior_sharp_prob) * 10000.0) / minutes_elapsed
        else:
            velocity_bps = 0.0

        urgency: Literal["URGENT_TAKER", "PASSIVE_MAKER", "ADVERSE_SELECTION_WARN"]
        if lag_delta >= self.min_lag_taker_threshold:
            urgency = "URGENT_TAKER"
            action = f"Immediate IOC market order: Polymarket lags sharp consensus by {lag_delta * 100:.1f}%"
        elif lag_delta <= -self.min_lag_taker_threshold:
            urgency = "ADVERSE_SELECTION_WARN"
            action = f"Hold orders: Polymarket price exceeds sharp consensus by {abs(lag_delta) * 100:.1f}%"
        else:
            urgency = "PASSIVE_MAKER"
            action = "Post resting limit order inside spread: market in sync with sharp consensus"

        return LeadLagSignal(
            market_id=market_id,
            target_selection=target_selection,
            sharp_reference_prob=round(sharp_p, 4),
            polymarket_prob=round(poly_p, 4),
            lag_delta=lag_delta,
            sharp_velocity_bps_per_min=round(velocity_bps, 2),
            execution_urgency=urgency,
            recommended_action=action,
            observed_at_utc=datetime.now(UTC).isoformat(),
        )
