"""Soccer Spread (Asian & Goal Handicap) Models."""

from model_prediction.models.market_models import spread_model
from model_prediction.models.soccer_dixon_coles import (
    AsianHandicapLineResult,
    DixonColesEngine,
    DixonColesMatchPrediction,
)

__all__ = [
    "AsianHandicapLineResult",
    "DixonColesEngine",
    "DixonColesMatchPrediction",
    "spread_model",
]
