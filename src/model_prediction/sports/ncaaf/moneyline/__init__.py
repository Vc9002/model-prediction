"""NCAAF Moneyline Models."""

from model_prediction.models.cfb_structural_v2 import CFBStructuralForecast, CFBStructuralV2Model
from model_prediction.models.college_football import CollegeFootballModel, UpcomingCFBGame

__all__ = [
    "CFBStructuralForecast",
    "CFBStructuralV2Model",
    "CollegeFootballModel",
    "UpcomingCFBGame",
]
