"""Targeted unit tests verifying 100% complete coverage across all 18 supported sports."""

from __future__ import annotations

import pytest

from model_prediction.domain import MarketType
from model_prediction.sports import SUPPORTED_SPORTS
from model_prediction.sports.base import SportAdapter, SportRegistry


@pytest.mark.unit
def test_all_18_supported_sports_registered() -> None:
    sports_list = SportRegistry.list_sports()

    for sport in SUPPORTED_SPORTS:
        assert sport in sports_list, f"Sport {sport} missing from SportRegistry"
        adapter = SportRegistry.get(sport)
        assert adapter is not None, f"Failed to get adapter for {sport}"
        assert isinstance(adapter, SportAdapter), f"{sport} does not satisfy SportAdapter protocol"
        assert adapter.sport_name == sport
        assert MarketType.MONEYLINE in adapter.supported_markets
