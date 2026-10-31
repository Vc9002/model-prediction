"""Tests for NFL Structural v6 overhauled spread and total models."""

import json
from pathlib import Path

from model_prediction.models.nfl_spread_v6 import NFLSpreadModelV6
from model_prediction.models.nfl_total_v6 import NFLTotalModelV6
from model_prediction.production_registry import compute_artifact_hash


def test_nfl_v6_artifacts_hash_integrity():
    """Verify NFL v6 artifacts match exact canonical SHA-256 hashes."""
    for model_file in ("nfl-spread-structural-v6.json", "nfl-total-structural-v6.json"):
        path = Path("config/models") / model_file
        payload = json.loads(path.read_text(encoding="utf-8"))
        declared_hash = payload["artifact_hash"]
        computed_hash = compute_artifact_hash(payload)
        assert declared_hash == computed_hash, f"Hash mismatch in {model_file}"


def test_nfl_spread_v6_forecast_and_key_margins():
    """Verify NFLSpreadModelV6 produces calibrated margin and coherent moneyline."""
    with open("config/models/nfl-spread-structural-v6.json", "r", encoding="utf-8") as f:
        artifact = json.load(f)

    model = NFLSpreadModelV6.from_artifact(artifact)
    fc = model.forecast_spread(
        home_team="Chiefs",
        away_team="Bills",
        home_off_epa=0.15,
        home_def_epa=-0.05,
        away_off_epa=0.10,
        away_def_epa=0.00,
        home_qb_tier_adj=2.0,
        away_qb_tier_adj=1.5,
        spread_home_line=-3.0,
    )

    assert fc.projected_home_points > fc.projected_away_points
    assert fc.projected_margin_home > 0
    assert 0.05 < fc.prob_home_win < 0.95
    assert abs(fc.prob_home_win + fc.prob_away_win - 1.0) < 1e-4

    # Bounded alternate-spread probability safeguards (never wildly overconfident)
    assert model.min_cover_prob <= fc.prob_home_cover <= model.max_cover_prob
    assert model.min_cover_prob <= fc.prob_away_cover <= model.max_cover_prob
    assert abs(fc.prob_home_cover + fc.prob_away_cover - 1.0) < 1e-4


def test_nfl_total_v6_dome_boost_and_wind_penalty():
    """Verify NFLTotalModelV6 applies dome boost and suppresses totals in high winds."""
    with open("config/models/nfl-total-structural-v6.json", "r", encoding="utf-8") as f:
        artifact = json.load(f)

    model = NFLTotalModelV6.from_artifact(artifact)

    fc_dome = model.forecast_total(
        home_team="Lions",
        away_team="Vikings",
        is_dome=True,
        total_line=48.5,
    )
    fc_windy = model.forecast_total(
        home_team="Bills",
        away_team="Patriots",
        is_dome=False,
        wind_mph=22.0,
        total_line=48.5,
    )

    # Dome game should have higher projected total than outdoor high-wind game
    assert fc_dome.projected_total > fc_windy.projected_total
    assert fc_windy.weather_total_penalty >= 4.0
    assert fc_windy.prob_under > fc_dome.prob_under

    # Bounded alternate-total probability safeguards
    assert model.min_total_prob <= fc_dome.prob_over <= model.max_total_prob
    assert model.min_total_prob <= fc_windy.prob_under <= model.max_total_prob
