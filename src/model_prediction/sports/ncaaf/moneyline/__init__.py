"""NCAAF Moneyline Models."""

from model_prediction.models.cfb_structural_v2 import CFBStructuralForecast, CFBStructuralV2Model
from model_prediction.models.cfb_structural_v3 import (
    CFB_V3_MODEL_VERSION,
    CFBStructuralForecastV3,
    CFBStructuralV3Model,
    cfb_structural_v3_model,
)
from model_prediction.models.college_football import CollegeFootballModel, UpcomingCFBGame

__all__ = [
    "CFB_V3_MODEL_VERSION",
    "CFBStructuralForecast",
    "CFBStructuralForecastV3",
    "CFBStructuralV2Model",
    "CFBStructuralV3Model",
    "CollegeFootballModel",
    "UpcomingCFBGame",
    "cfb_structural_v3_model",
]
