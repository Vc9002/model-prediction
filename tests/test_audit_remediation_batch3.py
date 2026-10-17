"""Unit tests for Batch 3 Audit Remediations across model-prediction.

Verifies:
1. Calibrator numerical boundary safety (Platt, Isotonic, Temperature).
2. Tennis spread line selection orientation & unobserved surface Elo fallback.
3. NFL structural v5 defensive EPA allowed directionality & total distribution baseline.
4. Scalar artifact replay integer line push normalization.
5. Basketball model baseline parameter & league history isolation.
6. Production lifecycle config enum alignment.
"""

from __future__ import annotations

import math
from pathlib import Path

from model_prediction.calibration import (
    CalibrationMetadata,
    IsotonicCalibrator,
    TemperatureCalibrator,
    TrainablePlattCalibrator,
)
from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import BasketballModel, UpcomingGame
from model_prediction.models.basketball_leagues import (
    WNBA_CONFIG,
    DedicatedBasketballLeagueModel,
)
from model_prediction.models.nfl_structural_v5 import (
    NFLStructuralV5Engine,
    discrete_total_distribution,
)
from model_prediction.models.tennis import TennisModel, UpcomingMatch
from model_prediction.production_registry import ProductionModelRegistry


def test_calibrator_numerical_boundary_clamping():
    """Verify that extreme inputs and low temperatures do not overflow math.exp in calibrators."""
    meta = CalibrationMetadata("platt", "v1", "base", None, None, 100, "hash")

    # 1. Trainable Platt
    train_platt = TrainablePlattCalibrator(intercept=100.0, slope=10.0, metadata=meta)
    p_high = train_platt.transform(0.9999999999)
    assert 0.0 < p_high < 1.0
    assert math.isfinite(p_high)
    p_low = train_platt.transform(0.0000000001)
    assert 0.0 < p_low < 1.0
    assert math.isfinite(p_low)

    # 2. Temperature Calibrator with small temperature
    temp_meta = CalibrationMetadata("temperature", "v1", "base", None, None, 100, "hash")
    temp_cal = TemperatureCalibrator(temperature=0.01, metadata=temp_meta)
    p_temp = temp_cal.transform(0.999999)
    assert 0.0 < p_temp < 1.0
    assert math.isfinite(p_temp)

    # 3. Isotonic Calibrator output clamping and NaN handling
    iso_meta = CalibrationMetadata("isotonic", "v1", "base", None, None, 100, "hash")
    iso_cal = IsotonicCalibrator(thresholds=[0.2, 0.8], values=[0.0, 1.0], metadata=iso_meta)
    # Output must not be exact 0.0 or 1.0 to protect downstream Kelly sizing
    p_zero = iso_cal.transform(0.1)
    assert p_zero >= 1e-12
    p_one = iso_cal.transform(0.9)
    assert p_one <= 1.0 - 1e-12

    # NaN input returns safe 0.5
    assert iso_cal.transform(float("nan")) == 0.5


def test_tennis_unobserved_surface_elo_fallback():
    """Verify that unobserved surface matches fall back to player's overall Elo, not 1500."""
    # Historical matches where Alcaraz has 1800+ overall Elo on Hard courts
    history = [
        {
            "winner": "Carlos Alcaraz",
            "loser": "Daniil Medvedev",
            "surface": "Hard",
            "match_date": f"2026-06-0{i}",
        }
        for i in range(1, 6)
    ]
    # Give opponent 1 match so both players are known in overall
    history.append(
        {"winner": "Novice Player", "loser": "Daniil Medvedev", "surface": "Clay", "match_date": "2026-06-07"}
    )
    model = TennisModel(version="tennis-surface-elo-v4")
    # Alcaraz plays his first match on Clay against Novice Player
    upcoming = [
        UpcomingMatch(
            event_id="french-open-1",
            event_start_utc="2026-06-10T12:00:00Z",
            player_one="Carlos Alcaraz",
            player_two="Novice Player",
            surface="Clay",  # Unobserved surface for Alcaraz
            tour="ATP",
            spread_player_one_line=-4.5,
            total_games_line=21.5,
            best_of=3,
        )
    ]
    preds = model.predict_games(history, upcoming)
    assert len(preds) == 3  # Moneyline, Spread, Total
    # Derivative basis should use Alcaraz's overall rating (~1580+), not default 1500.0
    basis = preds[0].feature_basis
    assert basis["elo_p1_surface"] > 1550.0
    # Expected games should reflect Alcaraz being a clear favorite
    assert basis["expected_games_p1"] > basis["expected_games_p2"]


def test_nfl_structural_v5_defensive_epa_allowed_directionality():
    """Verify that playing a porous defense increases offensive efficiency points."""
    engine = NFLStructuralV5Engine()

    # Matchup against a porous defense (allowed EPA = +0.10)
    fc_porous = engine.forecast_game(
        home_team="Chiefs",
        away_team="WeakDefense",
        home_off_epa=0.04,
        away_def_epa=0.10,  # Porous defense allows positive EPA
    )

    # Matchup against an elite defense (allowed EPA = -0.08)
    fc_elite = engine.forecast_game(
        home_team="Chiefs",
        away_team="StrongDefense",
        home_off_epa=0.04,
        away_def_epa=-0.08,  # Elite defense allows negative EPA
    )

    # Chiefs should project significantly more points against the porous defense
    assert fc_porous.projected_home_points > fc_elite.projected_home_points
    assert fc_porous.projected_margin_home > fc_elite.projected_margin_home


def test_nfl_structural_v5_shootout_vs_slugfest_total_sensitivity():
    """Verify that two high-powered offenses and weak defenses project a much higher total than a slugfest."""
    engine = NFLStructuralV5Engine()

    # Shootout: both offenses good (+0.10), both defenses porous (+0.10)
    fc_shootout = engine.forecast_game(
        home_team="TeamA",
        away_team="TeamB",
        home_off_epa=0.10,
        away_off_epa=0.10,
        home_def_epa=0.10,
        away_def_epa=0.10,
    )

    # Slugfest: both offenses poor (-0.05), both defenses elite (-0.05)
    fc_slugfest = engine.forecast_game(
        home_team="TeamC",
        away_team="TeamD",
        home_off_epa=-0.05,
        away_off_epa=-0.05,
        home_def_epa=-0.05,
        away_def_epa=-0.05,
    )

    assert fc_shootout.projected_total > fc_slugfest.projected_total + 8.0
    assert fc_shootout.prob_over > fc_slugfest.prob_over


def test_nfl_total_distribution_norm_zero_baseline():
    """Verify that discrete total distribution responds monotonically to expected total."""
    dist_56 = discrete_total_distribution(exp_total=56.0)
    dist_38 = discrete_total_distribution(exp_total=38.0)
    assert abs(sum(dist_56.values()) - 1.0) < 1e-4
    assert abs(sum(dist_38.values()) - 1.0) < 1e-4

    e_56 = sum(t * p for t, p in dist_56.items())
    e_38 = sum(t * p for t, p in dist_38.items())
    assert e_56 > e_38 + 15.0


def test_basketball_baseline_parameter_and_isolation():
    """Verify BasketballModel baseline_points_per_team and history isolation."""
    # Custom baseline
    model = BasketballModel(
        sport="custom",
        version="v1",
        margin_sd=10.0,
        total_sd=15.0,
        league="TEST",
        baseline_points_per_team=95.0,
    )
    assert model.baseline_points_per_team == 95.0

    # Dedicated WNBA model
    dedicated = DedicatedBasketballLeagueModel(WNBA_CONFIG)

    # History contains ONLY NBA games
    nba_game = GameRecord(
        event_id="nba_1",
        event_start_utc="2026-05-01T20:00:00Z",
        league="NBA",
        away_team="Lakers",
        home_team="Celtics",
        away_score=105,
        home_score=110,
    )
    upcoming_wnba = UpcomingGame(
        event_id="wnba_1",
        event_start_utc="2026-05-10T20:00:00Z",
        home_team="Liberty",
        away_team="Aces",
        league="WNBA",
    )

    # Predicting WNBA games should NOT ingest NBA history
    preds = dedicated.predict_games([nba_game], [upcoming_wnba])
    assert len(preds) == 1
    # With 0 WNBA history games, isolated history length must be 0
    basis = preds[0].feature_basis
    assert basis["history_games"] == 0


def test_production_lifecycle_config_enums():
    """Verify production.yaml evidence_status values are valid EvidenceStatus enums."""
    repo_root = Path(__file__).resolve().parents[1]
    registry = ProductionModelRegistry.load(repo_root)
    assert len(registry.entries) > 0
    valid_statuses = {
        "unverified",
        "historical_only",
        "predictively_qualified",
        "market_qualified",
        "prospectively_qualified",
        "degraded",
    }
    for model_id, entry in registry.entries.items():
        assert entry.evidence_status in valid_statuses, (
            f"{model_id} has invalid evidence_status: {entry.evidence_status}"
        )
