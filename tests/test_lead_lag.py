"""Unit tests for Sharp-Book Lead/Lag Execution Signal Engine."""

from __future__ import annotations

import pytest

from model_prediction.portfolio.lead_lag import SharpLeadLagAnalyzer


def test_sharp_lead_lag_urgent_taker():
    analyzer = SharpLeadLagAnalyzer(min_lag_taker_threshold=0.020)

    # Sharp book moved to 0.58, Polymarket still at 0.53 -> 5% lag edge
    signal = analyzer.evaluate_latency(
        market_id="m123",
        target_selection="New York Yankees",
        sharp_reference_prob=0.58,
        polymarket_prob=0.53,
        prior_sharp_prob=0.52,
        minutes_elapsed=10.0,
    )

    assert signal.execution_urgency == "URGENT_TAKER"
    assert signal.lag_delta == 0.05
    assert signal.sharp_velocity_bps_per_min == pytest.approx(60.0)  # +60 bps/min
    assert "Immediate IOC" in signal.recommended_action


def test_sharp_lead_lag_passive_maker():
    analyzer = SharpLeadLagAnalyzer(min_lag_taker_threshold=0.020)

    # Sharp book at 0.54, Polymarket at 0.535 -> in sync (lag 0.005)
    signal = analyzer.evaluate_latency(
        market_id="m124",
        target_selection="Boston Red Sox",
        sharp_reference_prob=0.54,
        polymarket_prob=0.535,
    )

    assert signal.execution_urgency == "PASSIVE_MAKER"
    assert signal.lag_delta == 0.005
    assert "Post resting limit" in signal.recommended_action


def test_sharp_lead_lag_adverse_selection():
    analyzer = SharpLeadLagAnalyzer(min_lag_taker_threshold=0.020)

    # Polymarket at 0.60, Sharp book at 0.55 -> Polymarket is overpriced (lag -0.05)
    signal = analyzer.evaluate_latency(
        market_id="m125",
        target_selection="LA Dodgers",
        sharp_reference_prob=0.55,
        polymarket_prob=0.60,
    )

    assert signal.execution_urgency == "ADVERSE_SELECTION_WARN"
    assert signal.lag_delta == -0.05
    assert "Hold orders" in signal.recommended_action
