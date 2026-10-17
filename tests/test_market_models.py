"""Tests for dedicated per-league, per-market models."""

from __future__ import annotations

from model_prediction.domain import MarketType
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.darts import UpcomingDartsMatch
from model_prediction.models.hockey import UpcomingHockeyMatch
from model_prediction.models.market_models import (
    EPLMoneylineModel,
    EPLSpreadModel,
    EPLTotalModel,
    MarketScopedModel,
    NBAMoneylineModel,
    NBASpreadModel,
    NBATotalModel,
    NHLMoneylineModel,
    NHLSpreadModel,
    NHLTotalModel,
    get_market_model,
    moneyline_model,
    spread_model,
    total_model,
)
from model_prediction.models.soccer import UpcomingMatch


def test_basketball_market_separation():
    """Verify that NBA models strictly separate Moneyline, Spread, and Total."""
    game = UpcomingGame(
        event_id="nba-game-1",
        event_start_utc="2026-10-17T00:00:00Z",
        away_team="BOS",
        home_team="NYK",
        spread_away_line=-3.5,
        total_line=218.5,
        league="NBA",
    )

    ml_model = NBAMoneylineModel()
    spread_mod = NBASpreadModel()
    tot_model = NBATotalModel()

    ml_preds = ml_model.predict_games([], [game])
    spread_preds = spread_mod.predict_games([], [game])
    tot_preds = tot_model.predict_games([], [game])

    assert len(ml_preds) == 1
    assert str(ml_preds[0].market_type).lower() == "moneyline"
    assert "home" in ml_preds[0].probabilities
    assert "away" in ml_preds[0].probabilities

    assert len(spread_preds) == 1
    assert str(spread_preds[0].market_type).lower() == "spread"
    assert spread_preds[0].line == -3.5

    assert len(tot_preds) == 1
    assert str(tot_preds[0].market_type).lower() == "total"
    assert tot_preds[0].line == 218.5


def test_soccer_market_separation():
    """Verify that EPL models strictly isolate 3-way moneyline, spreads, and totals."""
    match = UpcomingMatch(
        event_id="epl-match-1",
        event_start_utc="2026-10-17T14:00:00Z",
        home_team="Arsenal",
        away_team="Chelsea",
        league="EPL",
    )

    ml_model = EPLMoneylineModel()
    sp_model = EPLSpreadModel()
    tot_model = EPLTotalModel()

    ml_preds = ml_model.predict_games([], [match])
    sp_preds = sp_model.predict_games([], [match])
    tot_preds = tot_model.predict_games([], [match])

    # Moneyline model emits only moneyline (no total, no spread, no btts)
    assert len(ml_preds) == 1
    assert str(ml_preds[0].market_type).lower() == "moneyline"
    assert ml_preds[0].probabilities.keys() == {"home", "away", "draw"}

    # Spread model emits only spreads
    assert len(sp_preds) > 0
    assert all(str(p.market_type).lower() == "spread" for p in sp_preds)

    # Total model emits only totals
    assert len(tot_preds) > 0
    assert all(str(p.market_type).lower() == "total" for p in tot_preds)


def test_hockey_market_separation():
    """Verify that NHL models strictly isolate moneyline, puckline spread, and totals."""
    match = UpcomingHockeyMatch(
        event_id="nhl-match-1",
        event_start_utc="2026-10-17T23:00:00Z",
        home_team="NYR",
        away_team="BOS",
        league="NHL",
    )

    ml_model = NHLMoneylineModel()
    sp_model = NHLSpreadModel()
    tot_model = NHLTotalModel()

    ml_preds = ml_model.predict_games([], [match])
    sp_preds = sp_model.predict_games([], [match])
    tot_preds = tot_model.predict_games([], [match])

    assert len(ml_preds) == 1
    assert str(ml_preds[0].market_type).lower() == "moneyline"

    assert len(sp_preds) > 0
    assert all(str(p.market_type).lower() == "spread" for p in sp_preds)

    assert len(tot_preds) > 0
    assert all(str(p.market_type).lower() == "total" for p in tot_preds)


def test_darts_market_separation():
    """Verify that Darts PDC models isolate match winner, leg spread, and leg totals."""
    match = UpcomingDartsMatch(
        event_id="darts-match-1",
        event_start_utc="2026-10-17T18:00:00Z",
        player_a="Luke Humphries",
        player_b="Michael van Gerwen",
        league="DARTS_PDC",
        best_of_legs=11,
        spread_line=-1.5,
        total_line=9.5,
    )

    ml_mod = moneyline_model("DARTS_PDC")
    sp_mod = spread_model("DARTS_PDC")
    tot_mod = total_model("DARTS_PDC")

    ml_preds = ml_mod.predict_games([], [match])
    sp_preds = sp_mod.predict_games([], [match])
    tot_preds = tot_mod.predict_games([], [match])

    assert len(ml_preds) == 1
    assert str(ml_preds[0].market_type).lower() == "moneyline"

    assert len(sp_preds) == 1
    assert str(sp_preds[0].market_type).lower() == "spread"

    assert len(tot_preds) == 1
    assert str(tot_preds[0].market_type).lower() == "total"


def test_universal_market_model_resolver():
    """Verify get_market_model resolves across leagues and market types."""
    leagues = ["NBA", "WNBA", "NCAAB", "EUROLEAGUE", "EPL", "MLS", "NHL", "DARTS_PDC", "CS2", "KBO", "MLB"]
    markets = [MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL]

    for lg in leagues:
        for mkt in markets:
            model = get_market_model(lg, mkt)
            assert isinstance(model, MarketScopedModel)
            assert model.market_type == mkt
            assert model.league == lg
