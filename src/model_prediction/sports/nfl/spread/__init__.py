"""NFL Spread Models."""

from model_prediction.models.nfl_spread_v6 import NFLSpreadForecast, NFLSpreadModelV6
from model_prediction.models.nfl_structural_v5 import NFLStructuralForecast, NFLStructuralV5Engine

__all__ = [
    "NFLSpreadForecast",
    "NFLSpreadModelV6",
    "NFLStructuralForecast",
    "NFLStructuralV5Engine",
]
