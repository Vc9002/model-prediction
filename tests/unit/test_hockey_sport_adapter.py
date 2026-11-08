from __future__ import annotations

import pytest

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.models.hockey import UpcomingHockeyMatch
from model_prediction.sports.base import SportAdapter, SportRegistry
from model_prediction.sports.hockey import HockeyAdapter


@pytest.mark.unit
def test_hockey_adapter_registry_and_protocol() -> None:
    if not SportRegistry.has("hockey"):
        SportRegistry.register(HockeyAdapter())
    adapter = SportRegistry.get("hockey")
    assert adapter is not None
    assert isinstance(adapter, SportAdapter)
    assert adapter.sport_name == "hockey"
    assert MarketType.MONEYLINE in adapter.supported_markets
    assert MarketType.SPREAD in adapter.supported_markets
    assert MarketType.TOTAL in adapter.supported_markets


@pytest.mark.unit
def test_hockey_adapter_generate_probabilities() -> None:
    adapter = HockeyAdapter()
    ctx = TimeContext.of("2026-11-08T12:00:00Z")

    match = UpcomingHockeyMatch(
        event_id="test_nhl_1",
        event_start_utc="2026-11-08T19:00:00Z",
        away_team="Boston Bruins",
        home_team="Toronto Maple Leafs",
        league="NHL",
    )

    features = {"slate": [match], "observable_history": []}
    preds = adapter.generate_probabilities(features, ctx)

    assert len(preds) > 0
    markets = {p["market_type"] for p in preds}
    assert "moneyline" in markets
    assert "spread" in markets
    assert "total" in markets

    ml_pred = next(p for p in preds if p["market_type"] == "moneyline")
    probs = ml_pred["probabilities"]
    assert "home" in probs and "away" in probs
    assert abs(probs["home"] + probs["away"] - 1.0) < 1e-4


@pytest.mark.unit
def test_hockey_adapter_grade_event() -> None:
    adapter = HockeyAdapter()

    # 1. Moneyline test: home win 4-2
    pick_ml = {"pick_id": "p_h1", "market_type": "moneyline", "selection": "home", "units": 1.5}
    box = {"home_score": 4, "away_score": 2}
    res_ml = adapter.grade_event("h_e1", box, pick_ml)
    assert res_ml is not None
    assert res_ml["status"] == "settled"
    assert res_ml["result"] == "win"
    assert res_ml["pnl_units"] == 1.5

    # 2. Spread test: away covers +1.5 (final 4-3, margin is +1 home, away +1.5 covers -> win)
    pick_spread = {"pick_id": "p_h2", "market_type": "spread", "selection": "away", "line": 1.5, "units": 1.0}
    box_spread = {"home_score": 4, "away_score": 3}
    res_spread = adapter.grade_event("h_e2", box_spread, pick_spread)
    assert res_spread is not None
    assert res_spread["result"] == "win"

    # 3. Total test: over 5.5 (final 4-2 = 6 goals -> win)
    pick_tot = {"pick_id": "p_h3", "market_type": "total", "selection": "over", "line": 5.5, "units": 1.0}
    box_tot = {"home_score": 4, "away_score": 2}
    res_tot = adapter.grade_event("h_e3", box_tot, pick_tot)
    assert res_tot is not None
    assert res_tot["result"] == "win"
