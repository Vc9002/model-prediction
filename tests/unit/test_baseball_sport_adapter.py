"""Targeted unit tests for Baseball family sport adapters (MLB, KBO, NPB)."""

from __future__ import annotations

import pytest

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.sports.base import SportAdapter, SportRegistry
from model_prediction.sports.kbo import KBOAdapter
from model_prediction.sports.mlb import MLBAdapter
from model_prediction.sports.npb import NPBAdapter


@pytest.mark.unit
@pytest.mark.parametrize(
    "adapter_cls,sport_name",
    [
        (MLBAdapter, "mlb"),
        (KBOAdapter, "kbo"),
        (NPBAdapter, "npb"),
    ],
)
def test_baseball_adapter_conformance(adapter_cls: type[SportAdapter], sport_name: str) -> None:
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
def test_kbo_tie_handling_and_grading() -> None:
    ctx = TimeContext.of("2026-11-08T12:00:00Z")
    adapter = KBOAdapter()

    games = [
        {
            "event_id": "kbo_test_1",
            "home_team": "LG Twins",
            "away_team": "Doosan Bears",
            "lambda_home": 4.5,
            "lambda_away": 4.0,
            "total_line": 8.5,
            "spread_line": -1.5,
        }
    ]

    features = adapter.build_features(games, ctx)
    probs = adapter.generate_probabilities(features, ctx)
    assert len(probs) == 3

    ml = next(p for p in probs if p["market_type"] == "moneyline")
    assert "draw" in ml["probabilities"]
    assert ml["probabilities"]["draw"] > 0.0

    # Test Tie / Push grading for 2-way ML
    open_pick = {
        "pick_id": "kbo_pick_1",
        "market_type": "moneyline",
        "selection": "home",
        "line": 0.0,
        "units": 1.0,
    }
    tied_box = {"home_score": 4, "away_score": 4}
    settled = adapter.grade_event("kbo_test_1", tied_box, open_pick)
    assert settled is not None
    assert settled["result"] == "push"
    assert settled["pnl_units"] == 0.0
