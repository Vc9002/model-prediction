"""Unit tests for SportAdapter protocol and SportRegistry."""

from __future__ import annotations

from typing import Any

import pytest

from model_prediction.core.time_context import TimeContext
from model_prediction.domain import MarketType
from model_prediction.sports.base import SportAdapter, SportRegistry


class DummyAdapter:
    @property
    def sport_name(self) -> str:
        return "dummy_sport"

    @property
    def supported_markets(self) -> tuple[MarketType, ...]:
        return (MarketType.MONEYLINE, MarketType.SPREAD)

    def fetch_slate(self, context: TimeContext, **kwargs: Any) -> list[Any]:
        return [{"id": "game1"}]

    def build_features(self, slate: list[Any], context: TimeContext) -> Any:
        return {"features": [1.0, 2.0]}

    def generate_probabilities(self, features: Any, context: TimeContext) -> list[dict[str, Any]]:
        return [{"prob": 0.55}]

    def grade_event(
        self,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        return {"result": "win"}


@pytest.mark.unit
def test_sport_adapter_protocol_conformance():
    adapter = DummyAdapter()
    assert isinstance(adapter, SportAdapter)


@pytest.mark.unit
def test_sport_registry_operations():
    SportRegistry.clear()
    adapter = DummyAdapter()

    assert SportRegistry.has("dummy_sport") is False
    SportRegistry.register(adapter)
    assert SportRegistry.has("dummy_sport") is True
    assert SportRegistry.has("DUMMY_SPORT") is True  # Case insensitive

    retrieved = SportRegistry.get("DUMMY_SPORT")
    assert retrieved is adapter
    assert "dummy_sport" in SportRegistry.list_sports()

    SportRegistry.clear()
    assert SportRegistry.has("dummy_sport") is False
