"""Tests for Soccer Dixon-Coles v2, dynamic universe discovery, and entity resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from model_prediction.models.soccer_dixon_coles_v2 import (
    SoccerDixonColesV2Model,
)
from model_prediction.soccer.identity import (
    disambiguate_soccer_team,
    normalize_soccer_team_name,
)
from model_prediction.soccer.universe import (
    discover_soccer_leagues,
)


def test_normalize_and_disambiguate_soccer_team() -> None:
    norm = normalize_soccer_team_name("Arsenal F.C.")
    assert norm == "arsenal"

    ident = disambiguate_soccer_team("Arsenal Women", competition_context="wsl")
    assert ident.gender == "women"
    assert ident.squad_type == "senior"

    ident_u21 = disambiguate_soccer_team("Chelsea U21", competition_context="pl2")
    assert ident_u21.squad_type == "u21"
    assert ident_u21.gender == "men"


def test_discover_soccer_leagues(tmp_path: Path) -> None:
    out_file = tmp_path / "discovered_leagues.jsonl"
    markets = [
        {
            "tags": ["soccer", "premier-league"],
            "slug": "epl-arsenal-vs-chelsea",
            "series_id": "epl_2026",
            "series_name": "Premier League",
            "active": True,
        },
        {
            "tags": ["nba", "basketball"],
            "slug": "nba-celtics-vs-lakers",
            "active": True,
        },
    ]

    leagues = discover_soccer_leagues(markets, output_path=out_file)
    assert len(leagues) == 1
    assert leagues[0].polymarket_league_id == "epl_2026"
    assert out_file.exists()


def test_soccer_dixon_coles_v2_forecast() -> None:
    model = SoccerDixonColesV2Model()
    history = [
        {"home_team": "team_a", "away_team": "team_b", "home_score": 3, "away_score": 1},
        {"home_team": "team_a", "away_team": "team_c", "home_score": 2, "away_score": 0},
        {"home_team": "team_b", "away_team": "team_a", "home_score": 1, "away_score": 2},
    ]
    model.fit_team_ratings(history)

    forecast = model.forecast_match("team_a", "team_b", competition_id="global")

    # Joint distribution probabilities must be valid probabilities
    assert 0.0 < forecast.prob_home_win < 1.0
    assert 0.0 < forecast.prob_draw < 1.0
    assert 0.0 < forecast.prob_away_win < 1.0
    assert (
        pytest.approx(forecast.prob_home_win + forecast.prob_draw + forecast.prob_away_win, abs=1e-3) == 1.0
    )

    # BTTS
    assert 0.0 < forecast.prob_btts_yes < 1.0
    assert pytest.approx(forecast.prob_btts_yes + forecast.prob_btts_no, abs=1e-3) == 1.0

    # Totals
    assert pytest.approx(forecast.prob_over_2_5 + forecast.prob_under_2_5, abs=1e-3) == 1.0

    # Double Chance
    assert (
        pytest.approx(forecast.prob_double_chance_1x, abs=1e-3) == forecast.prob_home_win + forecast.prob_draw
    )
