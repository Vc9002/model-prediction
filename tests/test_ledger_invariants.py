"""Permanent architectural invariant tests for Flat vs Gated ledgers.

GOVERNING RULES:
1. FLAT LEDGERS (Flat Forecast & Flat Research):
   - Must ALWAYS evaluate and log EVERY candidate game and market.
   - Must NEVER apply edge gates, spread price caps, or minimum edge hurdles.
   - They are pure, comprehensive model observation ledgers.

2. GATED LEDGERS (Production Ledger, Gated Research, Polymarket Edge Ledger):
   - Must enforce edge gates (+2.0% / +2.5% / +3.5% for spreads).
   - Must enforce spread price caps (>60c cap).
   - Must size positions via real market-relative Quarter-Kelly.
"""

from __future__ import annotations

from model_prediction.portfolio.polymarket_kelly import PolymarketKellyEngine, PolymarketQuote
from model_prediction.units import UnitPolicy, edge_scaled_units


def test_flat_ledgers_concept_invariant():
    """Flat ledgers MUST record zero-edge or negative-edge games as valid observation rows."""
    policy = UnitPolicy(min_pick_units=1.0, max_pick_units=2.0)
    units = edge_scaled_units(
        model_probability=0.50, model_uncertainty=0.0, american_odds=-110, policy=policy
    )
    assert units == 1.0, "Flat observation must remain sized at baseline 1.0U without gating"


def test_gated_spread_requires_minimum_three_point_five_percent_edge():
    """Gated engines MUST enforce the +3.5pp minimum edge hurdle for spread markets."""
    engine = PolymarketKellyEngine(bankroll=1000.0, min_edge=0.025)

    # 1. Moneyline with +2.8% edge passes the standard 2.5% gate
    ml_quote = PolymarketQuote(
        market_id="ml_1",
        question="Will New York Yankees win?",
        best_bid=0.48,
        best_ask=0.50,
        spread=0.02,
        home_or_player_a="New York Yankees",
        away_or_player_b="Boston Red Sox",
        event_start_utc="2026-08-22T19:00:00Z",
        observed_at_utc="2026-08-22T18:00:00Z",
    )
    ml_decision = engine.evaluate_binary_opportunity(ml_quote, p_model=0.528)
    assert ml_decision.side == "BUY_YES", "Moneyline with +2.8% edge should qualify"

    # 2. Spread with +2.8% edge FAILS because spread hurdle is +3.5pp
    spread_quote = PolymarketQuote(
        market_id="spread_1",
        question="Will New York Yankees -1.5 Run Line win?",
        best_bid=0.48,
        best_ask=0.50,
        spread=0.02,
        home_or_player_a="New York Yankees -1.5",
        away_or_player_b="Boston Red Sox +1.5",
        event_start_utc="2026-08-22T19:00:00Z",
        observed_at_utc="2026-08-22T18:00:00Z",
    )
    spread_decision = engine.evaluate_binary_opportunity(spread_quote, p_model=0.528)
    assert spread_decision.side == "NO_ORDER", "Spread with +2.8% edge must fail the +3.5pp spread hurdle"

    # 3. Spread with +4.0% edge PASSES the +3.5pp spread hurdle
    spread_pass_decision = engine.evaluate_binary_opportunity(spread_quote, p_model=0.540)
    assert spread_pass_decision.side == "BUY_YES", "Spread with +4.0% edge must qualify"


def test_gated_spread_enforces_sixty_cent_price_ceiling():
    """Gated engines MUST reject buying expensive spread/runline contracts > 60c."""
    engine = PolymarketKellyEngine(bankroll=1000.0, min_edge=0.025)

    expensive_spread_quote = PolymarketQuote(
        market_id="spread_2",
        question="Will Boston Red Sox +1.5 Run Line win?",
        best_bid=0.62,
        best_ask=0.64,
        spread=0.02,
        home_or_player_a="Boston Red Sox +1.5",
        away_or_player_b="New York Yankees -1.5",
        event_start_utc="2026-08-22T19:00:00Z",
        observed_at_utc="2026-08-22T18:00:00Z",
    )
    decision = engine.evaluate_binary_opportunity(expensive_spread_quote, p_model=0.70)
    assert decision.side == "NO_ORDER", "Spread priced > 60c must be blocked"
    assert "exceeds 60¢ cap" in decision.reason


def test_real_edge_quarter_kelly_prevents_favorite_over_staking():
    """Heavy favorites with thin edges MUST NOT receive maximum stakes."""
    policy = UnitPolicy(reference_units=1.0, min_pick_units=1.0, max_pick_units=2.0)

    # Favorite at 65% model vs 64% market (-178 odds) -> thin +1% edge
    favorite_units = edge_scaled_units(
        model_probability=0.65, model_uncertainty=0.0, american_odds=-178, policy=policy
    )
    # Underdog with massive +12% edge (45% model vs 33% market, +200 odds)
    underdog_units = edge_scaled_units(
        model_probability=0.45, model_uncertainty=0.0, american_odds=+200, policy=policy
    )

    assert favorite_units < underdog_units, (
        "Huge edge underdog must receive higher sizing than thin edge favorite"
    )
    assert favorite_units <= 1.25, "Thin edge favorite must not exceed 1.25U"
    assert underdog_units >= 1.75, "High edge pick must scale up toward 2.0U"
