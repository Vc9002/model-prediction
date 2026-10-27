"""NFL research layer: fit a market correction using prior complete dates only.

This is a price-aware probability layer, not an independent outcome model.
It needs a timestamp-valid quote for the exact event, market, side, and line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

MODEL_VERSION = "nfl-market-anchor-v1"


@dataclass(frozen=True)
class SettledQuote:
    event_date: date
    model_probability: float
    market_probability: float
    outcome: int
    settled_at_utc: datetime


def asof_market_weight(history: list[SettledQuote], forecast_date: date, forecast_at_utc: datetime) -> float:
    """Minimize prior-date Brier loss for model + w*(market-model)."""
    if forecast_at_utc.tzinfo is None or any(row.settled_at_utc.tzinfo is None for row in history):
        raise ValueError("naive_asof_timestamp")
    prior = [
        row for row in history if row.event_date < forecast_date and row.settled_at_utc <= forecast_at_utc
    ]
    if not prior:
        raise ValueError("no_prior_complete_date_evidence")
    if any(
        not 0 < row.model_probability < 1
        or not 0 < row.market_probability < 1
        or not math.isfinite(row.model_probability + row.market_probability)
        or row.outcome not in (0, 1)
        for row in prior
    ):
        raise ValueError("invalid_prior_quote")
    numerator = -sum(
        (row.model_probability - row.outcome) * (row.market_probability - row.model_probability)
        for row in prior
    )
    denominator = sum((row.market_probability - row.model_probability) ** 2 for row in prior)
    if denominator == 0:
        return 0.0
    return min(1.0, max(0.0, numerator / denominator))


def market_anchored_probability(model_probability: float, market_probability: float, weight: float) -> float:
    if not all(math.isfinite(value) for value in (model_probability, market_probability, weight)):
        raise ValueError("nonfinite_market_anchor_input")
    if not 0 < model_probability < 1 or not 0 < market_probability < 1 or not 0 <= weight <= 1:
        raise ValueError("invalid_market_anchor_input")
    return model_probability + weight * (market_probability - model_probability)
