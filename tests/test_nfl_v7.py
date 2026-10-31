"""Targeted tests for overhauled NFL V7 models (nfl-spread-structural-v7 and nfl-total-structural-v7)."""

from __future__ import annotations

import json
from pathlib import Path

from model_prediction.features.nfl_efficiency import NFLEfficiencyStore
from model_prediction.models.nfl_spread_v7 import NFLSpreadModelV7
from model_prediction.models.nfl_total_v7 import NFLTotalModelV7
from model_prediction.production_canary import _compute_artifact_hash

REPO = Path(__file__).resolve().parents[1]


def test_nfl_spread_v7_contract_and_hash() -> None:
    path = REPO / "config" / "models" / "nfl-spread-structural-v7.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))

    expected_hash = artifact["artifact_hash"]
    computed_hash = _compute_artifact_hash(artifact)
    assert computed_hash == expected_hash, f"Hash mismatch: {computed_hash} != {expected_hash}"

    model = NFLSpreadModelV7.from_artifact(artifact)
    assert model.version == "nfl-spread-structural-v7"
    assert model.home_field_advantage == 1.35
    assert model.market_weight == 0.80

    # Symmetric neutral matchup
    fc = model.forecast_spread(
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        home_pass_epa=0.15,
        home_rush_epa=-0.02,
        away_pass_epa=0.10,
        away_rush_epa=-0.05,
        home_pressure_disparity=0.03,
        away_pressure_disparity=-0.03,
        spread_home_line=-2.5,
    )

    assert 0.35 <= fc.prob_home_cover <= 0.65
    assert 0.05 <= fc.prob_home_win <= 0.95
    assert round(fc.prob_home_win + fc.prob_away_win, 5) == 1.00000
    assert round(fc.prob_home_cover + fc.prob_away_cover, 5) == 1.00000
    assert fc.projected_margin_home > 0.0  # KC favored

    # Verify Key-Number Asymmetric Half-point pricing:
    # At line -2.5, KC covering on 3.0 margin gives extra key probability boost
    fc_below_key = model.forecast_spread(
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        spread_home_line=-2.5,
    )
    # At line -3.5, KC must win by 4, so 3.0 margin fails to cover
    fc_above_key = model.forecast_spread(
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        spread_home_line=-3.5,
    )
    assert fc_below_key.prob_home_cover > fc_above_key.prob_home_cover


def test_nfl_total_v7_contract_and_hash() -> None:
    path = REPO / "config" / "models" / "nfl-total-structural-v7.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))

    expected_hash = artifact["artifact_hash"]
    computed_hash = _compute_artifact_hash(artifact)
    assert computed_hash == expected_hash, f"Hash mismatch: {computed_hash} != {expected_hash}"

    model = NFLTotalModelV7.from_artifact(artifact)
    assert model.version == "nfl-total-structural-v7"
    assert model.league_avg_total == 45.5
    assert model.market_weight == 0.60

    # Multiplicative pace interaction: Fast pace game has higher scoring projection
    fc_fast = model.forecast_total(
        home_team="Miami Dolphins",
        away_team="Detroit Lions",
        home_pace=68.0,
        away_pace=68.0,
        total_pass_epa=0.25,
        total_rush_epa=0.10,
        total_line=48.5,
    )
    fc_slow = model.forecast_total(
        home_team="Pittsburgh Steelers",
        away_team="Tennessee Titans",
        home_pace=58.0,
        away_pace=58.0,
        total_pass_epa=-0.15,
        total_rush_epa=-0.10,
        total_line=48.5,
    )
    assert fc_fast.projected_total > fc_slow.projected_total
    assert fc_fast.prob_over > fc_slow.prob_over

    # Environmental weather penalty
    fc_dome = model.forecast_total(
        home_team="Detroit Lions",
        away_team="Minnesota Vikings",
        is_dome=True,
        total_line=47.0,
    )
    fc_wind = model.forecast_total(
        home_team="Buffalo Bills",
        away_team="New York Jets",
        is_dome=False,
        wind_mph=22.0,
        total_line=47.0,
    )
    assert fc_dome.projected_total > fc_wind.projected_total
    assert fc_wind.weather_total_penalty >= 4.0


def test_nfl_efficiency_opponent_adjusted_store() -> None:
    eff = NFLEfficiencyStore()
    m = eff.get_matchup_efficiency(
        "Kansas City Chiefs",
        "Baltimore Ravens",
        as_of_date="2024-11-01",
        opponent_adjust=True,
        recency_decay=True,
    )
    assert m.home_prior_games >= 1
    assert m.away_prior_games >= 1
    assert isinstance(m.home_pass_epa, float)
    assert isinstance(m.home_rush_epa, float)
    assert isinstance(m.home_pressure_disparity, float)
    assert isinstance(m.home_pace, float)
