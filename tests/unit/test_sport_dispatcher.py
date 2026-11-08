"""Unit tests for SportDispatcher."""

from __future__ import annotations

import pytest

from model_prediction.cli.dispatcher import SportDispatcher
from model_prediction.core.time_context import TimeContext


@pytest.mark.unit
def test_sport_dispatcher_unsupported_sport() -> None:
    ctx = TimeContext.of("2026-11-08T18:00:00Z")
    res = SportDispatcher.dispatch_forecast("non_existent_sport", ctx)
    assert res["status"] == "unsupported"
    assert res["count"] == 0


@pytest.mark.unit
def test_sport_dispatcher_supported_sport_forecast() -> None:
    ctx = TimeContext.of("2026-11-08T18:00:00Z")
    matches = [
        {
            "event_id": "cs2_disp_test",
            "team_a": "Vitality",
            "team_b": "MOUZ",
            "series_format": "Bo3",
        }
    ]
    res = SportDispatcher.dispatch_forecast("cs2", ctx, matches=matches)
    assert res["status"] == "ok"
    assert res["count"] == 3  # ML, spread, total
    assert len(res["candidates"]) == 3
    assert res["candidates"][0]["sport"] == "cs2"


@pytest.mark.unit
def test_sport_dispatcher_settlement() -> None:
    open_pick = {
        "pick_id": "pick_disp_1",
        "market_type": "moneyline",
        "selection": "home",
        "line": 0.0,
        "units": 1.0,
    }
    box = {"home_score": 2, "away_score": 0}
    res = SportDispatcher.dispatch_settlement("cs2", "cs2_disp_test", box, open_pick)
    assert res is not None
    assert res["status"] == "settled"
    assert res["result"] == "win"
    assert res["pnl_units"] == 1.0
