"""Unit tests for KBO & NPB International Baseball Tie-Aware Engine."""

from __future__ import annotations

import pytest

from model_prediction.models.intl_baseball import (
    IntlBaseballEngine,
    IntlStarterRecord,
)


def test_intl_starter_shrinkage():
    engine = IntlBaseballEngine(league="KBO", stabilization_bf=150.0)

    # Ace starter with high K% and low BB%
    for i in range(1, 6):
        engine.record_starter_outing(
            IntlStarterRecord(
                pitcher_id="sp_ace",
                game_date=f"2026-05-{i:02d}",
                batters_faced=28,
                innings_pitched=7.0,
                strikeouts=9,
                walks=1,
                home_runs=0,
                earned_runs=1,
            )
        )

    state = engine.evaluate_starter("sp_ace", as_of_date="2026-05-15")
    assert state.games == 5
    assert state.total_bf == 140
    assert state.shrunk_k_pct > 0.25
    assert state.shrunk_bb_pct < 0.065
    assert state.k_minus_bb_pct > 0.18
    assert state.shrunk_fip < 3.0


def test_kbo_vs_npb_tie_pricing_and_payout():
    engine_kbo = IntlBaseballEngine(league="KBO")
    engine_npb = IntlBaseballEngine(league="NPB")

    forecast_kbo = engine_kbo.forecast_matchup(
        "LG Twins", "Kia Tigers", "sp1", "sp2", as_of_date="2026-06-01"
    )
    forecast_npb = engine_npb.forecast_matchup(
        "Yomiuri Giants", "Hanshin Tigers", "sp1", "sp2", as_of_date="2026-06-01"
    )

    # NPB has higher tie rate than KBO
    assert forecast_npb.p_tie > forecast_kbo.p_tie

    # Probabilities + Tie sum to 1.0
    assert pytest.approx(forecast_kbo.p_home_win + forecast_kbo.p_away_win + forecast_kbo.p_tie) == 1.0
    assert pytest.approx(forecast_npb.p_home_win + forecast_npb.p_away_win + forecast_npb.p_tie) == 1.0

    # Polymarket expected payouts sum to 1.0
    # (P(H) + 0.5*P(T)) + (P(A) + 0.5*P(T)) = P(H) + P(A) + P(T) = 1.0
    assert pytest.approx(forecast_kbo.expected_payout_home + forecast_kbo.expected_payout_away) == 1.0
    assert pytest.approx(forecast_npb.expected_payout_home + forecast_npb.expected_payout_away) == 1.0
