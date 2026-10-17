"""Unit tests for strict per-league soccer model separation and parameter isolation."""

from __future__ import annotations

import pytest

from model_prediction.features.base import GameRecord
from model_prediction.models.soccer import (
    DedicatedSoccerLeagueModel,
    UpcomingMatch,
    bundesliga_soccer_model,
    epl_soccer_model,
    get_soccer_league_model,
    la_liga_soccer_model,
    mls_soccer_model,
    soccer_model,
)
from model_prediction.soccer.registry import resolve


def test_soccer_league_configs_are_distinct():
    epl_cfg = resolve("EPL")
    mls_cfg = resolve("MLS")
    bun_cfg = resolve("BUNDESLIGA")
    lal_cfg = resolve("LA_LIGA")

    # Baselines differ by league
    assert bun_cfg.baseline > epl_cfg.baseline > lal_cfg.baseline

    # Home advantage differs: MLS has strong home advantage (~1.2325)
    assert mls_cfg.home_advantage > epl_cfg.home_advantage
    assert mls_cfg.home_advantage > bun_cfg.home_advantage

    # Dixon-Coles rho parameters differ
    assert bun_cfg.dc_rho != mls_cfg.dc_rho


def test_dedicated_soccer_league_models():
    epl = epl_soccer_model()
    mls = mls_soccer_model()
    lal = la_liga_soccer_model()
    bun = bundesliga_soccer_model()

    assert isinstance(epl, DedicatedSoccerLeagueModel)
    assert epl.league_code == "EPL"
    assert mls.league_code == "MLS"
    assert lal.league_code == "LA_LIGA"
    assert bun.league_code == "BUNDESLIGA"

    generic = get_soccer_league_model("SERIE_A")
    assert generic.league_code == "SERIE_A"


def test_zero_cross_league_bleed_in_soccer():
    """Ensure that adding matches in another league does NOT alter EPL ratings or predictions."""
    model = soccer_model()

    # EPL match history: Arsenal consistently beating Chelsea
    epl_history = [
        GameRecord(
            event_id=f"epl_{i}",
            event_start_utc=f"2026-03-{i:02d}T15:00:00Z",
            league="EPL",
            away_team="Chelsea",
            home_team="Arsenal",
            away_score=0,
            home_score=3,
        )
        for i in range(1, 10)
    ]

    # La Liga history: Huge blowout scores (Real Madrid 8-0)
    laliga_history = [
        GameRecord(
            event_id=f"lal_{i}",
            event_start_utc=f"2026-03-{i:02d}T19:00:00Z",
            league="LA_LIGA",
            away_team="Getafe",
            home_team="Real Madrid",
            away_score=0,
            home_score=8,
        )
        for i in range(1, 10)
    ]

    # Combined history
    combined_history = epl_history + laliga_history

    upcoming = [
        UpcomingMatch(
            event_id="epl_derby",
            event_start_utc="2026-04-01T15:00:00Z",
            away_team="Chelsea",
            home_team="Arsenal",
            league="EPL",
        )
    ]

    # Predict using isolated EPL history
    preds_isolated = model.predict_games(epl_history, upcoming)
    # Predict using combined history
    preds_combined = model.predict_games(combined_history, upcoming)

    ml_isolated = next(p for p in preds_isolated if p.market_type == "moneyline")
    ml_combined = next(p for p in preds_combined if p.market_type == "moneyline")

    # Probabilities must be strictly identical — La Liga blowouts must not distort EPL goal rates
    assert ml_isolated.probabilities == ml_combined.probabilities
    assert ml_isolated.feature_basis["home_goal_rate"] == ml_combined.feature_basis["home_goal_rate"]
    assert ml_isolated.feature_basis["away_goal_rate"] == ml_combined.feature_basis["away_goal_rate"]


def test_dedicated_league_model_prediction():
    epl = epl_soccer_model()
    history = [
        GameRecord(
            event_id=f"m_{i}",
            event_start_utc=f"2026-04-{i:02d}T15:00:00Z",
            league="EPL",
            away_team="Tottenham",
            home_team="Liverpool",
            away_score=1,
            home_score=2,
        )
        for i in range(1, 12)
    ]

    upcoming = [
        UpcomingMatch(
            event_id="up1",
            event_start_utc="2026-05-01T15:00:00Z",
            away_team="Tottenham",
            home_team="Liverpool",
        )
    ]

    preds = epl.predict_games(history, upcoming)
    assert len(preds) > 0

    ml = next(p for p in preds if p.market_type == "moneyline")
    assert ml.probabilities["home"] > ml.probabilities["away"]
    assert pytest.approx(sum(ml.probabilities.values())) == 1.0
