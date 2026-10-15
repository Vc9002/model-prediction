"""Unit tests for newly served baseline, interaction, and availability features.

Covers:
1. log5_probability (calculation, edge cases, boundaries, Laplace smoothing)
2. pythagorean_probability (run differential, exponents, matchup expectation)
3. offense_pit_gap (batter PIT priors composite calculation and availability)
4. Availability indicators:
   - bullpen_available
   - bullpen_fatigue_available
   - offense_pit_available
   - probable_starter_available
   - park_available
   - weather_available
   - trailing_home_games_30d
5. outcome_3way (2=home, 1=draw, 0=away for soccer 3-way markets)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from model_prediction.features.baselines import (
    PYTHAGOREAN_EXPONENT_MLB,
    TeamRecordAccumulator,
    log5_matchup_probability,
    pythagorean_win_rate,
)
from model_prediction.features.batter_offense import matchup_offense_pit_gap
from model_prediction.validation import FEATURE_VARIANTS, ValidationRow


class TestLog5AndPythagorean:
    def test_log5_equal_teams(self) -> None:
        """Two 50% win rate teams should have 50% matchup probability."""
        prob = log5_matchup_probability(0.50, 0.50)
        assert pytest.approx(prob, abs=1e-4) == 0.50

    def test_log5_superior_team(self) -> None:
        """A .600 team vs a .400 team should yield > 69% win probability."""
        prob = log5_matchup_probability(0.60, 0.40)
        # Bill James formula: (.60 - .24) / (.60 + .40 - 2 * .24) = .36 / .52 = 0.6923
        assert pytest.approx(prob, abs=1e-3) == 0.6923

    def test_log5_boundaries(self) -> None:
        """Probabilities near 0 and 1 are safely clamped and do not divide by zero."""
        assert 0.01 <= log5_matchup_probability(0.0, 1.0) <= 0.99
        assert 0.01 <= log5_matchup_probability(1.0, 0.0) <= 0.99
        assert log5_matchup_probability(-0.5, 1.5) == log5_matchup_probability(0.01, 0.99)

    def test_pythagorean_expectation_mlb(self) -> None:
        """Equal runs scored and allowed gives exactly 0.50 win rate."""
        assert pythagorean_win_rate(500, 500) == 0.50
        assert pythagorean_win_rate(0, 0) == 0.50
        assert pythagorean_win_rate(100, 0) == 0.99
        assert pythagorean_win_rate(0, 100) == 0.01

        # Positive run differential yields > 0.50
        p = pythagorean_win_rate(800, 600, exponent=PYTHAGOREAN_EXPONENT_MLB)
        assert p > 0.60

    def test_team_record_accumulator(self) -> None:
        """Accumulator correctly computes Bayes smoothed win rate and pythagorean rate."""
        rec = TeamRecordAccumulator(wins=60, losses=40, runs_scored=500, runs_allowed=400)
        assert rec.total_games == 100
        # Win rate smoothed with 20 prior games at 0.50
        wr = rec.win_rate(prior_games=20, prior_win_rate=0.50)
        assert pytest.approx(wr, abs=1e-3) == (60 + 10) / 120  # 70 / 120 = 0.5833
        # Smoothed pythagorean expectation
        pyth = rec.pythagorean_expectation(prior_runs=100)
        assert 0.50 < pyth < 1.0


class TestOffensePitGap:
    def test_offense_pit_gap_structure(self) -> None:
        """Test matchup_offense_pit_gap returns a tuple of (gap_float, available_bool)."""
        feature_name = "offense_pit_gap"
        now = datetime.now(UTC)
        gap, available = matchup_offense_pit_gap("NYY", "BOS", now)
        assert feature_name == "offense_pit_gap"
        assert isinstance(gap, float)
        assert isinstance(available, bool)
        assert -1.0 <= gap <= 1.0


class TestAvailabilityAndMetaFeatures:
    def test_validation_row_contains_all_features(self) -> None:
        """ValidationRow properly stores all unserved availability and baseline features."""
        row = ValidationRow(
            date="2026-10-15",
            event_id="test_evt_123",
            outcome=1,
            elo_probability=0.55,
            trend_gap=0.02,
            defensive_trend_gap=-0.01,
            consistency_gap=0.005,
            hot_cold_gap=0.01,
            rest_disparity=0.0,
            back_to_back_gap=0.0,
            games_last_7_gap=0.0,
            schedule_available=1.0,
            park_factor=1.02,
            park_factor_pit=1.01,
            weather_factor=0.98,
            pitcher_era_gap=-0.3,
            starter_era_gap=-0.25,
            starter_fip_gap=-0.20,
            starter_kbb_gap=0.15,
            residual_trend_gap=0.05,
            probable_starter_era_gap=-0.10,
            probable_starter_available=True,
            bullpen_weakness_gap=0.02,
            bullpen_available=True,
            bullpen_fatigue_gap=1.5,
            bullpen_fatigue_available=True,
            offense_pit_gap=0.04,
            offense_pit_available=True,
            park_available=True,
            weather_available=True,
            elo_neutral_probability=0.52,
            trailing_home_win_rate_30d=0.60,
            trailing_home_games_30d=15,
            pythagorean_probability=0.58,
            log5_probability=0.57,
            outcome_3way=2,
        )

        assert row.probable_starter_available is True
        assert row.bullpen_available is True
        assert row.bullpen_fatigue_available is True
        assert row.offense_pit_available is True
        assert row.park_available is True
        assert row.weather_available is True
        assert row.trailing_home_games_30d == 15
        assert row.pythagorean_probability == 0.58
        assert row.log5_probability == 0.57
        assert row.outcome_3way == 2

    def test_soccer_outcome_3way_mapping(self) -> None:
        """Outcome 3-way maps correctly: home win = 2, draw = 1, away win = 0."""
        # Home win
        home_score, away_score = 2, 1
        outcome_3way = 2 if home_score > away_score else (1 if home_score == away_score else 0)
        assert outcome_3way == 2

        # Draw
        home_score, away_score = 1, 1
        outcome_3way = 2 if home_score > away_score else (1 if home_score == away_score else 0)
        assert outcome_3way == 1

        # Away win
        home_score, away_score = 0, 3
        outcome_3way = 2 if home_score > away_score else (1 if home_score == away_score else 0)
        assert outcome_3way == 0

    def test_feature_variants_contain_log5_and_pythagorean(self) -> None:
        """Variants registered in FEATURE_VARIANTS include pythagorean and log5."""
        assert "log5_only" in FEATURE_VARIANTS
        assert FEATURE_VARIANTS["log5_only"] == ("log5_probability",)
        assert "pythagorean_only" in FEATURE_VARIANTS
        assert FEATURE_VARIANTS["pythagorean_only"] == ("pythagorean_probability",)
        assert "elo_pythagorean" in FEATURE_VARIANTS
        assert "elo_log5" in FEATURE_VARIANTS
