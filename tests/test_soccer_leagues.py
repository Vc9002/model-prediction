"""Unit tests for per-league independent Dixon-Coles soccer model registry."""

from __future__ import annotations

from model_prediction.models.soccer_leagues import (
    LeagueDixonColesRegistry,
    LeagueMatchRecord,
    SoccerLeague,
)


def test_league_isolation():
    registry = LeagueDixonColesRegistry()

    # EPL matches: Arsenal vs Chelsea
    registry.record_match(
        LeagueMatchRecord(
            match_id="epl_1",
            match_date="2026-05-01",
            league=SoccerLeague.EPL,
            home_team="Arsenal",
            away_team="Chelsea",
            home_goals=3,
            away_goals=1,
        )
    )

    # La Liga matches: Real Madrid vs Barcelona
    registry.record_match(
        LeagueMatchRecord(
            match_id="laliga_1",
            match_date="2026-05-01",
            league=SoccerLeague.LA_LIGA,
            home_team="Real Madrid",
            away_team="Barcelona",
            home_goals=0,
            away_goals=0,
        )
    )

    epl_artifact = registry.fit_league(SoccerLeague.EPL, as_of_date="2026-05-10")
    assert epl_artifact.matches_count == 1
    assert epl_artifact.teams_count == 2

    laliga_artifact = registry.fit_league(SoccerLeague.LA_LIGA, as_of_date="2026-05-10")
    assert laliga_artifact.matches_count == 1
    assert laliga_artifact.teams_count == 2

    # Bundesliga has 0 matches
    bundesliga_artifact = registry.fit_league(SoccerLeague.BUNDESLIGA, as_of_date="2026-05-10")
    assert bundesliga_artifact.matches_count == 0


def test_league_forecasting():
    registry = LeagueDixonColesRegistry()
    # Record matches for EPL
    for i in range(1, 5):
        registry.record_match(
            LeagueMatchRecord(
                match_id=f"epl_{i}",
                match_date=f"2026-05-0{i}",
                league=SoccerLeague.EPL,
                home_team="ManCity",
                away_team="Everton",
                home_goals=3,
                away_goals=0,
            )
        )

    forecast = registry.forecast_match(
        league=SoccerLeague.EPL,
        home_team="ManCity",
        away_team="Everton",
        as_of_date="2026-05-10",
    )
    assert forecast.prob_home > 0.50
    assert forecast.lambda_home > forecast.lambda_away
    assert forecast.over_under[2.5]["over"] > 0.50
