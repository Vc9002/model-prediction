"""Unit tests for Polymarket US Unified Multi-Sport Dispatcher."""

from __future__ import annotations

import pytest

from model_prediction.portfolio.polymarket_dispatcher import (
    DispatchRequest,
    PolymarketDispatcher,
)


def test_dispatcher_single_order_evaluation():
    dispatcher = PolymarketDispatcher(bankroll=2000.0, min_edge=0.025, max_position_pct=0.03)

    req = DispatchRequest(
        market_id="m_mlb_01",
        league="MLB",
        question="Will Dodgers beat Padres?",
        home_or_player_a="Dodgers",
        away_or_player_b="Padres",
        best_bid=0.58,
        best_ask=0.60,
        event_start_utc="2026-08-22T00:00:00Z",
        p_model_override=0.67,  # +7.0% edge over ask 0.60
    )

    decision = dispatcher.evaluate_request(req)

    assert decision.side == "BUY_YES"
    assert decision.order_price == 0.60
    assert decision.edge == pytest.approx(0.07, abs=1e-4)
    assert decision.stake_units == 60.00  # 3% of $2000


def test_dispatcher_batch_filtering():
    dispatcher = PolymarketDispatcher(bankroll=1000.0, min_edge=0.03)

    req_pass = DispatchRequest(
        market_id="m_pass",
        league="WNBA",
        question="Will Lynx win?",
        home_or_player_a="Lynx",
        away_or_player_b="Storm",
        best_bid=0.68,
        best_ask=0.70,
        event_start_utc="2026-08-22T00:00:00Z",
        p_model_override=0.75,  # +5% edge >= 3%
    )
    req_fail = DispatchRequest(
        market_id="m_fail",
        league="TENNIS",
        question="Will Alcaraz win?",
        home_or_player_a="Alcaraz",
        away_or_player_b="Sinner",
        best_bid=0.52,
        best_ask=0.54,
        event_start_utc="2026-08-22T00:00:00Z",
        p_model_override=0.55,  # +1% edge < 3%
    )

    actionable = dispatcher.get_actionable_orders([req_pass, req_fail])

    assert len(actionable) == 1
    assert actionable[0].market_id == "m_pass"
    assert actionable[0].side == "BUY_YES"
