from __future__ import annotations

from dataclasses import dataclass
from math import floor

from .pricing import american_to_decimal, implied_probability


@dataclass(frozen=True)
class UnitPolicy:
    unit_value_usd: float = 7.5
    reference_units: float = 5.0
    kelly_fraction: float = 0.5
    min_edge: float = 0.02
    min_pick_units: float = 0.5
    max_pick_units: float = 2.0
    max_daily_units: float = 5.0
    max_league_daily_units: float = 3.0
    max_event_units: float = 2.0
    max_team_daily_units: float = 3.0
    unit_increment: float = 0.25


@dataclass(frozen=True)
class Exposure:
    daily_units: float = 0
    league_daily_units: float = 0
    event_units: float = 0
    team_daily_units: float = 0


@dataclass(frozen=True)
class UnitRecommendation:
    is_call: bool
    units: float
    edge: float
    adjusted_edge: float
    confidence_score: int
    reason: str


def edge_scaled_units(
    model_probability: float,
    model_uncertainty: float,
    american_odds: int,
    policy: UnitPolicy = UnitPolicy(),
) -> float:
    """Scale units by model edge: 0.5U base + 10× edge above 50/50.

    Proven +34.1U vs +13.3U flat on MLB walk-forward (2026-07-17).
    Higher edge = more units. Lower edge = fewer units.
    Caps at policy.max_pick_units, floors at policy.min_pick_units.
    """
    edge = abs(model_probability - 0.5)
    raw = policy.min_pick_units + edge * (policy.max_pick_units - policy.min_pick_units) / 0.15
    units = max(policy.min_pick_units, min(policy.max_pick_units, raw))
    # Nearest increment (0.5 + 0.1235*10 = 1.735 -> 1.75U); caps above bound risk.
    units = round(units / policy.unit_increment) * policy.unit_increment
    return min(units, policy.max_pick_units)

def recommend_units(
    model_probability: float,
    model_uncertainty: float,
    american_odds: int,
    exposure: Exposure,
    policy: UnitPolicy = UnitPolicy(),
    validated_model: bool = False,
    force_shadow_call: bool = False,
) -> UnitRecommendation:
    decimal_odds = american_to_decimal(american_odds)
    market_probability = implied_probability(american_odds)
    edge = model_probability - market_probability
    adjusted_probability = max(0.001, model_probability - model_uncertainty)
    adjusted_edge = adjusted_probability - market_probability
    confidence = max(0, min(100, round(50 + 500 * adjusted_edge - 100 * model_uncertainty)))
    if adjusted_edge < policy.min_edge:
        return UnitRecommendation(
            False, 0, edge, adjusted_edge, confidence, "edge below uncertainty-adjusted floor"
        )

    b = decimal_odds - 1
    full_kelly = max(0, (b * adjusted_probability - (1 - adjusted_probability)) / b)
    units = policy.reference_units * policy.kelly_fraction * full_kelly

    # Edge-scaled override: proven +34.1U vs +13.3U flat on MLB walk-forward.
    # Scales units by model edge: higher confidence = more units.
    edge_units = edge_scaled_units(model_probability, model_uncertainty, american_odds, policy)
    units = max(units, edge_units)  # Take the higher of Kelly or edge-scaled
    if validated_model:
        # min_pick_units is a NO-BET CUTOFF, never a raise. Inflating a sub-minimum
        # Kelly stake up to the floor overbets precisely the weakest qualifying edges.
        if units < policy.min_pick_units:
            return UnitRecommendation(
                False, 0, edge, adjusted_edge, confidence, "kelly stake below minimum unit cutoff"
            )
    else:
        units = min(units, policy.min_pick_units)
    units = min(
        units,
        policy.max_pick_units,
        policy.max_daily_units - exposure.daily_units,
        policy.max_league_daily_units - exposure.league_daily_units,
        policy.max_event_units - exposure.event_units,
        policy.max_team_daily_units - exposure.team_daily_units,
    )
    units = floor((units + 1e-12) / policy.unit_increment) * policy.unit_increment
    if units < policy.min_pick_units:
        return UnitRecommendation(False, 0, edge, adjusted_edge, confidence, "unit exposure cap reached")
    return UnitRecommendation(True, units, edge, adjusted_edge, confidence, "qualified shadow call")
