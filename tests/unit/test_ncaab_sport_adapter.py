from __future__ import annotations

import pytest

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.models.basketball import UpcomingGame
from model_prediction.sports.base import SportAdapter, SportRegistry
from model_prediction.sports.ncaab import NCAABAdapter


@pytest.mark.unit
def test_ncaab_adapter_registry_and_protocol() -> None:
    if not SportRegistry.has("ncaab"):
        SportRegistry.register(NCAABAdapter())
    adapter = SportRegistry.get("ncaab")
    assert adapter is not None
    assert isinstance(adapter, SportAdapter)
    assert adapter.sport_name == "ncaab"
    assert MarketType.MONEYLINE in adapter.supported_markets
    assert MarketType.SPREAD in adapter.supported_markets
    assert MarketType.TOTAL in adapter.supported_markets


@pytest.mark.unit
def test_ncaab_adapter_generate_probabilities() -> None:
    adapter = NCAABAdapter()
    ctx = TimeContext.of("2026-11-08T12:00:00Z")

    game = UpcomingGame(
        event_id="test_ncaab_1",
        event_start_utc="2026-11-08T19:00:00Z",
        away_team="Duke",
        home_team="North Carolina",
        spread_away_line=2.5,
        total_line=148.5,
        neutral_site=False,
    )

    features = {"slate": [game], "observable_history": []}
    preds = adapter.generate_probabilities(features, ctx)

    assert len(preds) == 3  # ML, Spread, Total
    markets = {p["market_type"] for p in preds}
    assert markets == {"moneyline", "spread", "total"}

    ml_pred = next(p for p in preds if p["market_type"] == "moneyline")
    probs = ml_pred["probabilities"]
    assert "home" in probs and "away" in probs
    assert abs(probs["home"] + probs["away"] - 1.0) < 1e-6


@pytest.mark.unit
def test_ncaab_adapter_grade_event() -> None:
    adapter = NCAABAdapter()

    # 1. Moneyline test: home win
    pick_ml = {"pick_id": "p1", "market_type": "moneyline", "selection": "home", "units": 1.5}
    box = {"home_score": 75, "away_score": 70}
    res_ml = adapter.grade_event("e1", box, pick_ml)
    assert res_ml is not None
    assert res_ml["status"] == "settled"
    assert res_ml["result"] == "win"
    assert res_ml["pnl_units"] == 1.5

    # 2. Spread test: home covers (+2.5, lost by 2 -> margin is -2, -2 + 2.5 = +0.5 > 0 -> win)
    pick_spread = {"pick_id": "p2", "market_type": "spread", "selection": "home", "line": 2.5, "units": 1.0}
    box_spread = {"home_score": 70, "away_score": 72}
    res_spread = adapter.grade_event("e2", box_spread, pick_spread)
    assert res_spread is not None
    assert res_spread["result"] == "win"

    # 3. Total test: over 145.0 (total 146 -> win)
    pick_tot = {"pick_id": "p3", "market_type": "total", "selection": "over", "line": 145.0, "units": 1.0}
    box_tot = {"home_score": 76, "away_score": 70}
    res_tot = adapter.grade_event("e3", box_tot, pick_tot)
    assert res_tot is not None
    assert res_tot["result"] == "win"
