"""Unit tests for Tennis v2 Dynamic Surface & Inactivity Shrinkage Model."""

from __future__ import annotations

import pytest

from model_prediction.models.tennis_v2 import (
    DEFAULT_ELO,
    TennisV2Model,
    set_to_match_prob_bo3,
    set_to_match_prob_bo5,
)


def test_format_transformation_bo3_vs_bo5():
    # If a player has a 60% chance to win a single set:
    # In Bo3: P = 0.60^2 * (3 - 1.2) = 0.36 * 1.8 = 0.648 (64.8%)
    p_bo3 = set_to_match_prob_bo3(0.60)
    assert p_bo3 == pytest.approx(0.648, abs=1e-3)

    # In Bo5: Favorite has higher win probability (upset variance drops)
    p_bo5 = set_to_match_prob_bo5(0.60)
    assert p_bo5 > p_bo3
    assert p_bo5 == pytest.approx(0.68256, abs=1e-3)

    # 50% set win prob remains 50% in both
    assert set_to_match_prob_bo3(0.50) == pytest.approx(0.50, abs=1e-3)
    assert set_to_match_prob_bo5(0.50) == pytest.approx(0.50, abs=1e-3)


def test_dynamic_surface_shrinkage():
    model = TennisV2Model()

    # Record 1 Grass match for Player A (win)
    model.record_match(
        {
            "winner": "Alcaraz",
            "loser": "Sinner",
            "surface": "Grass",
            "match_date": "2026-06-01",
        }
    )

    profiles = model.compute_player_profiles(as_of_date="2026-06-05")
    prof = profiles["Alcaraz"]

    # 1 match on Grass -> surface weight should be small (w ~ 0.75 * 1 / 16 = 0.0468)
    elo, w_surf, _days = model.evaluate_effective_elo(prof, surface="Grass", as_of_date="2026-06-05")
    assert w_surf < 0.10
    assert elo > DEFAULT_ELO

    # Add 20 more Grass matches
    for i in range(2, 22):
        model.record_match(
            {
                "winner": "Alcaraz",
                "loser": "Opponent",
                "surface": "Grass",
                "match_date": "2026-06-15",
            }
        )

    profiles_2 = model.compute_player_profiles(as_of_date="2026-06-20")
    prof_2 = profiles_2["Alcaraz"]
    elo_2, w_surf_2, _ = model.evaluate_effective_elo(prof_2, surface="Grass", as_of_date="2026-06-20")
    # 21 matches on Grass -> surface weight should expand (> 0.40)
    assert w_surf_2 > 0.40
    assert elo_2 > elo


def test_inactivity_decay():
    model = TennisV2Model()
    model.record_match(
        {
            "winner": "Djokovic",
            "loser": "Medvedev",
            "surface": "Hard",
            "match_date": "2026-01-01",
        }
    )

    profiles = model.compute_player_profiles(as_of_date="2026-01-05")
    prof = profiles["Djokovic"]

    # 10 days later: no decay
    elo_fresh, _, days_fresh = model.evaluate_effective_elo(prof, "Hard", as_of_date="2026-01-11")
    assert days_fresh == 10

    # 180 days later (long layoff): rating should shrink toward 1500
    elo_rusty, _, days_rusty = model.evaluate_effective_elo(prof, "Hard", as_of_date="2026-07-01")
    assert days_rusty > 100
    assert DEFAULT_ELO < elo_rusty < elo_fresh


def test_full_tennis_forecast():
    model = TennisV2Model()
    # Populate history
    for i in range(1, 10):
        model.record_match(
            {
                "winner": "Sinner",
                "loser": "Ruud",
                "surface": "Hard",
                "match_date": f"2026-05-0{i}",
            }
        )

    forecast_bo3 = model.forecast_match(
        "Sinner", "Ruud", surface="Hard", as_of_date="2026-05-15", match_format="Bo3"
    )
    forecast_bo5 = model.forecast_match(
        "Sinner", "Ruud", surface="Hard", as_of_date="2026-05-15", match_format="Bo5"
    )

    assert forecast_bo3.p_player_one_win > 0.60
    assert (
        forecast_bo5.p_player_one_win > forecast_bo3.p_player_one_win
    )  # Grand slam format favors dominant player
