from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from model_prediction.features.elo_ratings import EloBook
from model_prediction.learned_forward import _compute_features
from model_prediction.sports.nfl.moneyline import NFLCalibrator, NFLStructuralV5Engine


def test_nfl_moneyline_imports():
    assert NFLStructuralV5Engine is not None
    assert NFLCalibrator is not None


def test_neutral_site_elo_suppression():
    """International / neutral site matches must suppress domestic home advantage."""
    elo = EloBook(
        ratings={"Washington Commanders": 1500.0, "Indianapolis Colts": 1500.0},
        k=20.0,
        home_advantage=55.0,
    )
    home_trend = MagicMock(offensive_momentum=0.0, defensive_momentum=0.0)
    away_trend = MagicMock(offensive_momentum=0.0, defensive_momentum=0.0)
    artifact = MagicMock()
    artifact.raw = {
        "market_models": {
            "moneyline": {"feature_names": ["elo_probability", "trend_gap", "defensive_trend_gap"]}
        }
    }

    # Domestic home game: Washington gets +55 Elo points (~57.8% win probability)
    features_home, _ = _compute_features(
        sport="nfl",
        artifact=artifact,
        home_team="Washington Commanders",
        away_team="Indianapolis Colts",
        event_id="test-1",
        game_date="2026-10-04",
        event_start=datetime(2026, 10, 4, 13, tzinfo=UTC),
        history=[],
        elo=elo,
        home_trend=home_trend,
        away_trend=away_trend,
        data_root=Path("data"),
        observed_at=datetime(2026, 10, 4, 8, tzinfo=UTC),
        is_neutral_site=False,
    )
    assert features_home["elo_probability"] > 0.57

    # Neutral / London game: 0 Elo advantage (50.0% win probability)
    features_neutral, _ = _compute_features(
        sport="nfl",
        artifact=artifact,
        home_team="Washington Commanders",
        away_team="Indianapolis Colts",
        event_id="test-1",
        game_date="2026-10-04",
        event_start=datetime(2026, 10, 4, 13, tzinfo=UTC),
        history=[],
        elo=elo,
        home_trend=home_trend,
        away_trend=away_trend,
        data_root=Path("data"),
        observed_at=datetime(2026, 10, 4, 8, tzinfo=UTC),
        is_neutral_site=True,
    )
    assert features_neutral["elo_probability"] == 0.50
