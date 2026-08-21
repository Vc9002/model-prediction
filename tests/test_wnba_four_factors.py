"""Unit tests for WNBA Pace and Four Factors feature calculations."""

from __future__ import annotations

from model_prediction.features.wnba_pace_four_factors import (
    LEAGUE_PACE_40M,
    compute_team_four_factors,
    project_wnba_game_total,
)


def test_empty_logs_returns_league_baseline() -> None:
    factors = compute_team_four_factors("New York Liberty", [])
    assert factors.games_played == 0
    assert factors.pace_40m == LEAGUE_PACE_40M
    assert 0.0 < factors.efg_pct < 1.0
    assert factors.projected_points_per_game > 50.0


def test_efficient_team_has_higher_off_rating() -> None:
    high_eff_logs = [
        {
            "points": 90,
            "opp_points": 70,
            "fgm": 35,
            "fga": 65,
            "fg3m": 10,
            "fta": 15,
            "turnovers": 8,
            "oreb": 8,
            "opp_dreb": 22,
        }
        for _ in range(10)
    ]
    low_eff_logs = [
        {
            "points": 65,
            "opp_points": 85,
            "fgm": 22,
            "fga": 65,
            "fg3m": 3,
            "fta": 15,
            "turnovers": 18,
            "oreb": 5,
            "opp_dreb": 25,
        }
        for _ in range(10)
    ]

    team_high = compute_team_four_factors("High Eff", high_eff_logs)
    team_low = compute_team_four_factors("Low Eff", low_eff_logs)

    assert team_high.off_rating > team_low.off_rating
    assert team_high.efg_pct > team_low.efg_pct
    assert team_high.tov_pct < team_low.tov_pct


def test_game_total_projection() -> None:
    fast_logs = [
        {
            "points": 88,
            "opp_points": 85,
            "fgm": 32,
            "fga": 75,
            "fg3m": 8,
            "fta": 20,
            "turnovers": 12,
            "oreb": 9,
            "opp_dreb": 24,
        }
        for _ in range(10)
    ]
    slow_logs = [
        {
            "points": 70,
            "opp_points": 68,
            "fgm": 26,
            "fga": 60,
            "fg3m": 5,
            "fta": 14,
            "turnovers": 14,
            "oreb": 6,
            "opp_dreb": 22,
        }
        for _ in range(10)
    ]

    fast_team = compute_team_four_factors("Fast Team", fast_logs)
    slow_team = compute_team_four_factors("Slow Team", slow_logs)

    proj = project_wnba_game_total(fast_team, slow_team)

    assert "projected_total" in proj
    assert "game_pace" in proj
    assert 130.0 < proj["projected_total"] < 200.0
