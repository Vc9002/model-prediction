"""Quantitative Impact & Verification Suite for Active In-Season Production Models.

Tests the quantitative before-and-after improvements across all 5 wired production champions:
1. NBA (nba-spread-margin-v2, nba-total-margin-v2): Early-season dispersion gap & thin-league isolation.
2. Soccer (soccer-poisson-dc-v2): BTTS Platt calibration & league-specific low-score correlation (rho).
3. CFB (cfb-structural-v2): Total SD bounding & January bowl game calendar year resolution.
4. NFL (nfl-spread-structural-v5, nfl-total-structural-v5): Discrete key-number mean conservation & dual-threat QB rushing EPA.
5. Tennis (tennis-surface-elo-v4): Surface cold-start Bayesian shrinkage & Grand Slam Best-of-5 scaling.
"""

from __future__ import annotations

import numpy as np
import pytest

from model_prediction.features.base import GameRecord
from model_prediction.features.cfb_features import CFBFeatureExtractor
from model_prediction.features.nfl_qb_oline import evaluate_qb_profile
from model_prediction.models.basketball import BasketballModel, UpcomingGame
from model_prediction.models.basketball_leagues import (
    EuroLeagueModel,
)
from model_prediction.models.cfb_distribution import CFBDistributionType, CFBJointDistributionEngine
from model_prediction.models.nfl_structural_v5 import (
    discrete_spread_distribution,
    discrete_total_distribution,
)
from model_prediction.models.soccer_dixon_coles_v2 import (
    SoccerDixonColesV2Model,
    platt_scale_btts,
)
from model_prediction.models.tennis import TennisModel, shrink_surface_elo
from model_prediction.tennis_forward import _infer_best_of

# ============================================================================
# 1. NBA: Early-Season Dispersion Gap & Thin-League Isolation
# ============================================================================


def test_nba_dispersion_parameter_integrity():
    """Verify that NBA margin SD and total SD strictly preserve certified artifact parameters."""
    model = BasketballModel(
        sport="nba",
        version="nba-spread-margin-v2",
        margin_sd=14.6,
        total_sd=19.2,
        league="NBA",
    )

    # Case A: Opening game of season
    game = UpcomingGame(
        event_id="nba_day1",
        event_start_utc="2026-10-22T00:00:00Z",
        away_team="BOS",
        home_team="NYK",
        spread_away_line=+4.5,
        total_line=224.5,
    )
    preds_day1 = model.predict_games([], [game])
    spread_pred_day1 = next(p for p in preds_day1 if p.market_type == "spread")
    total_pred_day1 = next(p for p in preds_day1 if p.market_type == "total")

    # Certified artifact values must be strictly preserved without parameter drift
    assert spread_pred_day1.inference_inputs["sd"] == 14.6
    assert total_pred_day1.inference_inputs["sd"] == 19.2

    # Case B: Mid-season (sample >= 15 games)
    mock_history = [
        GameRecord(
            event_id=f"g_{i}",
            event_start_utc=f"2026-11-{1 + (i % 20):02d}T00:00:00Z",
            league="NBA",
            away_team="BOS" if i % 2 == 0 else "MIA",
            home_team="NYK" if i % 2 == 0 else "LAL",
            away_score=110,
            home_score=115,
        )
        for i in range(40)
    ]
    preds_mid = model.predict_games(mock_history, [game])
    spread_pred_mid = next(p for p in preds_mid if p.market_type == "spread")
    total_pred_mid = next(p for p in preds_mid if p.market_type == "total")

    # Parameters remain stable and reproduce exactly
    assert spread_pred_mid.inference_inputs["sd"] == 14.6
    assert total_pred_mid.inference_inputs["sd"] == 19.2


def test_nba_thin_league_isolation():
    """Verify that DedicatedBasketballLeagueModel never adopts unstamped 48-minute history for 40-minute leagues."""
    euro_model = EuroLeagueModel()
    unstamped_history = [
        GameRecord(
            event_id="unstamped_1",
            event_start_utc="2026-10-15T00:00:00Z",
            league="",  # blank league tag
            away_team="Real Madrid",
            home_team="Barcelona",
            away_score=115,  # 48-min score
            home_score=118,
        )
    ]
    upcoming = [
        UpcomingGame(
            event_id="euro_1",
            event_start_utc="2026-10-25T19:00:00Z",
            away_team="Real Madrid",
            home_team="Barcelona",
            spread_away_line=+2.5,
            total_line=162.5,
        )
    ]
    # EuroLeague model must ignore unstamped games and preserve its 40-minute baseline
    preds = euro_model.predict_games(unstamped_history, upcoming)
    assert len(preds) > 0
    # In feature_basis, history_games should be 0 because unstamped history was rejected
    assert preds[0].feature_basis["history_games"] == 0


# ============================================================================
# 2. Soccer: BTTS Platt Calibration & League Correlation Profiles
# ============================================================================


def test_soccer_btts_platt_calibration():
    """Verify that uncalibrated overconfident BTTS tail sums are shifted by Platt calibration."""
    raw_uncalibrated = 0.62  # Typical raw Poisson joint tail overconfidence
    calibrated = platt_scale_btts(raw_uncalibrated)

    # Raw 0.62 should calibrate downward toward 0.58 on holdout evidence
    assert 0.50 < calibrated < raw_uncalibrated
    assert round(calibrated, 4) == pytest.approx(0.5854, abs=0.01)

    # Test full model forecast_match produces calibrated BTTS
    model = SoccerDixonColesV2Model()
    forecast = model.forecast_match("Arsenal", "Chelsea")
    # Verify BTTS yes + no sum to 1.0
    assert forecast.prob_btts_yes + forecast.prob_btts_no == pytest.approx(1.0, abs=1e-4)
    assert 0.35 <= forecast.prob_btts_yes <= 0.65


def test_soccer_league_specific_low_score_profiles():
    """Verify that low-scoring leagues resolve stronger negative rho and lower baseline mu."""
    model = SoccerDixonColesV2Model()
    arg_params = model.get_competition_params("ARG_LIGA")
    ger_params = model.get_competition_params("GER_BUNDESLIGA")

    # Argentine Primera Division: low scoring, defensive
    assert arg_params.baseline_mu == pytest.approx(0.08)
    assert arg_params.rho_low_score_dep == pytest.approx(-0.13)

    # German Bundesliga: high scoring, open
    assert ger_params.baseline_mu == pytest.approx(0.42)
    assert ger_params.rho_low_score_dep == pytest.approx(-0.03)


# ============================================================================
# 3. College Football: Total SD Bounding & January Bowl Calendar Year
# ============================================================================


def test_cfb_total_sd_bounding():
    """Verify that total_sd <= margin_sd does not blow out simulated total SD to 20+ points."""
    engine = CFBJointDistributionEngine(
        margin_sd=17.0,
        total_sd=16.0,  # total_sd <= margin_sd condition
        distribution_type=CFBDistributionType.NEGATIVE_BINOMIAL,
        n_simulations=20000,
        random_seed=42,
    )
    home_scores, away_scores = engine.simulate_joint_scores(mu_home=31.0, mu_away=21.0)
    totals = home_scores + away_scores
    simulated_total_sd = float(np.std(totals))

    # Without the clamp, simulated Total SD blew up to 20.8+
    # With the noise clamp, simulated Total SD is tightly bounded near ~17.0
    assert simulated_total_sd < 18.5
    assert simulated_total_sd >= 15.5


def test_cfb_january_bowl_calendar_year_resolution():
    """Verify that CFP/Bowl games played in January resolve to the prior fall season."""
    extractor = CFBFeatureExtractor()

    # Bowl game played on January 11, 2027 (CFP National Championship)
    # Fall regular season games were in 2026
    history = [
        {
            "event_id": "game_nov_2026",
            "event_start_utc": "2026-11-21T19:00:00Z",
            "away_team": "Georgia",
            "home_team": "Alabama",
            "away_score": 28,
            "home_score": 24,
            "season_year": 2026,
        }
    ]

    # Calling extract_features with season_year=2027 (calendar year passed by mistake)
    features = extractor.extract_features(
        history=history,
        away_team="Georgia",
        home_team="Alabama",
        event_id="natty_jan_2027",
        game_start_utc="2027-01-11T20:00:00Z",
        season_year=2027,
    )
    # The extractor should recognize Jan 2027 belongs to 2026 season and find the game
    assert features.projected_possessions > 0
    assert features.projected_total > 40.0


# ============================================================================
# 4. NFL: Key Number Mean Conservation & Dual-Threat QB Rushing EPA
# ============================================================================


def test_nfl_key_number_mean_conservation():
    """Verify that exponential tilting restores expected margin to exact target without shrinkage."""
    exp_margin = 14.0
    probs = discrete_spread_distribution(exp_margin, sd=13.8)
    realized_mean = sum(m * p for m, p in probs.items())

    # Realized mean must equal expected margin within 0.01 points (previously shrank to 12.30!)
    assert abs(realized_mean - exp_margin) < 0.01

    # Verify that key number spikes (3, 7, 10, 14) are preserved
    # Margin 14 should have a clear probability density spike
    assert probs[14] > probs[13]
    assert probs[14] > probs[15]

    # Verify total distribution mean conservation
    exp_total = 47.5
    t_probs = discrete_total_distribution(exp_total, sd=13.5)
    realized_total_mean = sum(t * p for t, p in t_probs.items())
    assert abs(realized_total_mean - exp_total) < 0.01


def test_nfl_dual_threat_qb_rushing_epa():
    """Verify that dual-threat QBs gain appropriate spread value over pure pocket passers."""
    pocket_passer = evaluate_qb_profile(
        qb_name="Pocket Passer",
        team="LAC",
        epa_per_dropback=0.15,
        cpoe=2.0,
        pressure_to_sack_pct=0.18,
        turnover_worthy_play_pct=0.025,
        sample_dropbacks=400,
        rushing_epa_per_game=0.0,
    )
    dual_threat = evaluate_qb_profile(
        qb_name="Dual Threat",
        team="BAL",
        epa_per_dropback=0.15,
        cpoe=2.0,
        pressure_to_sack_pct=0.18,
        turnover_worthy_play_pct=0.025,
        sample_dropbacks=400,
        rushing_epa_per_game=3.0,  # ~3.0 rushing EPA/game (Lamar Jackson tier)
    )

    # Dual threat should have +0.60 pts higher spread value
    spread_diff = dual_threat.spread_value_pts - pocket_passer.spread_value_pts
    assert spread_diff == pytest.approx(0.60, abs=0.05)


# ============================================================================
# 5. Tennis: Surface Cold-Start Shrinkage & Grand Slam Best-of-5 Scaling
# ============================================================================


def test_tennis_surface_cold_start_shrinkage():
    """Verify that an elite player on a new surface shrinks toward global Elo rather than collapsing."""
    overall = {"Alcaraz": 2150.0, "Qualifier": 1550.0}
    by_surface = {}  # 0 grass matches observed
    surface_counts = {}

    # Player with 0 surface matches should resolve to global Elo (2150)
    shrunk_0 = shrink_surface_elo("Alcaraz", "Grass", overall, by_surface, surface_counts)
    assert shrunk_0 == pytest.approx(2150.0, abs=1.0)

    # Player with 2 surface matches (w = 0.8 * 2 / 14 = 0.114)
    by_surface[("Alcaraz", "Grass")] = 1800.0
    surface_counts[("Alcaraz", "Grass")] = 2
    shrunk_2 = shrink_surface_elo("Alcaraz", "Grass", overall, by_surface, surface_counts)
    # Should retain ~89% of global 2150 Elo -> ~2110
    assert 2100.0 < shrunk_2 < 2150.0


def test_tennis_grand_slam_best_of_5_scaling():
    """Verify that Grand Slam ATP tournaments infer best_of=5 and scale variance by sqrt(5/3)."""
    assert _infer_best_of("Australian Open", "ATP") == 5
    assert _infer_best_of("Wimbledon", "ATP") == 5
    assert _infer_best_of("US Open", "ATP") == 5
    assert _infer_best_of("Roland Garros", "ATP") == 5

    # WTA and regular tour matches remain best_of=3
    assert _infer_best_of("Australian Open", "WTA") == 3
    assert _infer_best_of("Miami Open", "ATP") == 3

    # Test that model scales probability when best_of=5
    model = TennisModel(version="tennis-surface-elo-v4")
    # For a favorite (2000 vs 1800 Elo), Best-of-5 increases favorite's win probability
    p_bo3 = model.match_probability_v4(
        overall={"A": 2000.0, "B": 1800.0},
        by_surface={},
        surface_counts={},
        counts={"A": 20, "B": 20},
        player_one="A",
        player_two="B",
        surface="Hard",
        best_of=3,
    )
    p_bo5 = model.match_probability_v4(
        overall={"A": 2000.0, "B": 1800.0},
        by_surface={},
        surface_counts={},
        counts={"A": 20, "B": 20},
        player_one="A",
        player_two="B",
        surface="Hard",
        best_of=5,
    )
    # Favorite has higher probability in Best of 5 due to sqrt(5/3) sample size reduction in match variance
    assert p_bo5 > p_bo3
