"""Unit tests for Polymarket US Binary Edge Gate & Kelly Sizing Engine."""

from __future__ import annotations

import pytest

from model_prediction.portfolio.polymarket_kelly import (
    PolymarketKellyEngine,
    PolymarketQuote,
)


def test_qualifying_taker_yes_opportunity():
    engine = PolymarketKellyEngine(
        bankroll=1000.0, min_edge=0.025, kelly_fraction=0.25, max_position_pct=0.03
    )

    # Market ask is 0.55, model says 0.62 -> edge = +0.07 (7.0%)
    quote = PolymarketQuote(
        market_id="m_1",
        question="Will NYY win?",
        best_bid=0.53,
        best_ask=0.55,
        spread=0.02,
        home_or_player_a="New York Yankees",
        away_or_player_b="Boston Red Sox",
    )

    decision = engine.evaluate_binary_opportunity(quote, p_model=0.62)

    assert decision.side == "BUY_YES"
    assert decision.target_selection == "New York Yankees"
    assert decision.target_side == "YES"
    assert decision.home_team == "New York Yankees"
    assert decision.away_team == "Boston Red Sox"
    assert decision.selection_label == "New York Yankees (BUY YES)"
    assert decision.order_price == 0.55
    assert decision.edge == pytest.approx(0.07, abs=1e-4)
    assert decision.expected_value_pct > 10.0  # EV = 0.07 / 0.55 = 12.7%
    # Full Kelly = (0.62 - 0.55) / (1 - 0.55) = 0.07 / 0.45 = 0.1555
    # Rec Kelly (0.25x) = 0.0388 -> capped at max_position_pct (0.03 = $30.00)
    assert decision.kelly_fraction_full == pytest.approx(0.1556, abs=1e-3)
    assert decision.kelly_fraction_recommended == pytest.approx(0.03, abs=1e-3)
    assert decision.stake_units == 30.00


def test_qualifying_taker_no_opportunity():
    engine = PolymarketKellyEngine(bankroll=1000.0, min_edge=0.025)

    # Market bid is 0.65, model says 0.50 -> Model believes away/NO is underpriced
    # Buying NO: Cost is 1.0 - 0.65 = 0.35. P_no is 0.50 -> edge = 0.15 (15.0%)
    quote = PolymarketQuote(
        market_id="m_2",
        question="Will Favorite win?",
        best_bid=0.65,
        best_ask=0.67,
        spread=0.02,
        home_or_player_a="Favorite Team",
        away_or_player_b="Underdog Team",
    )

    decision = engine.evaluate_binary_opportunity(quote, p_model=0.50)

    assert decision.side == "BUY_NO"
    assert decision.target_selection == "Underdog Team"
    assert decision.target_side == "NO"
    assert decision.selection_label == "Underdog Team (BUY NO)"
    assert decision.order_price == 0.35
    assert decision.edge == pytest.approx(0.15, abs=1e-4)
    assert decision.stake_units > 0.0


def test_rejection_below_min_edge():
    engine = PolymarketKellyEngine(bankroll=1000.0, min_edge=0.03)

    # Model says 0.56, ask is 0.55 -> edge is 0.01 (1.0%), below 3.0% gate
    quote = PolymarketQuote(
        market_id="m_3",
        question="Will Match Tie?",
        best_bid=0.53,
        best_ask=0.55,
        spread=0.02,
    )

    decision = engine.evaluate_binary_opportunity(quote, p_model=0.56)

    assert decision.side == "NO_ORDER"
    assert decision.stake_units == 0.0
    assert "below min threshold" in decision.reason


def test_maker_pricing_inside_spread():
    engine = PolymarketKellyEngine(bankroll=1000.0, min_edge=0.025)

    # Spread is wide: Bid 0.40, Ask 0.50. Model is 0.52.
    quote = PolymarketQuote(
        market_id="m_4",
        question="Will Underdog win?",
        best_bid=0.40,
        best_ask=0.50,
        spread=0.10,
    )

    # With prefer_maker=True, posts at bid + 0.01 = 0.41 (capturing extra edge)
    decision = engine.evaluate_binary_opportunity(quote, p_model=0.52, prefer_maker=True)

    assert decision.side == "BUY_YES"
    assert decision.is_maker is True
    assert decision.order_price == 0.41
    assert decision.edge == pytest.approx(0.11, abs=1e-4)


def test_polymarket_tie_half_payout_rule():
    engine = PolymarketKellyEngine(bankroll=1000.0, min_edge=0.02)

    # In KBO: P(Win) = 0.45, P(Tie) = 0.10 -> E[Payout] = 0.45 + 0.05 = 0.50
    # Ask is 0.46 -> Edge is 0.50 - 0.46 = 0.04 (4.0%)
    quote = PolymarketQuote(
        market_id="m_kbo",
        question="Will Twins win?",
        best_bid=0.44,
        best_ask=0.46,
        spread=0.02,
    )

    decision = engine.evaluate_binary_opportunity(quote, p_model=0.45, p_tie=0.10)

    assert decision.side == "BUY_YES"
    assert decision.model_probability == pytest.approx(0.50, abs=1e-4)
    assert decision.edge == pytest.approx(0.04, abs=1e-4)
