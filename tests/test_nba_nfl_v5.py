"""Tests for NBA Structural v5 and NFL Structural v5 engines."""

from model_prediction.models.nba_structural_v5 import (
    NBAStructuralV5Engine,
)
from model_prediction.models.nfl_structural_v5 import (
    NFLStructuralV5Engine,
)


def test_nba_structural_v5_pace_and_hfa():
    engine = NBAStructuralV5Engine()
    fc = engine.forecast_game(
        home_team="Celtics",
        away_team="Heat",
        home_pace=101.0,
        away_pace=98.0,
        home_ortg=118.0,
        home_drtg=110.0,
        away_ortg=112.0,
        away_drtg=114.0,
        spread_home_line=-7.5,
        total_line=225.5,
    )

    assert fc.projected_possessions > 95.0
    assert fc.projected_home_points > fc.projected_away_points
    assert fc.prob_home_win > 0.50
    assert abs(fc.prob_home_win + fc.prob_away_win - 1.0) < 1e-4
    assert abs(fc.prob_home_cover + fc.prob_away_cover - 1.0) < 1e-4
    assert abs(fc.prob_over + fc.prob_under - 1.0) < 1e-4


def test_nfl_structural_v5_weather_penalty_and_key_numbers():
    engine = NFLStructuralV5Engine()
    fc_clear = engine.forecast_game(
        home_team="Chiefs",
        away_team="Bills",
        wind_mph=5.0,
        spread_home_line=-3.0,
        total_line=48.5,
    )
    fc_windy = engine.forecast_game(
        home_team="Chiefs",
        away_team="Bills",
        wind_mph=25.0,  # High wind suppression
        spread_home_line=-3.0,
        total_line=48.5,
    )

    assert fc_windy.projected_total < fc_clear.projected_total
    assert fc_windy.weather_total_penalty > 0.0
    assert fc_windy.prob_under > fc_clear.prob_under


def test_nfl_structural_v5_short_week_drag():
    """Verify Thursday Night Football / short-week (<= 5 days rest) applies -3.5 pt total drag."""
    engine = NFLStructuralV5Engine()
    fc_standard = engine.forecast_game(
        home_team="Seahawks",
        away_team="Broncos",
        home_rest_days=7,
        away_rest_days=7,
        total_line=40.5,
    )
    fc_tnf = engine.forecast_game(
        home_team="Seahawks",
        away_team="Broncos",
        home_rest_days=5,
        away_rest_days=5,
        total_line=40.5,
    )

    # TNF short week should reduce projected total by exactly 3.5 points
    assert fc_tnf.short_week_penalty == 3.5
    assert fc_standard.short_week_penalty == 0.0
    assert round(fc_standard.projected_total - fc_tnf.projected_total, 2) == 3.5
    assert fc_tnf.prob_under > fc_standard.prob_under
