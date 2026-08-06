"""Winner-first value-betting decision engine (CLAUDE.md's non-negotiable
betting strategy, Checkpoint 7).

The system is not an unrestricted "bet whichever side has the largest
apparent edge" engine. It answers three questions independently:

1. What is the sports-only probability? (SportsForecast — market-blind)
2. Is a specific, frozen-before-market side mispriced? (MarketEvaluation)
3. Is the disagreement large enough after costs? (edge_scaled_units)

Moneyline/spread: the candidate side must equal `predicted_winner`, decided
before any market price is inspected. An opponent priced attractively is
never a substitute recommendation — it is simply never evaluated at all.

Totals: independent of game-winner alignment. The more probable side (OVER
or UNDER) is frozen from the sports-only distribution for that exact line
before market inspection, then only that side may be bet.

`NO_BET` always carries exactly 0.0 units — there is no minimum-stake floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .economic import SizeLimits, edge_scaled_units


@dataclass(frozen=True)
class SportsForecast:
    """Market-blind sports-only prediction for one event. Constructed once,
    from the model alone — nothing in the market layer may rewrite any field
    on this object."""
    event_id: str
    predicted_winner: Literal["home", "away"]
    raw_probabilities: dict[str, float]          # {"home": .., "away": ..}
    calibrated_probabilities: dict[str, float]
    probability_lower: dict[str, float]           # conservative/lower-bound
    probability_upper: dict[str, float]
    expected_home_score: float
    expected_away_score: float
    model_artifact_hash: str
    calibration_artifact_hash: str
    # {line: {"over": prob, "under": prob}} — the sports-only distribution's
    # own view per exact line, independent of any specific market's price.
    totals_probabilities: dict[float, dict[str, float]] = field(default_factory=dict)

    def frozen_totals_side(self, line: float) -> Literal["over", "under"] | None:
        """The more probable side for this exact line, decided from the
        sports-only distribution alone — call this before ever looking at a
        market price for that line."""
        probs = self.totals_probabilities.get(line)
        if probs is None:
            return None
        return "over" if probs.get("over", 0.0) >= probs.get("under", 0.0) else "under"


@dataclass(frozen=True)
class MarketEvaluation:
    """Real, executable market evidence for one specific contract."""
    market_id: str
    market_type: Literal["moneyline", "spread", "total"]
    team_or_side: str                    # "home"/"away" for moneyline/spread, "over"/"under" for total
    line: float | None
    executable_ask: float
    depth_adjusted_price: float
    quote_age_seconds: float
    available_depth: float


@dataclass(frozen=True)
class BetDecision:
    event_id: str
    action: Literal["BET", "NO_BET"]
    predicted_winner: str
    market_type: str
    selected_market: MarketEvaluation | None
    units: float
    reason_code: str
    cost_adjusted_edge: float | None = None


# Reason codes are deliberately explicit and stable — a dashboard/audit
# consumer should never need to parse a free-text message to know why a
# game produced NO_BET.
NOT_ALIGNED_WITH_PREDICTED_WINNER = "not_aligned_with_predicted_winner"
NOT_ALIGNED_WITH_FROZEN_TOTALS_SIDE = "not_aligned_with_frozen_totals_side"
NO_FORECAST_FOR_LINE = "no_forecast_for_line"
NO_EDGE_AFTER_COSTS = "no_edge_after_costs"
STALE_QUOTE = "stale_quote"
INSUFFICIENT_DEPTH = "insufficient_depth"
ZERO_SIZED = "zero_sized"
QUALIFIED = "qualified"


def _quote_gate_reason(candidate: MarketEvaluation, limits: SizeLimits) -> str | None:
    """Freshness/depth are checked before alignment is even evaluated further
    into edge math — a stale or illiquid quote must fail closed regardless of
    how attractive its price looks."""
    if candidate.quote_age_seconds > limits.max_quote_age_seconds:
        return STALE_QUOTE
    if candidate.available_depth < limits.min_depth_units:
        return INSUFFICIENT_DEPTH
    return None


def _no_bet(
    forecast: SportsForecast, market_type: str, reason: str, edge: float | None = None,
) -> BetDecision:
    return BetDecision(
        event_id=forecast.event_id, action="NO_BET", predicted_winner=forecast.predicted_winner,
        market_type=market_type, selected_market=None, units=0.0, reason_code=reason,
        cost_adjusted_edge=edge,
    )


def decide_team_market(
    forecast: SportsForecast,
    candidate: MarketEvaluation,
    limits: SizeLimits | None = None,
    fee_rate: float = 0.0,
    safety_margin: float = 0.0,
) -> BetDecision:
    """Moneyline or spread decision. `candidate.team_or_side` must equal
    `forecast.predicted_winner` — market price never influences which side
    that is, and an opponent priced attractively is never evaluated as an
    alternative recommendation, no matter how large its apparent edge."""
    limits = limits if limits is not None else SizeLimits()
    if candidate.market_type not in ("moneyline", "spread"):
        raise ValueError(f"decide_team_market got market_type={candidate.market_type!r}")
    if candidate.team_or_side != forecast.predicted_winner:
        return _no_bet(forecast, candidate.market_type, NOT_ALIGNED_WITH_PREDICTED_WINNER)

    gate_reason = _quote_gate_reason(candidate, limits)
    if gate_reason is not None:
        return _no_bet(forecast, candidate.market_type, gate_reason)

    conservative_prob = forecast.probability_lower[forecast.predicted_winner]
    cost_adjusted_edge = conservative_prob - candidate.depth_adjusted_price - fee_rate - safety_margin
    if cost_adjusted_edge <= 0:
        return _no_bet(forecast, candidate.market_type, NO_EDGE_AFTER_COSTS, cost_adjusted_edge)

    sizing = edge_scaled_units(
        model_prob=forecast.calibrated_probabilities[forecast.predicted_winner],
        conservative_prob=conservative_prob,
        best_ask=candidate.executable_ask,
        limits=limits,
    )
    units = sizing.get("units", 0.0)
    if units <= 0:
        return _no_bet(forecast, candidate.market_type, sizing.get("reason", ZERO_SIZED), cost_adjusted_edge)

    return BetDecision(
        event_id=forecast.event_id, action="BET", predicted_winner=forecast.predicted_winner,
        market_type=candidate.market_type, selected_market=candidate, units=units,
        reason_code=QUALIFIED, cost_adjusted_edge=cost_adjusted_edge,
    )


def decide_total(
    forecast: SportsForecast,
    candidate: MarketEvaluation,
    limits: SizeLimits | None = None,
    fee_rate: float = 0.0,
    safety_margin: float = 0.0,
) -> BetDecision:
    """Totals decision. The frozen side is whichever of OVER/UNDER the
    sports-only distribution favors for this exact line — decided by
    `forecast.frozen_totals_side()`, before `candidate`'s market price is
    inspected here."""
    limits = limits if limits is not None else SizeLimits()
    if candidate.market_type != "total":
        raise ValueError(f"decide_total got market_type={candidate.market_type!r}")
    if candidate.line is None:
        return _no_bet(forecast, "total", NO_FORECAST_FOR_LINE)

    frozen_side = forecast.frozen_totals_side(candidate.line)
    if frozen_side is None:
        return _no_bet(forecast, "total", NO_FORECAST_FOR_LINE)
    if candidate.team_or_side != frozen_side:
        return _no_bet(forecast, "total", NOT_ALIGNED_WITH_FROZEN_TOTALS_SIDE)

    gate_reason = _quote_gate_reason(candidate, limits)
    if gate_reason is not None:
        return _no_bet(forecast, "total", gate_reason)

    conservative_prob = forecast.totals_probabilities[candidate.line][frozen_side]
    cost_adjusted_edge = conservative_prob - candidate.depth_adjusted_price - fee_rate - safety_margin
    if cost_adjusted_edge <= 0:
        return _no_bet(forecast, "total", NO_EDGE_AFTER_COSTS, cost_adjusted_edge)

    sizing = edge_scaled_units(
        model_prob=conservative_prob, conservative_prob=conservative_prob,
        best_ask=candidate.executable_ask, limits=limits,
    )
    units = sizing.get("units", 0.0)
    if units <= 0:
        return _no_bet(forecast, "total", sizing.get("reason", ZERO_SIZED), cost_adjusted_edge)

    return BetDecision(
        event_id=forecast.event_id, action="BET", predicted_winner=forecast.predicted_winner,
        market_type="total", selected_market=candidate, units=units,
        reason_code=QUALIFIED, cost_adjusted_edge=cost_adjusted_edge,
    )


def evaluate_game(
    forecast: SportsForecast,
    candidates: list[MarketEvaluation],
    limits: SizeLimits | None = None,
) -> list[BetDecision]:
    """Evaluate every candidate market for one game. Every candidate produces
    a decision — including NO_BET — so a caller can persist a full audit
    trail, not just the accepted bets (CLAUDE.md: "every event must persist
    a decision, including no-bets")."""
    limits = limits if limits is not None else SizeLimits()
    decisions = []
    for c in candidates:
        if c.market_type in ("moneyline", "spread"):
            decisions.append(decide_team_market(forecast, c, limits))
        elif c.market_type == "total":
            decisions.append(decide_total(forecast, c, limits))
    return decisions
