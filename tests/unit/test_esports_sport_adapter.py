"""Targeted unit tests for Esports family sport adapters."""

from __future__ import annotations

import pytest

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.sports.base import SportAdapter, SportRegistry
from model_prediction.sports.cs2 import CS2Adapter
from model_prediction.sports.dota2 import Dota2Adapter
from model_prediction.sports.lol import LoLAdapter
from model_prediction.sports.rainbow_six import RainbowSixAdapter
from model_prediction.sports.valorant import ValorantAdapter


@pytest.mark.unit
@pytest.mark.parametrize(
    "adapter_cls,sport_name",
    [
        (CS2Adapter, "cs2"),
        (Dota2Adapter, "dota2"),
        (LoLAdapter, "lol"),
        (ValorantAdapter, "valorant"),
        (RainbowSixAdapter, "rainbow_six"),
    ],
)
def test_esports_adapter_conformance(adapter_cls: type[SportAdapter], sport_name: str) -> None:
    adapter = adapter_cls()
    assert isinstance(adapter, SportAdapter)
    assert adapter.sport_name == sport_name
    assert MarketType.MONEYLINE in adapter.supported_markets
    assert MarketType.SPREAD in adapter.supported_markets
    assert MarketType.TOTAL in adapter.supported_markets

    # Test registration in SportRegistry
    reg = SportRegistry.get(sport_name)
    assert reg is not None
    assert reg.sport_name == sport_name


@pytest.mark.unit
def test_esports_prediction_and_grading() -> None:
    ctx = TimeContext.of("2026-11-08T12:00:00Z")
    adapter = CS2Adapter()

    matches = [
        {
            "event_id": "cs2_test_match_1",
            "team_a": "NaVi",
            "team_b": "FaZe",
            "series_format": "Bo3",
            "is_lan": True,
            "team_a_map_win_rate": 0.60,
            "team_b_map_win_rate": 0.50,
        }
    ]

    features = adapter.build_features(matches, ctx)
    assert len(features) == 1

    probs = adapter.generate_probabilities(features, ctx)
    assert len(probs) == 3  # Moneyline, Spread (-1.5), Total (2.5)

    ml = next(p for p in probs if p["market_type"] == "moneyline")
    assert 0.0 < ml["probabilities"]["home"] < 1.0
    assert 0.0 < ml["probabilities"]["away"] < 1.0
    assert abs((ml["probabilities"]["home"] + ml["probabilities"]["away"]) - 1.0) < 1e-4

    # Test Grading
    open_pick = {
        "pick_id": "pick_test_cs2",
        "market_type": "moneyline",
        "selection": "home",
        "line": 0.0,
        "units": 1.5,
    }
    box = {"home_score": 2, "away_score": 1}
    settled = adapter.grade_event("cs2_test_match_1", box, open_pick)
    assert settled is not None
    assert settled["status"] == "settled"
    assert settled["result"] == "win"
    assert settled["pnl_units"] == 1.5
