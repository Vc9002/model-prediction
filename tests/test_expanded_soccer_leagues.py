"""Tests for expanded soccer league models, configurations, and market isolation."""

from __future__ import annotations

from model_prediction.domain import MarketType
from model_prediction.models.market_models import (
    UELMoneylineModel,
    UELSpreadModel,
    UELTotalModel,
    get_market_model,
)
from model_prediction.models.soccer import (
    UpcomingMatch,
    libertadores_soccer_model,
    saudi_pro_soccer_model,
    scottish_prem_soccer_model,
    uel_soccer_model,
)
from model_prediction.soccer.registry import resolve


def test_expanded_league_configs():
    """Verify newly added soccer leagues have distinct, calibrated configurations."""
    uel_cfg = resolve("UEL")
    assert uel_cfg.league_code == "UEL"
    assert uel_cfg.baseline == 1.5510
    assert uel_cfg.home_advantage == 1.2200

    uecl_cfg = resolve("UECL")
    assert uecl_cfg.league_code == "UECL"
    assert uecl_cfg.baseline == 1.5820

    facup_cfg = resolve("FA_CUP")
    assert facup_cfg.league_code == "FA_CUP"
    assert facup_cfg.baseline == 1.5200

    scot_cfg = resolve("SCOTTISH_PREM")
    assert scot_cfg.league_code == "SCOTTISH_PREM"
    assert scot_cfg.baseline == 1.4020

    saudi_cfg = resolve("SAUDI_PRO")
    assert saudi_cfg.league_code == "SAUDI_PRO"
    assert saudi_cfg.baseline == 1.5520

    lib_cfg = resolve("LIBERTADORES")
    assert lib_cfg.league_code == "LIBERTADORES"
    assert lib_cfg.home_advantage == 1.4500  # Extreme South American home field boost

    arg_cfg = resolve("ARGENTINA")
    assert arg_cfg.league_code == "ARGENTINA"
    assert arg_cfg.baseline == 1.0550  # Low scoring baseline

    aleague_cfg = resolve("A_LEAGUE")
    assert aleague_cfg.league_code == "A_LEAGUE"
    assert aleague_cfg.baseline == 1.6050  # High scoring baseline

    segunda_cfg = resolve("SEGUNDA")
    assert segunda_cfg.baseline == 1.0850


def test_dedicated_expanded_league_models():
    """Verify dedicated models instantiate and generate predictions for expanded leagues."""
    match = UpcomingMatch(
        event_id="uel-match-1",
        event_start_utc="2026-10-22T19:00:00Z",
        home_team="Roma",
        away_team="Real Sociedad",
        league="UEL",
    )

    uel_mod = uel_soccer_model()
    preds = uel_mod.predict_games([], [match])

    assert len(preds) > 0
    ml_preds = [p for p in preds if str(p.market_type).lower() == "moneyline"]
    assert len(ml_preds) == 1
    assert ml_preds[0].league == "UEL"
    assert "home" in ml_preds[0].probabilities
    assert "draw" in ml_preds[0].probabilities
    assert "away" in ml_preds[0].probabilities

    # Check Saudi Pro League model
    saudi_match = UpcomingMatch(
        event_id="saudi-match-1",
        event_start_utc="2026-10-23T18:00:00Z",
        home_team="Al-Hilal",
        away_team="Al-Nassr",
        league="SAUDI_PRO",
    )
    saudi_mod = saudi_pro_soccer_model()
    saudi_preds = saudi_mod.predict_games([], [saudi_match])
    assert len(saudi_preds) > 0
    assert saudi_preds[0].league == "SAUDI_PRO"

    # Check Copa Libertadores model
    lib_match = UpcomingMatch(
        event_id="lib-match-1",
        event_start_utc="2026-10-24T00:30:00Z",
        home_team="Flamengo",
        away_team="River Plate",
        league="LIBERTADORES",
    )
    lib_mod = libertadores_soccer_model()
    lib_preds = lib_mod.predict_games([], [lib_match])
    assert len(lib_preds) > 0
    assert lib_preds[0].league == "LIBERTADORES"

    # Check Scottish Premiership model
    scot_match = UpcomingMatch(
        event_id="scot-match-1",
        event_start_utc="2026-10-25T12:00:00Z",
        home_team="Celtic",
        away_team="Rangers",
        league="SCOTTISH_PREM",
    )
    scot_mod = scottish_prem_soccer_model()
    scot_preds = scot_mod.predict_games([], [scot_match])
    assert len(scot_preds) > 0
    assert scot_preds[0].league == "SCOTTISH_PREM"


def test_expanded_league_market_scoped_models():
    """Verify market-scoped models (Moneyline, Spread, Total) work on expanded soccer leagues."""
    match = UpcomingMatch(
        event_id="uel-match-2",
        event_start_utc="2026-10-22T20:00:00Z",
        home_team="Porto",
        away_team="Lazio",
        league="UEL",
    )

    ml_mod = UELMoneylineModel()
    sp_mod = UELSpreadModel()
    tot_mod = UELTotalModel()

    ml_preds = ml_mod.predict_games([], [match])
    sp_preds = sp_mod.predict_games([], [match])
    tot_preds = tot_mod.predict_games([], [match])

    assert len(ml_preds) == 1
    assert str(ml_preds[0].market_type).lower() == "moneyline"
    assert ml_preds[0].league == "UEL"

    assert len(sp_preds) > 0
    assert all(str(p.market_type).lower() == "spread" for p in sp_preds)

    assert len(tot_preds) > 0
    assert all(str(p.market_type).lower() == "total" for p in tot_preds)

    # Check resolver for various new leagues
    for lg in ["SAUDI_PRO", "TURKISH_SUPER", "LIBERTADORES", "ARGENTINA", "A_LEAGUE"]:
        m = get_market_model(lg, MarketType.MONEYLINE)
        assert m.league == lg
        assert m.market_type == MarketType.MONEYLINE
