"""High-confidence signal overlay.

A pick earns the HIGH_CONFIDENCE tag only when every condition below holds.
The tag exists for post-hoc evaluation; sizing stays half-Kelly regardless.

Conditions:
1. Model edge >= 8% versus the Polymarket executable ask
2. Trend momentum in the top 5th percentile (or bottom, for fades)
3. Polymarket bid-ask spread <= 5%
4. No key injuries or lineup changes in the last 24 hours (unknown = fail)
5. Data freshness < 6 hours
6. Team not banned
7. >= 30 historical observations for this market type
8. Survives all standard eligibility gates
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..domain import parse_utc, utc_now


HIGH_CONFIDENCE_TAG = "HIGH_CONFIDENCE"


@dataclass(frozen=True)
class SignalInputs:
    edge_vs_executable_ask: float
    momentum_percentile: float
    bid_ask_spread: float | None
    injury_news_clear_last_24h: bool | None  # None = unknown, which fails closed
    observed_at_utc: str | None
    team_banned: bool
    historical_observations_for_market: int
    passed_standard_gates: bool


def evaluate_high_confidence(inputs: SignalInputs, now: datetime | None = None) -> dict[str, Any]:
    current = now or utc_now()
    freshness_hours: float | None = None
    if inputs.observed_at_utc:
        freshness_hours = (current - parse_utc(inputs.observed_at_utc)).total_seconds() / 3600
    checks = {
        "edge_at_least_8pct": inputs.edge_vs_executable_ask >= 0.08,
        "momentum_top_5th_percentile": (
            inputs.momentum_percentile >= 0.95 or inputs.momentum_percentile <= 0.05
        ),
        "spread_at_most_5pct": inputs.bid_ask_spread is not None and inputs.bid_ask_spread <= 0.05,
        "no_recent_injury_news": inputs.injury_news_clear_last_24h is True,
        "data_fresh_under_6h": freshness_hours is not None and freshness_hours < 6,
        "team_not_banned": not inputs.team_banned,
        "at_least_30_observations": inputs.historical_observations_for_market >= 30,
        "passed_standard_gates": inputs.passed_standard_gates,
    }
    qualifies = all(checks.values())
    return {
        "tag": HIGH_CONFIDENCE_TAG if qualifies else None,
        "qualifies": qualifies,
        "checks": checks,
        "note": "Tag is for post-hoc evaluation only; sizing remains half-Kelly.",
    }
