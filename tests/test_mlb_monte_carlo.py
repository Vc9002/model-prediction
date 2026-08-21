"""Unit tests for MLB Monte Carlo Discrete-Event Simulation Engine."""

from __future__ import annotations

import numpy as np
import pytest

from model_prediction.features.pitch_arsenal import create_sample_pitch_arsenal
from model_prediction.models.base import ScoreSimulation
from model_prediction.models.mlb_monte_carlo import (
    BaseOccupancy,
    BaseRunnerState,
    BatterProfile,
    MLBMonteCarloEngine,
    MLBMonteCarloResult,
    PAOutcome,
    PitcherProfile,
    SingleGameSimulation,
    create_sample_lineup,
)


def test_pa_outcome_classes():
    """Verify exactly 8 PA outcome classes exist."""
    assert len(PAOutcome) == 8
    expected = {
        "single",
        "double",
        "triple",
        "home_run",
        "walk_hbp",
        "strikeout",
        "field_out",
        "gidp",
    }
    assert {o.value for o in PAOutcome} == expected


def test_base_occupancy_states():
    """Verify 8 base occupancy states binary encoding and BaseRunnerState conversions."""
    assert len(BaseOccupancy) == 8
    assert BaseOccupancy.EMPTY == 0
    assert BaseOccupancy.FIRST == 1
    assert BaseOccupancy.SECOND == 2
    assert BaseOccupancy.FIRST_SECOND == 3
    assert BaseOccupancy.THIRD == 4
    assert BaseOccupancy.FIRST_THIRD == 5
    assert BaseOccupancy.SECOND_THIRD == 6
    assert BaseOccupancy.LOADED == 7

    # From occupancy
    state_loaded = BaseRunnerState.from_occupancy(BaseOccupancy.LOADED)
    assert state_loaded.first and state_loaded.second and state_loaded.third
    assert state_loaded.occupancy == BaseOccupancy.LOADED
    assert state_loaded.runners_count() == 3

    state_f3 = BaseRunnerState.from_occupancy(BaseOccupancy.FIRST_THIRD)
    assert state_f3.first and not state_f3.second and state_f3.third
    assert state_f3.occupancy == BaseOccupancy.FIRST_THIRD


def test_base_advancement_walk_hbp():
    """Verify force advancement logic on Walk/HBP."""
    engine = MLBMonteCarloEngine()
    rng = np.random.default_rng(42)

    # Empty -> 1B
    s = BaseRunnerState()
    runs, outs = engine.simulate_pa(s, outs=0, outcome=PAOutcome.WALK_HBP, rng=rng)
    assert runs == 0 and outs == 0
    assert s.first and not s.second and not s.third

    # 1B -> 1B + 2B
    runs, outs = engine.simulate_pa(s, outs=0, outcome=PAOutcome.WALK_HBP, rng=rng)
    assert runs == 0 and outs == 0
    assert s.first and s.second and not s.third

    # 1B + 2B -> Loaded
    runs, outs = engine.simulate_pa(s, outs=0, outcome=PAOutcome.WALK_HBP, rng=rng)
    assert runs == 0 and outs == 0
    assert s.first and s.second and s.third

    # Loaded -> 1 run scores, still Loaded
    runs, outs = engine.simulate_pa(s, outs=0, outcome=PAOutcome.WALK_HBP, rng=rng)
    assert runs == 1 and outs == 0
    assert s.first and s.second and s.third


def test_base_advancement_hits():
    """Verify run scoring and runner advancement on Single, Double, Triple, Home Run."""
    engine = MLBMonteCarloEngine(p_1b_to_3b_on_single=0.0, p_2b_score_on_single=1.0, p_1b_score_on_double=0.0)
    rng = np.random.default_rng(42)

    # Home Run with Bases Loaded -> 4 runs, bases empty
    s_loaded = BaseRunnerState(first=True, second=True, third=True)
    runs, outs = engine.simulate_pa(s_loaded, outs=1, outcome=PAOutcome.HOME_RUN, rng=rng)
    assert runs == 4 and outs == 1
    assert not s_loaded.first and not s_loaded.second and not s_loaded.third

    # Triple with 1B + 2B -> 2 runs, batter on 3B
    s_12 = BaseRunnerState(first=True, second=True, third=False)
    runs, outs = engine.simulate_pa(s_12, outs=0, outcome=PAOutcome.TRIPLE, rng=rng)
    assert runs == 2 and outs == 0
    assert not s_12.first and not s_12.second and s_12.third

    # Double with 1B + 2B -> 2B scores (1 run), 1B to 3B, batter on 2B
    s_12 = BaseRunnerState(first=True, second=True, third=False)
    runs, outs = engine.simulate_pa(s_12, outs=0, outcome=PAOutcome.DOUBLE, rng=rng)
    assert runs == 1 and outs == 0
    assert not s_12.first and s_12.second and s_12.third

    # Single with 2B + 3B -> 3B scores, 2B scores (since p_2b_score=1.0), batter on 1B
    s_23 = BaseRunnerState(first=False, second=True, third=True)
    runs, outs = engine.simulate_pa(s_23, outs=0, outcome=PAOutcome.SINGLE, rng=rng)
    assert runs == 2 and outs == 0
    assert s_23.first and not s_23.second and not s_23.third


def test_base_advancement_gidp():
    """Verify Ground Into Double Play (GIDP) logic."""
    engine = MLBMonteCarloEngine(p_gidp_score_3b=1.0)
    rng = np.random.default_rng(42)

    # 0 outs with 1B + 3B -> 2 outs recorded, 3B scores, 1B out, batter out
    s = BaseRunnerState(first=True, second=False, third=True)
    runs, outs = engine.simulate_pa(s, outs=0, outcome=PAOutcome.GIDP, rng=rng)
    assert outs == 2
    assert runs == 1
    assert not s.first and not s.third

    # 1 out with 1B -> 3 outs (inning over), 0 runs
    s1 = BaseRunnerState(first=True, second=False, third=False)
    runs, outs = engine.simulate_pa(s1, outs=1, outcome=PAOutcome.GIDP, rng=rng)
    assert outs == 3
    assert runs == 0

    # 2 outs or empty bases -> behaves as standard field out (+1 out)
    s_empty = BaseRunnerState()
    runs, outs = engine.simulate_pa(s_empty, outs=1, outcome=PAOutcome.GIDP, rng=rng)
    assert outs == 2
    assert runs == 0


def test_matchup_probabilities_and_fatigue():
    """Verify Log-5 matchup probabilities and Times Through Order (TTO) fatigue degradation."""
    batter = BatterProfile(player_id="b1", hr_rate=0.06, k_rate=0.20)
    pitcher = PitcherProfile(player_id="p1", k_rate=0.30, hr_rate=0.02)

    # 1st TTO
    p_tto1 = MLBMonteCarloEngine.compute_matchup_probabilities(batter, pitcher, times_through_order=1)
    assert pytest.approx(np.sum(p_tto1), abs=1e-5) == 1.0

    # 3rd TTO -> K% should decline, HR% should increase
    p_tto3 = MLBMonteCarloEngine.compute_matchup_probabilities(batter, pitcher, times_through_order=3)
    k_idx = list(PAOutcome).index(PAOutcome.STRIKEOUT)
    hr_idx = list(PAOutcome).index(PAOutcome.HOME_RUN)

    assert p_tto3[k_idx] < p_tto1[k_idx]
    assert p_tto3[hr_idx] > p_tto1[hr_idx]


def test_single_game_simulation():
    """Verify single game simulation execution and output contract."""
    engine = MLBMonteCarloEngine()
    home_lineup = create_sample_lineup("Dodgers", power_boost=0.02)
    away_lineup = create_sample_lineup("Padres")
    home_starter = PitcherProfile(player_id="sp_home", pitch_limit=90, k_rate=0.28)
    away_starter = PitcherProfile(player_id="sp_away", pitch_limit=90, k_rate=0.24)
    home_bp = PitcherProfile(player_id="bp_home", is_starter=False)
    away_bp = PitcherProfile(player_id="bp_away", is_starter=False)

    rng = np.random.default_rng(12345)
    sim = engine.simulate_game(
        home_lineup=home_lineup,
        away_lineup=away_lineup,
        home_starter=home_starter,
        away_starter=away_starter,
        home_bullpen=home_bp,
        away_bullpen=away_bp,
        rng=rng,
    )

    assert isinstance(sim, SingleGameSimulation)
    assert sim.home_runs >= 0
    assert sim.away_runs >= 0
    assert sim.home_runs != sim.away_runs  # Baseball games never end in a tie
    assert sim.total_innings >= 9
    assert len(sim.home_inning_runs) >= 9
    assert len(sim.away_inning_runs) >= 9
    assert sim.home_sp_innings >= 0.0
    assert sim.away_sp_innings >= 0.0
    assert sim.home_sp_strikeouts >= 0
    assert sim.away_sp_strikeouts >= 0


def test_monte_carlo_full_game_derivatives():
    """Verify full Monte Carlo simulation distributions and betting market outputs."""
    engine = MLBMonteCarloEngine()
    home_lineup = create_sample_lineup("HomeTeam", power_boost=0.01)
    away_lineup = create_sample_lineup("AwayTeam")

    home_arsenal = create_sample_pitch_arsenal(primary_velo=97.5, whiff_boost=0.06)
    away_arsenal = create_sample_pitch_arsenal(primary_velo=92.0, whiff_boost=-0.04)

    home_starter = PitcherProfile(
        player_id="sp_home_ace",
        k_rate=0.30,
        pitch_arsenal_tensor=home_arsenal.to_tensor(),
    )
    away_starter = PitcherProfile(
        player_id="sp_away_avg",
        k_rate=0.20,
        pitch_arsenal_tensor=away_arsenal.to_tensor(),
    )

    result = engine.run_monte_carlo(
        home_lineup=home_lineup,
        away_lineup=away_lineup,
        home_starter=home_starter,
        away_starter=away_starter,
        n_sims=500,
        seed=42,
    )

    assert isinstance(result, MLBMonteCarloResult)
    assert result.n_sims == 500

    # 1. Moneyline
    assert 0.0 < result.home_win_prob < 1.0
    assert pytest.approx(result.home_win_prob + result.away_win_prob) == 1.0
    # Home ace should be favored over away avg starter
    assert result.home_win_prob > result.away_win_prob

    # 2. Runline
    assert 0.0 <= result.home_minus_1_5_prob <= 1.0
    assert 0.0 <= result.away_plus_1_5_prob <= 1.0
    assert pytest.approx(result.home_minus_1_5_prob + (1.0 - result.home_minus_1_5_prob)) == 1.0

    # 3. Totals Monotonicity
    totals = result.total_distributions
    assert totals[6.5] >= totals[7.5] >= totals[8.5] >= totals[9.5] >= totals[10.5]

    # 4. F5 First 5 Innings
    assert pytest.approx(result.f5_home_win_prob + result.f5_away_win_prob + result.f5_push_prob) == 1.0

    # 5. NRFI / YRFI
    assert 0.20 < result.nrfi_prob < 0.80
    assert pytest.approx(result.nrfi_prob + result.yrfi_prob) == 1.0

    # 6. Strikeout Props
    assert result.home_sp_k_props["expected_k"] > result.away_sp_k_props["expected_k"]
    assert (
        result.home_sp_k_props["p_k_ge_3_5"]
        >= result.home_sp_k_props["p_k_ge_4_5"]
        >= result.home_sp_k_props["p_k_ge_5_5"]
        >= result.home_sp_k_props["p_k_ge_6_5"]
    )

    # 7. Integration with ScoreSimulation
    score_sim = result.to_score_simulation()
    assert isinstance(score_sim, ScoreSimulation)
    assert len(score_sim.away_scores) == 500
    assert len(score_sim.home_scores) == 500

    # Summary
    summary = result.summary()
    assert "moneyline" in summary
    assert "runline" in summary
    assert "nrfi_yrfi" in summary
    assert "first_5_innings" in summary
