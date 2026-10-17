"""Unit tests for dedicated, independent per-league basketball models."""

from __future__ import annotations

from model_prediction.features.base import GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import (
    EUROLEAGUE_CONFIG,
    NBA_CONFIG,
    NCAAB_CONFIG,
    WNBA_CONFIG,
    WNCAAB_CONFIG,
    BasketballLeagueRegistry,
    DedicatedBasketballLeagueModel,
    EuroLeagueModel,
    NBAModel,
    NCAABModel,
    WNBAModel,
    WNCAABModel,
    get_basketball_model,
    nba_model,
    ncaab_model,
    wnba_model,
)


def test_league_config_parameters_are_distinct():
    # NBA has 48-minute duration, WNBA & NCAAB have 40
    assert NBA_CONFIG.game_duration_minutes == 48
    assert WNBA_CONFIG.game_duration_minutes == 40
    assert NCAAB_CONFIG.game_duration_minutes == 40
    assert WNCAAB_CONFIG.game_duration_minutes == 40
    assert EUROLEAGUE_CONFIG.game_duration_minutes == 40

    # Possessions and pace differ
    assert NBA_CONFIG.pace > WNBA_CONFIG.pace > NCAAB_CONFIG.pace

    # Home court advantage differs (college basketball has higher HCA)
    assert NCAAB_CONFIG.home_court_points > NBA_CONFIG.home_court_points
    assert EUROLEAGUE_CONFIG.home_court_points > NBA_CONFIG.home_court_points

    # Standard deviations differ
    assert NBA_CONFIG.margin_sd > NCAAB_CONFIG.margin_sd
    assert NBA_CONFIG.total_sd > NCAAB_CONFIG.total_sd


def test_dedicated_league_model_instances():
    nba = nba_model()
    wnba = wnba_model()
    ncaab = ncaab_model()
    wncaab = get_basketball_model("WNCAAB")
    euro = get_basketball_model("EUROLEAGUE")

    assert isinstance(nba, NBAModel)
    assert isinstance(wnba, WNBAModel)
    assert isinstance(ncaab, NCAABModel)
    assert isinstance(wncaab, WNCAABModel)
    assert isinstance(euro, EuroLeagueModel)
    assert isinstance(nba, DedicatedBasketballLeagueModel)

    assert nba.league == "NBA"
    assert wnba.league == "WNBA"
    assert ncaab.league == "NCAAB"
    assert wncaab.league == "WNCAAB"
    assert euro.league == "EUROLEAGUE"


def test_strict_league_isolation():
    """Ensure games from one league NEVER alter predictions or ratings in another league."""
    ncaab = ncaab_model()

    # NCAA game history: Duke beats UNC
    ncaab_history = [
        GameRecord(
            event_id=f"ncaa_{i}",
            event_start_utc=f"2026-02-{i:02d}T19:00:00Z",
            league="NCAAB",
            away_team="UNC",
            home_team="Duke",
            away_score=70,
            home_score=85,
        )
        for i in range(1, 15)
    ]

    # NBA game history: completely different teams and environment
    nba_history = [
        GameRecord(
            event_id=f"nba_{i}",
            event_start_utc=f"2026-02-{i:02d}T19:00:00Z",
            league="NBA",
            away_team="Celtics",
            home_team="Lakers",
            away_score=125,
            home_score=90,
        )
        for i in range(1, 15)
    ]

    # Combined history containing both NBA and NCAAB
    combined_history = ncaab_history + nba_history

    upcoming = [
        UpcomingGame(
            event_id="up_ncaa",
            event_start_utc="2026-03-01T19:00:00Z",
            away_team="UNC",
            home_team="Duke",
            spread_away_line=8.5,
            total_line=145.5,
            league="NCAAB",
        )
    ]

    # Prediction using isolated NCAAB history
    pred_isolated = ncaab.predict_games(ncaab_history, upcoming)
    # Prediction using combined history (containing NBA games)
    pred_combined = ncaab.predict_games(combined_history, upcoming)

    # NCAAB model must filter out NBA games, producing identical predictions
    assert pred_isolated[0].probabilities == pred_combined[0].probabilities
    assert pred_isolated[1].probabilities == pred_combined[1].probabilities
    assert pred_isolated[2].probabilities == pred_combined[2].probabilities


def test_basketball_league_registry_slate_prediction():
    registry = BasketballLeagueRegistry()

    upcoming_slate = [
        UpcomingGame(
            event_id="g_nba",
            event_start_utc="2026-10-20T23:00:00Z",
            away_team="Warriors",
            home_team="Celtics",
            spread_away_line=4.5,
            total_line=228.5,
            league="NBA",
        ),
        UpcomingGame(
            event_id="g_ncaa",
            event_start_utc="2026-11-10T19:00:00Z",
            away_team="Kansas",
            home_team="Kentucky",
            spread_away_line=2.5,
            total_line=152.0,
            league="NCAAB",
        ),
        UpcomingGame(
            event_id="g_euro",
            event_start_utc="2026-10-22T18:00:00Z",
            away_team="Real Madrid",
            home_team="Panathinaikos",
            spread_away_line=1.5,
            total_line=162.5,
            league="EUROLEAGUE",
        ),
    ]

    predictions = registry.predict_slate(upcoming_slate)
    assert len(predictions) == 9  # 3 games * 3 markets (moneyline, spread, total)

    by_event = {}
    for p in predictions:
        by_event.setdefault(p.event_id, []).append(p)

    assert "g_nba" in by_event
    assert "g_ncaa" in by_event
    assert "g_euro" in by_event

    # Check that model_version matches each league
    assert by_event["g_nba"][0].model_version == NBA_CONFIG.model_version
    assert by_event["g_ncaa"][0].model_version == NCAAB_CONFIG.model_version
    assert by_event["g_euro"][0].model_version == EUROLEAGUE_CONFIG.model_version
