"""LoL Total (Map Total Over/Under) Models."""

from model_prediction.models.esports_derivatives import (
    EsportsBo3Derivatives,
    price_esports_bo3_derivatives,
)
from model_prediction.models.market_models import total_model

__all__ = [
    "EsportsBo3Derivatives",
    "price_esports_bo3_derivatives",
    "total_model",
]
