"""Tests for WNBA Structural v3 Engine (wnba-spread-structural-v3 & wnba-total-possession-v3)."""

from model_prediction.models.wnba_structural_v3 import WNBAStructuralV3Engine


def test_wnba_structural_v3_forecast():
    engine = WNBAStructuralV3Engine()
    fc = engine.forecast_game(
        home_team="Liberty",
        away_team="Aces",
        home_pace=82.0,
        away_pace=81.0,
        home_ortg_ppp=1.08,
        home_drtg_ppp=0.98,
        away_ortg_ppp=1.04,
        away_drtg_ppp=1.02,
        spread_home_line=-5.5,
        total_line=168.5,
    )

    assert fc.projected_possessions > 75.0
    assert fc.projected_home_points > fc.projected_away_points
    assert fc.prob_home_win > 0.50
    assert abs(fc.prob_home_win + fc.prob_away_win - 1.0) < 1e-4
    assert abs(fc.prob_home_cover + fc.prob_away_cover - 1.0) < 1e-4
    assert abs(fc.prob_over + fc.prob_under - 1.0) < 1e-4


def test_wnba_structural_v3_cross_market_monotonicity():
    engine = WNBAStructuralV3Engine()
    fc_base = engine.forecast_game("Liberty", "Aces", spread_home_line=-4.5, total_line=165.5)

    # Increasing home offensive efficiency increases home win prob
    fc_better_home = engine.forecast_game(
        "Liberty",
        "Aces",
        home_ortg_ppp=1.12,
        spread_home_line=-4.5,
        total_line=165.5,
    )
    assert fc_better_home.prob_home_win > fc_base.prob_home_win
    assert fc_better_home.prob_home_cover > fc_base.prob_home_cover

    # Increasing pace increases projected total and prob over fixed total
    fc_faster = engine.forecast_game(
        "Liberty",
        "Aces",
        home_pace=85.0,
        away_pace=85.0,
        spread_home_line=-4.5,
        total_line=165.5,
    )
    assert fc_faster.projected_total > fc_base.projected_total
    assert fc_faster.prob_over > fc_base.prob_over


def test_wnba_structural_v3_missing_player_minutes_impact():
    engine = WNBAStructuralV3Engine()
    fc_full = engine.forecast_game("Liberty", "Aces", home_missing_minutes=0.0)
    fc_injured = engine.forecast_game("Liberty", "Aces", home_missing_minutes=32.0)

    assert fc_injured.projected_home_points < fc_full.projected_home_points
    assert fc_injured.prob_home_win < fc_full.prob_home_win
