"""NFL Total Models."""

from model_prediction.models.nfl_structural_v5 import NFLStructuralForecast, NFLStructuralV5Engine
from model_prediction.models.nfl_total_v6 import NFLTotalForecast, NFLTotalModelV6

__all__ = [
    "NFLStructuralForecast",
    "NFLStructuralV5Engine",
    "NFLTotalForecast",
    "NFLTotalModelV6",
]
