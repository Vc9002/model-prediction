"""Research NFL joint score model with a dated, fair moneyline market anchor."""

from __future__ import annotations

from .nfl_joint_score_v1 import NFLJointScoreForecast, joint_score_forecast
from .nfl_market_anchor_v1 import market_anchored_probability

MODEL_VERSION = "nfl-joint-market-v2"


def forecast_with_market_anchor(
    incumbent_home_probability: float,
    market_home_probability: float,
    market_weight: float,
    margin_mean: float,
    margin_sd: float,
    total_mean: float,
    total_sd: float,
    league_avg_total: float,
) -> NFLJointScoreForecast:
    anchored_home = market_anchored_probability(
        incumbent_home_probability, market_home_probability, market_weight
    )
    return joint_score_forecast(anchored_home, margin_mean, margin_sd, total_mean, total_sd, league_avg_total)
