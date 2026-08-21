"""Tests for Point-in-Time Empirical Bayes batter priors module."""

from __future__ import annotations

import pytest

from model_prediction.features.batter_priors import (
    PRIOR_HYPERPARAMETERS,
    BatterGameRecord,
    BatterPriorState,
    PointInTimeBatterPriorEngine,
    beta_binomial_shrink,
)


def test_beta_binomial_shrink_zero_sample():
    mu_0, _ = PRIOR_HYPERPARAMETERS["k_pct"]
    assert beta_binomial_shrink(0, 0, "k_pct") == mu_0


def test_beta_binomial_shrink_large_sample_converges():
    # 300 strikeouts in 1000 PA = 0.300
    shrunk = beta_binomial_shrink(300, 1000, "k_pct")
    assert abs(shrunk - 0.300) < 0.01


def test_beta_binomial_shrink_monotonic():
    low = beta_binomial_shrink(10, 100, "k_pct")
    high = beta_binomial_shrink(40, 100, "k_pct")
    assert low < high


def test_beta_binomial_shrink_invalid_metric():
    with pytest.raises(ValueError, match="Unknown metric"):
        beta_binomial_shrink(10, 100, "invalid_metric")


def test_batter_prior_state_calculations():
    state = BatterPriorState(
        player_id="player_1",
        total_pa=100,
        total_ab=90,
        total_hits=25,
        total_doubles=5,
        total_triples=1,
        total_home_runs=4,
        total_strikeouts=20,
        total_walks=10,
        total_hbp=0,
        total_bip=65,
        total_hard_hit=25,
        total_barrel=6,
    )
    assert 0.15 < state.shrunk_k_pct() < 0.30
    assert 0.05 < state.shrunk_bb_pct() < 0.15
    assert 0.10 < state.shrunk_iso() < 0.30
    assert 0.30 < state.shrunk_hard_hit_pct() < 0.50
    assert 0.05 < state.shrunk_barrel_pct() < 0.15
    assert 0.28 < state.shrunk_xwoba() < 0.40


def test_point_in_time_engine_lineup_weights():
    engine = PointInTimeBatterPriorEngine()
    # Populate player 1 (elite hitter) and player 9 (weak hitter)
    engine.update_player_game(
        BatterGameRecord(
            player_id="elite_1",
            team_id="NYY",
            game_date="2026-05-01",
            pa=100,
            ab=85,
            hits=35,
            doubles=8,
            triples=1,
            home_runs=10,
            strikeouts=15,
            walks=15,
            bip_count=60,
            hard_hit_count=30,
            barrel_count=10,
        )
    )
    engine.update_player_game(
        BatterGameRecord(
            player_id="weak_9",
            team_id="NYY",
            game_date="2026-05-01",
            pa=100,
            ab=95,
            hits=15,
            doubles=2,
            triples=0,
            home_runs=0,
            strikeouts=35,
            walks=5,
            bip_count=60,
            hard_hit_count=10,
            barrel_count=1,
        )
    )

    lineup_9 = ["elite_1"] + ["weak_9"] * 8
    res_top = engine.evaluate_confirmed_lineup(lineup_9)

    lineup_bottom = ["weak_9"] * 8 + ["elite_1"]
    res_bottom = engine.evaluate_confirmed_lineup(lineup_bottom)

    # Placing elite hitter at leadoff gives higher weighted lineup xwOBA than batting 9th
    assert res_top.xwoba > res_bottom.xwoba
    assert res_top.k_pct < res_bottom.k_pct


def test_point_in_time_engine_pit_boundary():
    engine = PointInTimeBatterPriorEngine()
    engine.update_player_game(
        BatterGameRecord(
            player_id="p1",
            team_id="BOS",
            game_date="2026-05-01",
            pa=4,
            ab=4,
            hits=2,
            doubles=1,
            home_runs=1,
        )
    )
    engine.update_player_game(
        BatterGameRecord(
            player_id="p1",
            team_id="BOS",
            game_date="2026-05-10",
            pa=4,
            ab=4,
            hits=0,
            strikeouts=4,
        )
    )

    # Evaluating as of May 5 must only see the May 1 game
    proj_early = engine.evaluate_projected_team_offense("BOS", as_of_date="2026-05-05")
    assert proj_early.sample_pa == 4

    # Evaluating as of May 15 sees both games
    proj_late = engine.evaluate_projected_team_offense("BOS", as_of_date="2026-05-15")
    assert proj_late.sample_pa == 8


def test_point_in_time_engine_empty_team_fallback():
    engine = PointInTimeBatterPriorEngine()
    proj = engine.evaluate_projected_team_offense("NONEXISTENT", as_of_date="2026-05-01")
    assert proj.xwoba == PRIOR_HYPERPARAMETERS["xwoba"][0]
    assert proj.sample_pa == 0
