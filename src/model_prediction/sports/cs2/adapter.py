"""CS2 modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

from pathlib import Path

from model_prediction.sports.base import SportRegistry
from model_prediction.sports.esports_base import BaseEsportsAdapter


class CS2Adapter(BaseEsportsAdapter):
    """Modular forecasting and settlement adapter for Counter-Strike 2."""

    def __init__(self, data_root: Path | str = "data") -> None:
        super().__init__(title="cs2", data_root=data_root)


# Auto-register CS2Adapter in SportRegistry
SportRegistry.register(CS2Adapter())
