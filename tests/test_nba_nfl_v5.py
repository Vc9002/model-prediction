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


def test_nfl_qb_injury_status_detection():
    """Verify check_nfl_qb_injury_status detects Out, IR, Doubtful, and Questionable QBs."""
    from model_prediction.features.nfl_qb_oline import check_nfl_qb_injury_status

    entries = [
        {"player_name": "Patrick Mahomes", "team": "Kansas City Chiefs", "position": "QB", "status": "Out"},
        {"player_name": "Josh Allen", "team": "Buffalo Bills", "position": "QB", "status": "Questionable"},
        {"player_name": "Lamar Jackson", "team": "Baltimore Ravens", "position": "QB", "status": "Doubtful"},
        {"player_name": "Aaron Rodgers", "team": "New York Jets", "position": "QB", "status": "IR"},
        {"player_name": "Travis Kelce", "team": "Kansas City Chiefs", "position": "TE", "status": "Out"},
    ]

    is_out, stat, pen = check_nfl_qb_injury_status("Kansas City Chiefs", "Patrick Mahomes", entries)
    assert is_out is True
    assert stat == "Out"
    assert pen == 4.5

    is_out_ar, stat_ar, pen_ar = check_nfl_qb_injury_status("New York Jets", "Aaron Rodgers", entries)
    assert is_out_ar is True
    assert stat_ar == "IR"
    assert pen_ar == 4.5

    is_out_lj, stat_lj, pen_lj = check_nfl_qb_injury_status("Baltimore Ravens", "Lamar Jackson", entries)
    assert is_out_lj is False
    assert stat_lj == "Doubtful"
    assert pen_lj == 2.5

    is_out_ja, stat_ja, pen_ja = check_nfl_qb_injury_status("Buffalo Bills", "Josh Allen", entries)
    assert is_out_ja is False
    assert stat_ja == "Questionable"
    assert pen_ja == 1.0

    # Healthy unlisted player
    is_out_jb, stat_jb, pen_jb = check_nfl_qb_injury_status("Cincinnati Bengals", "Joe Burrow", entries)
    assert is_out_jb is False
    assert stat_jb == "Healthy"
    assert pen_jb == 0.0


def test_nfl_structural_v5_qb_replacement_penalty():
    """Verify QB tier adjustment decrement reduces spread margin and shifts cover probability."""
    engine = NFLStructuralV5Engine()
    fc_starter = engine.forecast_game(
        home_team="Chiefs",
        away_team="Raiders",
        home_qb_tier_adj=3.0,  # Elite starter tier
        spread_home_line=-7.5,
    )
    fc_backup = engine.forecast_game(
        home_team="Chiefs",
        away_team="Raiders",
        home_qb_tier_adj=3.0 - 4.5,  # Replaced by backup (-4.5 pt penalty)
        spread_home_line=-7.5,
    )

    # Margin should drop by 4.5 points
    assert round(fc_starter.projected_margin_home - fc_backup.projected_margin_home, 1) == 4.5
    assert fc_backup.prob_home_cover < fc_starter.prob_home_cover
    assert fc_backup.prob_home_win < fc_starter.prob_home_win
