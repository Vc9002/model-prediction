"""Direct integration and unit tests for baseline and availability features.

Exercises:
1. Direct calls to _compute_features() using fixture game records with known history and outcomes.
2. Verified point-in-time calculation of log5_probability and pythagorean_probability.
3. Accurate trailing_home_games_30d aggregation from history.
4. Point-in-time availability flags:
   - bullpen_available & bullpen_fatigue_available (real observed relief lines)
   - offense_pit_available (batter PIT priors availability)
   - probable_starter_available (starter status verification)
   - park_available (point-in-time park factor availability)
   - weather_available (Open-Meteo live weather / dome availability)
5. Removal of outcome_3way from the serving feature space (target label only).
6. Unit tests for baselines (log5, pythagorean, accumulator, batter PIT).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction.features.baselines import (
    PYTHAGOREAN_EXPONENT_MLB,
    TeamRecordAccumulator,
    log5_matchup_probability,
    pythagorean_win_rate,
)
from model_prediction.features.batter_offense import matchup_offense_pit_gap
from model_prediction.learned_forward import _compute_features
from model_prediction.validation import FEATURE_VARIANTS, ValidationRow


@dataclass
class FixtureGame:
    event_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    start: datetime
    date: str = ""

    def __post_init__(self) -> None:
        if not self.date:
            self.date = self.start.strftime("%Y-%m-%d")


class MockElo:
    def expected_home_win(self, home: str, away: str) -> float:
        return 0.55

    def expected_neutral_win(self, home: str, away: str) -> float:
        return 0.50


class MockTrend:
    offensive_momentum: float = 0.0
    defensive_momentum: float = 0.0
    consistency: float = 0.0
    hot_cold_score: float = 0.0


def _build_fixture_history(ref_date: datetime) -> list[FixtureGame]:
    """Build realistic fixture history: 20 completed games for NYY and BOS."""
    games = []
    # NYY: 14 wins, 6 losses, 110 runs scored, 70 runs allowed
    # BOS: 8 wins, 12 losses, 80 runs scored, 95 runs allowed
    for i in range(20):
        game_start = ref_date - timedelta(days=25 - i)
        # Alternate home/away
        if i % 2 == 0:
            h_team, a_team = "NYY", "BOS"
            h_score = 6 if i < 14 else 2
            a_score = 3 if i < 14 else 5
        else:
            h_team, a_team = "BOS", "NYY"
            h_score = 4 if i >= 14 else 1
            a_score = 5 if i >= 14 else 7
        games.append(
            FixtureGame(
                event_id=f"hist_game_{i}",
                home_team=h_team,
                away_team=a_team,
                home_score=h_score,
                away_score=a_score,
                start=game_start,
            )
        )
    return games


class TestLog5AndPythagoreanUnits:
    def test_log5_equal_teams(self) -> None:
        """Two 50% win rate teams should have 50% matchup probability."""
        prob = log5_matchup_probability(0.50, 0.50)
        assert pytest.approx(prob, abs=1e-4) == 0.50

    def test_log5_superior_team(self) -> None:
        """A .600 team vs a .400 team should yield > 69% win probability."""
        prob = log5_matchup_probability(0.60, 0.40)
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

        p = pythagorean_win_rate(800, 600, exponent=PYTHAGOREAN_EXPONENT_MLB)
        assert p > 0.60

    def test_team_record_accumulator(self) -> None:
        """Accumulator correctly computes Bayes smoothed win rate and pythagorean rate."""
        rec = TeamRecordAccumulator(wins=60, losses=40, runs_scored=500, runs_allowed=400)
        assert rec.total_games == 100
        wr = rec.win_rate(prior_games=20, prior_win_rate=0.50)
        assert pytest.approx(wr, abs=1e-3) == (60 + 10) / 120
        pyth = rec.pythagorean_expectation(prior_runs=100)
        assert 0.50 < pyth < 1.0

    def test_offense_pit_gap_structure(self) -> None:
        """Test matchup_offense_pit_gap returns a tuple of (gap_float, available_bool)."""
        now = datetime.now(UTC)
        gap, available = matchup_offense_pit_gap("NYY", "BOS", now)
        assert isinstance(gap, float)
        assert isinstance(available, bool)
        assert -1.0 <= gap <= 1.0


class TestDirectComputeFeatures:
    """Direct tests calling _compute_features() with known fixture game history."""

    def test_compute_features_baseline_probabilities_and_trailing_games(self) -> None:
        """Verify _compute_features correctly calculates log5, pythagorean, and trailing home games."""
        game_date_dt = datetime(2026, 7, 20, 19, 0, tzinfo=UTC)
        history = _build_fixture_history(game_date_dt)

        artifact = SimpleNamespace(
            raw={
                "market_models": {
                    "moneyline": {
                        "feature_names": [
                            "log5_probability",
                            "pythagorean_probability",
                            "trailing_home_games_30d",
                        ]
                    }
                }
            }
        )

        features, unavailable = _compute_features(
            sport="mlb",
            artifact=artifact,  # type: ignore[arg-type]
            home_team="NYY",
            away_team="BOS",
            event_id="test_game_target",
            game_date="2026-07-20",
            event_start=game_date_dt,
            history=history,
            elo=MockElo(),
            home_trend=MockTrend(),
            away_trend=MockTrend(),
            data_root=Path("data"),
            observed_at=game_date_dt - timedelta(hours=2),
        )

        assert "log5_probability" in features
        assert "pythagorean_probability" in features
        assert "trailing_home_games_30d" in features

        # NYY was superior in the fixture: log5 and pythagorean should reflect > 0.55
        assert features["log5_probability"] > 0.55
        assert features["pythagorean_probability"] > 0.55
        # 10 NYY home games in the 20-game history (all within last 25 days)
        assert features["trailing_home_games_30d"] == 10.0
        assert unavailable == ()

    def test_outcome_3way_purged_from_serving(self) -> None:
        """outcome_3way is a ground-truth label and must NEVER be emitted as a serving feature."""
        game_date_dt = datetime(2026, 7, 20, 19, 0, tzinfo=UTC)
        artifact = SimpleNamespace(
            raw={"market_models": {"moneyline": {"feature_names": ["outcome_3way", "elo_probability"]}}}
        )

        features, _ = _compute_features(
            sport="soccer",
            artifact=artifact,  # type: ignore[arg-type]
            home_team="Arsenal",
            away_team="Chelsea",
            event_id="soccer_test_1",
            game_date="2026-07-20",
            event_start=game_date_dt,
            history=[],
            elo=MockElo(),
            home_trend=MockTrend(),
            away_trend=MockTrend(),
            data_root=Path("data"),
            observed_at=game_date_dt - timedelta(hours=2),
        )

        assert "outcome_3way" not in features
        assert "elo_probability" in features

    def test_availability_flags_reflect_live_source_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Availability flags in _compute_features must reflect real point-in-time observations."""
        game_date_dt = datetime(2026, 7, 20, 19, 0, tzinfo=UTC)
        history = _build_fixture_history(game_date_dt)

        artifact = SimpleNamespace(
            raw={
                "market_models": {
                    "moneyline": {
                        "feature_names": [
                            "park_available",
                            "weather_available",
                            "bullpen_available",
                            "bullpen_fatigue_available",
                            "offense_pit_available",
                            "probable_starter_available",
                            "offense_pit_gap",
                        ]
                    }
                }
            }
        )

        # 1. Test when sources are available
        monkeypatch.setattr(
            "model_prediction.learned_forward.team_recent_relief_lines",
            lambda team, decision, **kwargs: [{"innings": 3.0, "earned_runs": 1.0}],
        )
        monkeypatch.setattr(
            "model_prediction.learned_forward.matchup_offense_pit_gap",
            lambda home, away, dt: (0.15, True),
        )
        monkeypatch.setattr(
            "model_prediction.learned_forward.starter_era_gap_live",
            lambda home, away, dt: -0.5,
        )
        monkeypatch.setattr(
            "model_prediction.learned_forward.park_factor_at",
            lambda home, dt, **kw: {"park_factor": 1.05, "status": "available"},
        )
        monkeypatch.setattr(
            "model_prediction.features.weather.live_weather",
            lambda home, dt_iso: {"status": "available", "weather_run_factor": 1.02},
        )

        features_avail, _ = _compute_features(
            sport="mlb",
            artifact=artifact,  # type: ignore[arg-type]
            home_team="NYY",
            away_team="BOS",
            event_id="test_avail_1",
            game_date="2026-07-20",
            event_start=game_date_dt,
            history=history,
            elo=MockElo(),
            home_trend=MockTrend(),
            away_trend=MockTrend(),
            data_root=Path("data"),
            observed_at=game_date_dt - timedelta(hours=2),
            home_starter_name="Gerrit Cole",
            away_starter_name="Brayan Bello",
        )

        assert features_avail["park_available"] == 1.0
        assert features_avail["weather_available"] == 1.0
        assert features_avail["bullpen_available"] == 1.0
        assert features_avail["bullpen_fatigue_available"] == 1.0
        assert features_avail["offense_pit_available"] == 1.0
        assert features_avail["probable_starter_available"] == 1.0
        assert features_avail["offense_pit_gap"] == 0.15

        # 2. Test when sources are unavailable (fail-closed)
        monkeypatch.setattr(
            "model_prediction.learned_forward.team_recent_relief_lines",
            lambda team, decision, **kwargs: [],  # No relief history
        )
        monkeypatch.setattr(
            "model_prediction.learned_forward.matchup_offense_pit_gap",
            lambda home, away, dt: (0.0, False),
        )
        monkeypatch.setattr(
            "model_prediction.learned_forward.park_factor_at",
            lambda home, dt, **kw: {"park_factor": 1.0, "status": "unavailable_from_source"},
        )
        monkeypatch.setattr(
            "model_prediction.features.weather.live_weather",
            lambda home, dt_iso: {"status": "api_error", "weather_run_factor": 1.0},
        )

        features_unavail, _ = _compute_features(
            sport="mlb",
            artifact=artifact,  # type: ignore[arg-type]
            home_team="NYY",
            away_team="BOS",
            event_id="test_unavail_1",
            game_date="2026-07-20",
            event_start=game_date_dt,
            history=history,
            elo=MockElo(),
            home_trend=MockTrend(),
            away_trend=MockTrend(),
            data_root=Path("data"),
            observed_at=game_date_dt - timedelta(hours=2),
            home_starter_name=None,  # No starter confirmed
            away_starter_name=None,
        )

        assert features_unavail["park_available"] == 0.0
        assert features_unavail["weather_available"] == 0.0
        assert features_unavail["bullpen_available"] == 0.0
        assert features_unavail["bullpen_fatigue_available"] == 0.0
        assert features_unavail["offense_pit_available"] == 0.0
        assert features_unavail["probable_starter_available"] == 0.0
        assert features_unavail["offense_pit_gap"] == 0.0


class TestValidationRowCoverage:
    def test_validation_row_holds_all_walk_forward_fields(self) -> None:
        """ValidationRow properly stores all unserved availability, baseline features, and target."""
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

    def test_feature_variants_contain_log5_and_pythagorean(self) -> None:
        """Variants registered in FEATURE_VARIANTS include pythagorean and log5."""
        assert "log5_only" in FEATURE_VARIANTS
        assert FEATURE_VARIANTS["log5_only"] == ("log5_probability",)
        assert "pythagorean_only" in FEATURE_VARIANTS
        assert FEATURE_VARIANTS["pythagorean_only"] == ("pythagorean_probability",)
        assert "elo_pythagorean" in FEATURE_VARIANTS
        assert "elo_log5" in FEATURE_VARIANTS
