"""Targeted tests for overhauled NFL V7 models (nfl-spread-structural-v7 and nfl-total-structural-v7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_prediction.features.nfl_efficiency import NFLEfficiencyStore
from model_prediction.models.nfl_spread_v7 import NFLSpreadModelV7
from model_prediction.models.nfl_spread_v8 import NFLSpreadModelV8
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
    assert model.edsr_weight == 4.0
    assert model.turnover_luck_weight == 0.5
    assert model.use_scoring_simulation is True
    assert model.rho == 0.08
    assert model.simulation_weight == 0.20

    # EDSR margin boost check
    fc_edsr_high = model.forecast_spread(
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        home_first_down_rate=0.40,
        away_first_down_rate=0.25,
        spread_home_line=-2.5,
    )
    fc_edsr_low = model.forecast_spread(
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        home_first_down_rate=0.25,
        away_first_down_rate=0.40,
        spread_home_line=-2.5,
    )
    assert fc_edsr_high.projected_margin_home > fc_edsr_low.projected_margin_home

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


def test_nfl_spread_v8_discrete_margin_symmetry_and_push() -> None:
    model = NFLSpreadModelV8(market_weight=1.0, use_scoring_simulation=False)
    for line in (3.0, 3.5, 8.0):
        home_dog = model.forecast_spread(home_team="A", away_team="B", spread_home_line=line)
        home_favorite = model.forecast_spread(home_team="A", away_team="B", spread_home_line=-line)
        assert abs(home_dog.prob_home_cover - home_favorite.prob_away_cover) < 0.001
        assert abs(home_dog.prob_spread_push - home_favorite.prob_spread_push) < 0.001
        assert (
            abs(home_dog.prob_home_cover + home_dog.prob_away_cover + home_dog.prob_spread_push - 1) < 0.001
        )
    at_three = model.forecast_spread(home_team="A", away_team="B", spread_home_line=-3.0)
    past_three = model.forecast_spread(home_team="A", away_team="B", spread_home_line=-3.5)
    assert at_three.prob_spread_push > 0.0
    assert past_three.prob_spread_push == 0.0
    assert at_three.prob_home_cover != past_three.prob_home_cover
    assert (
        model.forecast_spread(home_team="A", away_team="B", spread_home_line=8.0).effective_sd
        == model.margin_sd * 1.25
    )


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
    assert model.ngsp_weight == 0.35

    # NGSP pass-ratio pace acceleration check
    fc_pass_heavy = model.forecast_total(
        home_team="Miami Dolphins",
        away_team="Detroit Lions",
        home_pass_ratio=0.68,
        away_pass_ratio=0.65,
        total_line=48.5,
    )
    fc_run_heavy = model.forecast_total(
        home_team="Miami Dolphins",
        away_team="Detroit Lions",
        home_pass_ratio=0.45,
        away_pass_ratio=0.48,
        total_line=48.5,
    )
    assert fc_pass_heavy.projected_total > fc_run_heavy.projected_total

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
    assert fc_wind.weather_total_penalty >= 2.0

    # Explosive play rate and red-zone TD conversion test
    fc_explosive = model.forecast_total(
        home_team="Kansas City Chiefs",
        away_team="Buffalo Bills",
        home_explosive_rate=0.14,
        away_explosive_rate=0.13,
        home_rz_td_rate=0.72,
        away_rz_td_rate=0.68,
        total_line=47.5,
    )
    fc_grind = model.forecast_total(
        home_team="Kansas City Chiefs",
        away_team="Buffalo Bills",
        home_explosive_rate=0.05,
        away_explosive_rate=0.06,
        home_rz_td_rate=0.45,
        away_rz_td_rate=0.48,
        total_line=47.5,
    )
    assert fc_explosive.projected_total > fc_grind.projected_total
    assert fc_explosive.prob_over > fc_grind.prob_over


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
    assert isinstance(m.home_explosive_rate, float)
    assert isinstance(m.home_rz_td_rate, float)
    assert isinstance(m.home_first_down_rate, float)
    assert isinstance(m.home_turnover_luck, float)
    assert isinstance(m.home_pass_ratio, float)


def test_bivariate_scoring_simulation() -> None:
    from model_prediction.models.nfl_scoring_simulation import solve_bivariate_scoring

    res = solve_bivariate_scoring(
        exp_home_pts=24.5,
        exp_away_pts=20.5,
        home_rz_td_rate=0.65,
        away_rz_td_rate=0.55,
        spread_home_line=-3.0,
        total_line=44.0,
    )
    assert 0.30 <= res.prob_home_cover <= 0.70
    assert 0.30 <= res.prob_away_cover <= 0.70
    assert res.prob_spread_push > 0.02  # Key number 3 has notable push mass
    assert res.prob_total_push > 0.01
    assert round(res.prob_home_cover + res.prob_away_cover + res.prob_spread_push, 4) == 1.0000
    assert round(res.prob_over + res.prob_under + res.prob_total_push, 4) == 1.0000
    assert res.prob_home_win > res.prob_away_win


def test_forecast_from_efficiency_with_qb_injury_penalty() -> None:
    path_sp = REPO / "config" / "models" / "nfl-spread-structural-v7.json"
    path_tot = REPO / "config" / "models" / "nfl-total-structural-v7.json"
    spread_model = NFLSpreadModelV7.from_artifact(json.loads(path_sp.read_text(encoding="utf-8")))
    total_model = NFLTotalModelV7.from_artifact(json.loads(path_tot.read_text(encoding="utf-8")))
    eff_store = NFLEfficiencyStore()

    # Healthy matchup
    eff_healthy = eff_store.get_matchup_efficiency(
        "Buffalo Bills",
        "Baltimore Ravens",
        as_of_date="2024-11-01",
        home_qb="Josh Allen",
        away_qb="Lamar Jackson",
    )
    fc_sp_healthy = spread_model.forecast_from_efficiency(eff_healthy, spread_home_line=-3.0)
    fc_tot_healthy = total_model.forecast_from_efficiency(eff_healthy, total_line=50.0)

    # Injured QB matchup with penalty
    injuries = [
        {"player_name": "Lamar Jackson", "team": "Baltimore Ravens", "position": "QB", "status": "Out"}
    ]
    eff_injured = eff_store.get_matchup_efficiency(
        "Buffalo Bills",
        "Baltimore Ravens",
        as_of_date="2024-11-01",
        home_qb="Josh Allen",
        away_qb="Lamar Jackson",
        injury_entries=injuries,
    )
    assert eff_injured.away_qb_tier_adj < eff_healthy.away_qb_tier_adj

    fc_sp_injured = spread_model.forecast_from_efficiency(eff_injured, spread_home_line=-3.0)
    fc_tot_injured = total_model.forecast_from_efficiency(eff_injured, total_line=50.0)

    # Bills margin should increase when away QB is out
    assert fc_sp_injured.projected_margin_home > fc_sp_healthy.projected_margin_home
    assert fc_sp_injured.prob_home_cover > fc_sp_healthy.prob_home_cover
    assert fc_tot_injured.projected_total < fc_tot_healthy.projected_total


def test_nfl_spread_v7_heavy_line_replays_from_effective_sd() -> None:
    """|line| >= 8 prices at 1.25x margin_sd. The decision snapshot must carry THAT sd:
    with the artifact's base sd the scalar replay rejects the row and the forecast path
    silently skips the candidate (found 2026-11-11, no heavy line had appeared since the
    1.25x change landed)."""
    from model_prediction.scalar_artifact_replay import build_snapshot, validate_decision_snapshot

    artifact = json.loads(
        (REPO / "config" / "models" / "nfl-spread-structural-v7.json").read_text(encoding="utf-8")
    )
    model = NFLSpreadModelV7.from_artifact(artifact)
    fc = model.forecast_spread(home_team="A", away_team="B", spread_home_line=-9.5)
    assert fc.effective_sd == model.margin_sd * 1.25

    def _snapshot(sd: float) -> dict:
        return build_snapshot(
            artifact,
            {},
            {"mean": fc.projected_margin_home, "sd": sd, "line": 9.5},
            event_id="e1",
            event_start_utc="2026-11-16T18:00:00Z",
            observed_at_utc="2026-11-16T12:00:00Z",
        )

    row = {
        "model_version": model.version,
        "model_artifact_hash": artifact["artifact_hash"],
        "league": "NFL",
        "market_type": "spread",
        "selection": "home",
        "event_id": "e1",
        "event_start_utc": "2026-11-16T18:00:00Z",
        "line": -9.5,
        "model_probability": fc.prob_home_cover,
    }
    replayed = validate_decision_snapshot(_snapshot(fc.effective_sd), row)
    assert abs(replayed - fc.prob_home_cover) <= 2e-6
    with pytest.raises(ValueError, match="scalar_replay_probability_mismatch"):
        validate_decision_snapshot(_snapshot(model.margin_sd), row)
