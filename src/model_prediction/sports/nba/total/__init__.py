"""NBA Total (Over/Under) Models."""

from model_prediction.models.basketball_leagues import NBAModel
from model_prediction.models.market_models import total_model
from model_prediction.models.nba_total_v6 import NBATotalModelV6
from model_prediction.models.nba_v7 import NBATotalModelV7

__all__ = [
    "NBAModel",
    "NBATotalModelV6",
    "NBATotalModelV7",
    "total_model",
]
