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


def test_nfl_spread_v6_decomposed_epa_and_market_anchoring():
    """Verify NFLSpreadModelV6 uses pass/rush decomposition, pressure disparity, and market anchoring."""
    with open("config/models/nfl-spread-structural-v6.json", "r", encoding="utf-8") as f:
        artifact = json.load(f)

    model = NFLSpreadModelV6.from_artifact(artifact)
    fc = model.forecast_spread(
        home_team="Chiefs",
        away_team="Bills",
        home_pass_epa=0.25,
        home_rush_epa=-0.05,
        away_pass_epa=0.10,
        away_rush_epa=-0.02,
        home_def_pass_epa=-0.05,
        home_def_rush_epa=0.00,
        away_def_pass_epa=0.08,
        away_def_rush_epa=0.00,
        home_pressure_disparity=0.04,  # Home pass rush advantage
        away_pressure_disparity=-0.04,
        home_qb_tier_adj=2.5,
        away_qb_tier_adj=2.0,
        spread_home_line=-3.5,
    )

    assert fc.projected_home_points > fc.projected_away_points
    assert fc.projected_margin_home > 0
    assert 0.05 < fc.prob_home_win < 0.95
    assert abs(fc.prob_home_win + fc.prob_away_win - 1.0) < 1e-4

    # Bounded alternate-spread probability safeguards (never wildly overconfident)
    assert model.min_cover_prob <= fc.prob_home_cover <= model.max_cover_prob
    assert model.min_cover_prob <= fc.prob_away_cover <= model.max_cover_prob
    assert abs(fc.prob_home_cover + fc.prob_away_cover - 1.0) < 1e-4


def test_nfl_total_v6_pace_and_weather():
    """Verify NFLTotalModelV6 models pace, dome boost, and non-linear wind drag."""
    with open("config/models/nfl-total-structural-v6.json", "r", encoding="utf-8") as f:
        artifact = json.load(f)

    model = NFLTotalModelV6.from_artifact(artifact)

    fc_dome_fast = model.forecast_total(
        home_team="Lions",
        away_team="Vikings",
        home_pace=68.0,
        away_pace=66.0,
        is_dome=True,
        total_line=48.5,
    )
    fc_windy_slow = model.forecast_total(
        home_team="Bills",
        away_team="Patriots",
        home_pace=58.0,
        away_pace=58.0,
        is_dome=False,
        wind_mph=22.0,
        total_line=48.5,
    )

    # Dome + fast pace game should project significantly higher than windy + slow outdoor game
    assert fc_dome_fast.projected_total > fc_windy_slow.projected_total
    assert fc_windy_slow.weather_total_penalty >= 5.0
    assert fc_windy_slow.prob_under > fc_dome_fast.prob_under

    # Bounded alternate-total probability safeguards
    assert model.min_total_prob <= fc_dome_fast.prob_over <= model.max_total_prob
    assert model.min_total_prob <= fc_windy_slow.prob_under <= model.max_total_prob
