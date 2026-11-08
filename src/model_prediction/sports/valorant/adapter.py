"""Valorant modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

from pathlib import Path

from model_prediction.sports.base import SportRegistry
from model_prediction.sports.esports_base import BaseEsportsAdapter


class ValorantAdapter(BaseEsportsAdapter):
    """Modular forecasting and settlement adapter for Valorant."""

    def __init__(self, data_root: Path | str = "data") -> None:
        super().__init__(title="valorant", data_root=data_root)


# Auto-register ValorantAdapter in SportRegistry
SportRegistry.register(ValorantAdapter())
