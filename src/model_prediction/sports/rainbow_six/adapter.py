"""Rainbow Six modular sport adapter conforming to SportAdapter protocol."""

from __future__ import annotations

from pathlib import Path

from model_prediction.sports.base import SportRegistry
from model_prediction.sports.esports_base import BaseEsportsAdapter


class RainbowSixAdapter(BaseEsportsAdapter):
    """Modular forecasting and settlement adapter for Rainbow Six Siege."""

    def __init__(self, data_root: Path | str = "data") -> None:
        super().__init__(title="rainbow_six", data_root=data_root)


# Auto-register RainbowSixAdapter in SportRegistry
SportRegistry.register(RainbowSixAdapter())
