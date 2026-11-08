from __future__ import annotations

import pytest

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.models.basketball import UpcomingGame
from model_prediction.sports.base import SportAdapter, SportRegistry
from model_prediction.sports.nba import NBAAdapter
from model_prediction.sports.ncaaf import NCAAFAdapter
from model_prediction.sports.nfl import NFLAdapter


@pytest.mark.unit
def test_nfl_adapter_registry_and_forecast() -> None:
    if not SportRegistry.has("nfl"):
        SportRegistry.register(NFLAdapter())
    adapter = SportRegistry.get("nfl")
    assert adapter is not None
    assert isinstance(adapter, SportAdapter)
    assert adapter.sport_name == "nfl"
    assert MarketType.MONEYLINE in adapter.supported_markets

    ctx = TimeContext.of("2026-11-08T12:00:00Z")
    game = {
        "event_id": "test_nfl_1",
        "event_start_utc": "2026-11-08T18:00:00Z",
        "away_team": "Detroit Lions",
        "home_team": "Green Bay Packers",
        "spread_home_line": -3.5,
        "total_line": 48.5,
    }
    features = {"slate": [game], "observable_history": []}
    preds = adapter.generate_probabilities(features, ctx)
    assert len(preds) == 3
    markets = {p["market_type"] for p in preds}
    assert markets == {"moneyline", "spread", "total"}

    # Grade event test
    box = {"home_score": 24, "away_score": 21}
    pick = {"pick_id": "p_nfl", "market_type": "spread", "selection": "home", "line": -2.5, "units": 1.0}
    res = adapter.grade_event("test_nfl_1", box, pick)
    assert res is not None
    assert res["result"] == "win"  # home margin is +3, 3 - 2.5 = 0.5 > 0 -> win


@pytest.mark.unit
def test_ncaaf_adapter_registry_and_forecast() -> None:
    if not SportRegistry.has("ncaaf"):
        SportRegistry.register(NCAAFAdapter())
    adapter = SportRegistry.get("ncaaf")
    assert adapter is not None
    assert isinstance(adapter, SportAdapter)
    assert adapter.sport_name == "ncaaf"

    ctx = TimeContext.of("2026-11-08T12:00:00Z")
    game = {
        "event_id": "test_cfb_1",
        "event_start_utc": "2026-11-08T20:00:00Z",
        "away_team": "Alabama",
        "home_team": "LSU",
        "spread_home_line": 2.5,
        "total_line": 56.5,
    }
    features = {"slate": [game], "observable_history": []}
    preds = adapter.generate_probabilities(features, ctx)
    assert len(preds) == 3

    # Grade event test: total over 56.5 (31 + 28 = 59 -> win)
    box = {"home_score": 31, "away_score": 28}
    pick = {"pick_id": "p_cfb", "market_type": "total", "selection": "over", "line": 56.5, "units": 1.5}
    res = adapter.grade_event("test_cfb_1", box, pick)
    assert res is not None
    assert res["result"] == "win"
    assert res["pnl_units"] == 1.5


@pytest.mark.unit
def test_nba_adapter_registry_and_forecast() -> None:
    if not SportRegistry.has("nba"):
        SportRegistry.register(NBAAdapter())
    adapter = SportRegistry.get("nba")
    assert adapter is not None
    assert isinstance(adapter, SportAdapter)
    assert adapter.sport_name == "nba"

    ctx = TimeContext.of("2026-11-08T12:00:00Z")
    game = UpcomingGame(
        event_id="test_nba_1",
        event_start_utc="2026-11-08T23:00:00Z",
        away_team="Boston Celtics",
        home_team="Milwaukee Bucks",
        spread_away_line=-1.5,
        total_line=228.5,
        league="NBA",
    )
    features = {"slate": [game], "observable_history": []}
    preds = adapter.generate_probabilities(features, ctx)
    assert len(preds) == 3

    # Grade event test: moneyline away win
    box = {"home_score": 110, "away_score": 115}
    pick = {"pick_id": "p_nba", "market_type": "moneyline", "selection": "away", "units": 1.0}
    res = adapter.grade_event("test_nba_1", box, pick)
    assert res is not None
    assert res["result"] == "win"
