"""Targeted unit tests for NCAAB v6 spread, total, and conference-tiered models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v6 import (
    NCAABSpreadModelV6,
    _load_team_priors,
    _student_t_cdf,
)
from model_prediction.models.ncaab_total_v6 import (
    NCAABTotalModelV6,
)


def test_conference_tier_priors_load():
    priors = _load_team_priors()
    assert len(priors) >= 300
    # Power 5 teams should have high baseline priors
    assert priors.get("Houston Cougars", 0) >= 1650
    assert priors.get("Duke Blue Devils", 0) >= 1650
    assert priors.get("Kansas Jayhawks", 0) >= 1650
    # Bottom tier D1 teams should have lower baseline priors
    assert priors.get("Grambling Tigers", 1500) <= 1300


def test_student_t_cdf_has_heavier_tails_than_normal():
    # Far in the tail (3.0 SD away), Student-t (df=8) should have more tail mass than Gaussian
    t_val = _student_t_cdf(3.0, mean=0.0, sd=1.0, df=8.0)
    # Under standard normal, CDF at +3.0 is 0.99865 (tail = 0.00135)
    # Under t(8), CDF at +3.0 is 0.9915 (tail = 0.0085, >6x heavier)
    assert t_val < 0.995


def test_ncaab_spread_v6_altitude_bonus():
    now = datetime.now(UTC)
    games = [
        GameRecord(
            str(i),
            (now - timedelta(days=i + 1)).isoformat(),
            "NCAAB",
            "Air Force Falcons",
            "Wyoming Cowboys",
            65,
            75,
        )
        for i in range(10)
    ]
    up_altitude = UpcomingGame(
        "g1", now.isoformat(), "San Diego State Aztecs", "Wyoming Cowboys", spread_away_line=4.5
    )
    up_neutral = UpcomingGame(
        "g2",
        now.isoformat(),
        "San Diego State Aztecs",
        "Wyoming Cowboys",
        spread_away_line=4.5,
        neutral_site=True,
    )

    model = NCAABSpreadModelV6()
    pred_alt = model.predict_spreads(games, [up_altitude])
    pred_neu = model.predict_spreads(games, [up_neutral])

    # Wyoming at home in Laramie gets altitude bonus + HCA
    assert pred_alt[0].feature_basis["altitude_bonus"] == 1.8
    assert pred_alt[0].feature_basis["effective_hca"] == 7.2 + 1.8
    # On neutral court, altitude bonus and HCA are zeroed
    assert pred_neu[0].feature_basis["altitude_bonus"] == 0.0
    assert pred_neu[0].feature_basis["effective_hca"] == 0.0


def test_ncaab_total_v6_venue_tempo_control():
    now = datetime.now(UTC)
    games = [
        GameRecord(str(i), (now - timedelta(days=i + 1)).isoformat(), "NCAAB", "Away", "Home", 70, 75)
        for i in range(10)
    ]
    up_home = UpcomingGame("g1", now.isoformat(), "FastRoad", "SlowHome", total_line=145.0)
    up_neu = UpcomingGame("g2", now.isoformat(), "FastRoad", "SlowHome", total_line=145.0, neutral_site=True)

    model = NCAABTotalModelV6(home_tempo_control=0.60)
    preds_home = model.predict_totals(games, [up_home])
    preds_neu = model.predict_totals(games, [up_neu])

    assert len(preds_home) == 1
    assert len(preds_neu) == 1
    assert "expected_pace" in preds_home[0].feature_basis
    assert model.compute_dynamic_units(0.05) > 1.0


def test_ncaab_total_v6_bayesian_credibility_shrinkage():
    now = datetime.now(UTC)
    up = UpcomingGame("g1", now.isoformat(), "Away", "Home", total_line=140.0)

    # 1. Zero sample: expected_total must shrink 100% to line (z=0.0)
    model = NCAABTotalModelV6(credibility_constant=4.0)
    preds_zero = model.predict_totals([], [up])
    assert abs(preds_zero[0].inference_inputs["mean"] - 140.0) < 1e-4
    assert preds_zero[0].feature_basis["credibility_factor"] == 0.0
    # Probabilities must be exactly 50/50
    assert abs(preds_zero[0].probabilities["over"] - 0.50) < 0.01

    # 2. Sample = 12 games: z = 12 / (12 + 4) = 0.75 (model dominates)
    history_12 = [
        GameRecord(str(i), (now - timedelta(days=i + 1)).isoformat(), "NCAAB", "Away", "Home", 80, 85)
        for i in range(12)
    ]
    preds_mature = model.predict_totals(history_12, [up])
    z = preds_mature[0].feature_basis["credibility_factor"]
    assert 0.70 <= z <= 0.80
    # Expected total moves toward high-scoring history (>150), not pinned to 140
    assert preds_mature[0].inference_inputs["mean"] > 145.0


def test_ncaab_total_v6_conference_scoring_baselines():
    now = datetime.now(UTC)
    # Matchup between two SEC teams vs two NEC teams
    up_sec = UpcomingGame(
        "g_sec", now.isoformat(), "Alabama Crimson Tide", "Arkansas Razorbacks", total_line=None
    )
    up_nec = UpcomingGame("g_nec", now.isoformat(), "Wagner Seahawks", "LIU Sharks", total_line=None)

    model = NCAABTotalModelV6()
    pred_sec = model.predict_totals([], [up_sec])
    pred_nec = model.predict_totals([], [up_nec])

    # SEC expected total should be significantly higher than NEC expected total
    sec_total = pred_sec[0].inference_inputs["mean"]
    nec_total = pred_nec[0].inference_inputs["mean"]
    assert sec_total > nec_total + 15.0
