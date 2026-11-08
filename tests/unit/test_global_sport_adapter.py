"""Targeted unit tests for Global and Target sport adapters (Soccer, Tennis, Darts, WNBA, Basketball)."""

from __future__ import annotations

import pytest

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.sports.base import SportAdapter, SportRegistry
from model_prediction.sports.basketball import BasketballAdapter
from model_prediction.sports.darts import DartsAdapter
from model_prediction.sports.soccer import SoccerAdapter
from model_prediction.sports.tennis import TennisAdapter
from model_prediction.sports.wnba import WNBAAdapter


@pytest.mark.unit
@pytest.mark.parametrize(
    "adapter_cls,sport_name",
    [
        (SoccerAdapter, "soccer"),
        (TennisAdapter, "tennis"),
        (DartsAdapter, "darts"),
        (WNBAAdapter, "wnba"),
        (BasketballAdapter, "basketball"),
    ],
)
def test_global_adapter_conformance(adapter_cls: type[SportAdapter], sport_name: str) -> None:
    adapter = adapter_cls()
    assert isinstance(adapter, SportAdapter)
    assert adapter.sport_name == sport_name
    assert MarketType.MONEYLINE in adapter.supported_markets
    assert MarketType.SPREAD in adapter.supported_markets
    assert MarketType.TOTAL in adapter.supported_markets

    reg = SportRegistry.get(sport_name)
    assert reg is not None
    assert reg.sport_name == sport_name


@pytest.mark.unit
def test_tennis_retirement_fair_push_grading() -> None:
    adapter = TennisAdapter()
    open_pick = {
        "pick_id": "tennis_pick_1",
        "market_type": "moneyline",
        "selection": "home",
        "line": 0.0,
        "units": 1.0,
    }
    # Retired match should result in push
    box = {"status": "retired", "home_score": 1, "away_score": 0}
    settled = adapter.grade_event("tennis_event_1", box, open_pick)
    assert settled is not None
    assert settled["result"] == "push"
    assert settled["pnl_units"] == 0.0


@pytest.mark.unit
def test_darts_prediction_and_grading() -> None:
    ctx = TimeContext.of("2026-11-08T12:00:00Z")
    adapter = DartsAdapter()
    matches = [
        {
            "event_id": "darts_final",
            "player_a": "Luke Littler",
            "player_b": "Michael van Gerwen",
            "best_of_legs": 11,
            "spread_line": -1.5,
            "total_line": 9.5,
        }
    ]
    features = adapter.build_features(matches, ctx)
    probs = adapter.generate_probabilities(features, ctx)
    assert len(probs) == 3

    ml = next(p for p in probs if p["market_type"] == "moneyline")
    assert 0.0 < ml["probabilities"]["home"] < 1.0
    assert 0.0 < ml["probabilities"]["away"] < 1.0

    # Test grading
    open_pick = {
        "pick_id": "darts_pick_1",
        "market_type": "spread",
        "selection": "home",
        "line": -1.5,
        "units": 1.0,
    }
    # Score 6-3: margin is +3. +3 + (-1.5) = +1.5 > 0 -> win
    box = {"home_score": 6, "away_score": 3}
    settled = adapter.grade_event("darts_final", box, open_pick)
    assert settled is not None
    assert settled["result"] == "win"
    assert settled["pnl_units"] == 1.0
