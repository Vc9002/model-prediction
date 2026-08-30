"""Comprehensive test verifying active computation and non-fallback behavior across all models and features.

Verifies:
1. Feature values vary dynamically (variance > 0) across distinct teams, matchups, and conditions.
2. Features are not frozen or stuck on constant fallback placeholders.
3. Model prediction outputs respond directly to feature shifts (proving feature ingestion and weights are active).
4. Covers MLB, WNBA, Soccer, Tennis, Esports, and NFL models and feature engines.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from model_prediction.esports import NeutralElo
from model_prediction.features.air_density import air_density, pressure_from_altitude_pa
from model_prediction.features.elo_ratings import ELO_CONFIG, EloBook, expected_win_probability
from model_prediction.features.nfl_qb_oline import (
    NFLOffensiveLineProfile,
    evaluate_qb_profile,
    extract_nfl_matchup_features,
)
from model_prediction.features.park_factors import PARK_RUN_FACTORS
from model_prediction.features.wnba_pace_four_factors import compute_team_four_factors
from model_prediction.models.mlb_first_inning import FEATURE_NAMES, FirstInningGameRow, MLBFirstInningModel
from model_prediction.models.soccer_dixon_coles import DixonColesEngine
from model_prediction.rebuild.models.tennis import TennisEloManager

# ── 1. MLB Feature & Model Responsiveness ─────────────────────────────────────


def test_mlb_features_have_nonzero_variance_and_no_constant_fallbacks() -> None:
    """MLB Elo and park factor features must produce varying, dynamic values across teams."""
    mlb_cfg = ELO_CONFIG["mlb"]
    book = EloBook(
        ratings={
            "New York Yankees": 1580.0,
            "Los Angeles Dodgers": 1600.0,
            "Houston Astros": 1540.0,
            "Boston Red Sox": 1490.0,
            "Oakland Athletics": 1420.0,
            "Colorado Rockies": 1390.0,
        },
        k=mlb_cfg["k"],
        home_advantage=mlb_cfg["home_advantage"],
    )

    matchups = [
        ("New York Yankees", "Boston Red Sox"),
        ("Los Angeles Dodgers", "Oakland Athletics"),
        ("Houston Astros", "Colorado Rockies"),
        ("Oakland Athletics", "New York Yankees"),
    ]

    elo_probs = []
    for home, away in matchups:
        p = expected_win_probability(book.rating(home), book.rating(away), advantage=book.home_advantage)
        elo_probs.append(p)

    # 1. Elo probabilities must vary across matchups
    assert np.var(elo_probs) > 1e-3, f"Elo probabilities have zero variance: {elo_probs}"
    assert all(0.30 <= p <= 0.85 for p in elo_probs)
    assert not all(p == 0.5 for p in elo_probs), "Elo is stuck on 0.5 fallback"

    # 2. Park factors in catalog must vary significantly across ballparks
    factors = list(PARK_RUN_FACTORS.values())
    assert len(factors) >= 20
    assert np.var(factors) > 1e-4, f"Park factors have zero variance: {factors}"
    assert PARK_RUN_FACTORS["Colorado Rockies"] > PARK_RUN_FACTORS["Seattle Mariners"]
    assert PARK_RUN_FACTORS["Colorado Rockies"] >= 1.15  # Coors elevation effect
    assert PARK_RUN_FACTORS["Seattle Mariners"] <= 0.96  # T-Mobile pitcher park effect


def test_mlb_nrfi_first_inning_model_active_computation() -> None:
    """MLB First Inning (NRFI) model must compute dynamic zero-run probabilities responsive to inputs."""
    model = MLBFirstInningModel()
    # Provide fitted coefficients and scaler mean/scale for 19 features
    model.feature_names = list(FEATURE_NAMES)
    model.coef = [-0.15] * len(FEATURE_NAMES)
    model.intercept = 0.05
    model.scaler_mean = [0.5] * len(FEATURE_NAMES)
    model.scaler_scale = [0.2] * len(FEATURE_NAMES)

    # Matchup 1: Elite pitching matchup (low runs allowed)
    elite_features = {k: 0.20 for k in FEATURE_NAMES}
    elite_row = FirstInningGameRow(
        game_pk=1001,
        game_start_utc="2026-06-01T19:00:00Z",
        home_team="NYY",
        away_team="BOS",
        venue_name="Yankee Stadium",
        features=elite_features,
        nrfi=1,
        runs_1st_total=0.0,
    )

    # Matchup 2: High scoring matchup (high runs allowed)
    slugfest_features = {k: 0.85 for k in FEATURE_NAMES}
    slugfest_row = FirstInningGameRow(
        game_pk=1002,
        game_start_utc="2026-06-01T19:00:00Z",
        home_team="COL",
        away_team="CIN",
        venue_name="Coors Field",
        features=slugfest_features,
        nrfi=0,
        runs_1st_total=2.0,
    )

    p_nrfi_elite = model.predict_p_nrfi(elite_row)
    p_nrfi_slugfest = model.predict_p_nrfi(slugfest_row)

    assert p_nrfi_elite > p_nrfi_slugfest
    assert p_nrfi_elite - p_nrfi_slugfest > 0.15, (
        f"NRFI model failed to differentiate matchup types: elite={p_nrfi_elite:.4f}, slugfest={p_nrfi_slugfest:.4f}"
    )


# ── 2. WNBA Four Factors & Possession Pace Feature Active Use ─────────────────


def test_wnba_four_factors_and_pace_features() -> None:
    """WNBA possession pace & Four Factors must compute dynamic efficiency metrics."""
    # Fast pace, high scoring logs
    fast_logs = [
        {
            "fga": 76,
            "fta": 22,
            "tov": 14,
            "oreb": 11,
            "dreb": 28,
            "fgm": 35,
            "fg3m": 10,
            "pts": 92,
            "opp_dreb": 24,
            "opp_pts": 85,
        }
        for _ in range(10)
    ]
    # Slow pace, defensive logs
    slow_logs = [
        {
            "fga": 62,
            "fta": 14,
            "tov": 8,
            "oreb": 6,
            "dreb": 32,
            "fgm": 26,
            "fg3m": 6,
            "pts": 68,
            "opp_dreb": 30,
            "opp_pts": 62,
        }
        for _ in range(10)
    ]

    fast_metrics = compute_team_four_factors("Las Vegas Aces", fast_logs)
    slow_metrics = compute_team_four_factors("Connecticut Sun", slow_logs)

    # Pace (possessions per 40 min)
    assert fast_metrics.pace_40m > slow_metrics.pace_40m
    assert abs(fast_metrics.pace_40m - slow_metrics.pace_40m) > 4.0, "Possession pace did not diverge"

    # Effective FG% and Projected Points
    assert fast_metrics.efg_pct > slow_metrics.efg_pct
    assert fast_metrics.projected_points_per_game > slow_metrics.projected_points_per_game
    assert not math.isclose(fast_metrics.efg_pct, 0.0)


# ── 3. Soccer Dixon-Coles Active Poisson Expectancy ───────────────────────────


def test_soccer_dixon_coles_goal_expectancy_and_probabilities() -> None:
    """Soccer Dixon-Coles engine must compute dynamic attack/defense strengths and 1X2 odds."""
    engine = DixonColesEngine(xi=0.002)

    # Fit small synthetic league history (Top offense vs Weak defense)
    matches = [
        {
            "home_team": "Manchester City",
            "away_team": "Sheffield United",
            "home_score": 4,
            "away_score": 0,
            "date": "2026-03-01",
        },
        {
            "home_team": "Manchester City",
            "away_team": "Burnley",
            "home_score": 3,
            "away_score": 0,
            "date": "2026-03-08",
        },
        {
            "home_team": "Arsenal",
            "away_team": "Sheffield United",
            "home_score": 5,
            "away_score": 0,
            "date": "2026-03-15",
        },
        {
            "home_team": "Sheffield United",
            "away_team": "Burnley",
            "home_score": 1,
            "away_score": 1,
            "date": "2026-03-22",
        },
    ]
    engine.fit(matches)

    # Predict: Man City (home) vs Sheffield (away)
    city_sheff = engine.predict_match("Manchester City", "Sheffield United")
    # Predict: Sheffield (home) vs Man City (away)
    sheff_city = engine.predict_match("Sheffield United", "Manchester City")

    # Man City home win prob must be heavily favored over Sheffield home win prob
    assert city_sheff.prob_home > 0.65, f"Man City home win prob unexpectedly low: {city_sheff}"
    assert sheff_city.prob_home < 0.25, f"Sheffield home win prob unexpectedly high: {sheff_city}"
    assert city_sheff.lambda_home > sheff_city.lambda_home + 1.0


# ── 4. Tennis Surface-Elo Active Adjustment ───────────────────────────────────


def test_tennis_surface_elo_dynamic_surface_advantage() -> None:
    """Tennis Surface-Elo model must apply clay vs grass surface adjustments."""
    manager = TennisEloManager(k=32.0, surface_k_boost=8.0)

    # Player A: Clay Specialist (many clay matches & high rating)
    manager.ratings["Carlos Alcaraz"] = 2050.0
    manager.surface_ratings["Carlos Alcaraz"]["Clay"] = 2200.0
    manager.surface_ratings["Carlos Alcaraz"]["Hard"] = 1980.0
    manager.surface_match_count["Carlos Alcaraz"]["Clay"] = 30
    manager.surface_match_count["Carlos Alcaraz"]["Hard"] = 30

    # Player B: Hard Court Specialist
    manager.ratings["Daniil Medvedev"] = 2050.0
    manager.surface_ratings["Daniil Medvedev"]["Clay"] = 1850.0
    manager.surface_ratings["Daniil Medvedev"]["Hard"] = 2150.0
    manager.surface_match_count["Daniil Medvedev"]["Clay"] = 30
    manager.surface_match_count["Daniil Medvedev"]["Hard"] = 30

    prob_on_clay = manager.expected_win("Carlos Alcaraz", "Daniil Medvedev", surface="Clay")
    prob_on_hard = manager.expected_win("Carlos Alcaraz", "Daniil Medvedev", surface="Hard")

    # Alcaraz win probability must be substantially higher on Clay than Hard
    assert prob_on_clay > prob_on_hard
    assert prob_on_clay - prob_on_hard > 0.20, (
        f"Surface weighting did not differentiate surfaces: clay={prob_on_clay:.4f}, hard={prob_on_hard:.4f}"
    )


# ── 5. Esports Title-Specific Elo Active Modeling ─────────────────────────────


def test_esports_neutral_elo_active_probability_computation() -> None:
    """Esports NeutralElo engine must compute non-constant probabilities responsive to rating deltas."""
    book = NeutralElo(
        k=24.0,
        ratings={"Team Spirit": 2100.0, "FaZe Clan": 1850.0, "Wildcard": 1500.0},
        games_played={"Team Spirit": 50, "FaZe Clan": 50, "Wildcard": 50},
    )

    # Heavy Favorite vs Underdog
    p_fav = book.probability("Team Spirit", "Wildcard")
    # Even Matchup
    p_even = book.probability("FaZe Clan", "FaZe Clan")
    # Underdog vs Heavy Favorite
    p_dog = book.probability("Wildcard", "Team Spirit")

    assert p_fav > 0.85
    assert math.isclose(p_even, 0.50, abs_tol=1e-3)
    assert p_dog < 0.15
    assert p_fav + p_dog == pytest.approx(1.0, abs=1e-3)


# ── 6. NFL Starting QB & Offensive Line Vector ────────────────────────────────


def test_nfl_qb_oline_active_feature_computation() -> None:
    """NFL QB state vector must penalize backup quarterbacks relative to elite starters."""
    elite_qb = evaluate_qb_profile(
        qb_name="Patrick Mahomes",
        team="Kansas City Chiefs",
        epa_per_dropback=0.25,
        cpoe=4.5,
        pressure_to_sack_pct=0.12,
        turnover_worthy_play_pct=0.018,
        sample_dropbacks=500,
        is_starter=True,
    )

    backup_qb = evaluate_qb_profile(
        qb_name="Backup QB",
        team="Carolina Panthers",
        epa_per_dropback=-0.15,
        cpoe=-4.0,
        pressure_to_sack_pct=0.26,
        turnover_worthy_play_pct=0.045,
        sample_dropbacks=30,
        is_starter=False,
    )

    elite_oline = NFLOffensiveLineProfile(
        team="Kansas City Chiefs",
        pass_block_rating=85.0,
        run_block_rating=78.0,
        missing_starters=0,
        adjusted_sack_rate=0.042,
        oline_penalty_pts=0.0,
    )

    poor_oline = NFLOffensiveLineProfile(
        team="Carolina Panthers",
        pass_block_rating=45.0,
        run_block_rating=50.0,
        missing_starters=2,
        adjusted_sack_rate=0.095,
        oline_penalty_pts=-1.0,
    )

    matchup = extract_nfl_matchup_features(
        home_qb=elite_qb,
        away_qb=backup_qb,
        home_oline=elite_oline,
        away_oline=poor_oline,
        home_base_success_rate=0.54,
        away_base_success_rate=0.42,
    )

    assert matchup.qb_value_gap > 3.0, "Elite QB vs Backup spread gap must exceed 3 pts"
    assert matchup.oline_protection_gap > 20.0
    assert matchup.projected_spread_margin > 6.0  # Chiefs heavily favored at home


# ── 7. Atmospheric Physics & Air Density Non-Fallback ─────────────────────────


def test_air_density_physics_computation() -> None:
    """Air density physics engine must compute dynamic barometric density."""
    # Sea-level cold day (e.g. Seattle, 5C, 1013 hPa, 80% RH)
    seattle_p = 101325.0
    seattle_density = air_density(temp_c=5.0, pressure_pa=seattle_p, relative_humidity=80.0)

    # Mile-high hot day (e.g. Denver, 1600m altitude, 32C, 25% RH)
    denver_p = pressure_from_altitude_pa(1600.0)
    denver_density = air_density(temp_c=32.0, pressure_pa=denver_p, relative_humidity=25.0)

    # Denver air density should be ~20% lighter than Seattle sea-level air
    assert denver_density.density_kg_m3 < seattle_density.density_kg_m3
    assert denver_density.density_ratio < 0.85
    assert seattle_density.density_ratio > 1.00
    assert abs(seattle_density.density_ratio - denver_density.density_ratio) > 0.20
