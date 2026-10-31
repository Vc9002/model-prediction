"""Basketball Total (Combined Points) Models."""

from model_prediction.models.basketball import (
    BasketballLeagueConfig,
    BasketballLeagueRegistry,
    DedicatedBasketballLeagueModel,
    EuroLeagueModel,
    NBAModel,
    NCAABModel,
    UpcomingGame,
    WNBAModel,
    WNCAABModel,
    euroleague_model,
    get_basketball_model,
    ncaab_model,
    wncaab_model,
)
from model_prediction.models.nba_total_v6 import NBATotalModelV6

__all__ = [
    "BasketballLeagueConfig",
    "BasketballLeagueRegistry",
    "DedicatedBasketballLeagueModel",
    "EuroLeagueModel",
    "NBAModel",
    "NBATotalModelV6",
    "NCAABModel",
    "UpcomingGame",
    "WNBAModel",
    "WNCAABModel",
    "euroleague_model",
    "get_basketball_model",
    "ncaab_model",
    "wncaab_model",
]
