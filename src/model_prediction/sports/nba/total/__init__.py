"""NBA Total (Over/Under) Models."""

from model_prediction.models.basketball_leagues import NBAModel
from model_prediction.models.market_models import total_model
from model_prediction.models.nba_total_v6 import NBATotalModelV6

__all__ = [
    "NBAModel",
    "NBATotalModelV6",
    "total_model",
]
