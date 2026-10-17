"""NBA Spread Models."""

from model_prediction.models.basketball_leagues import NBAModel
from model_prediction.models.market_models import spread_model

__all__ = [
    "NBAModel",
    "spread_model",
]
