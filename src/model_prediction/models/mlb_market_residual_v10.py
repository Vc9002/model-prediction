"""MLB Moneyline Market-Residual Next-Generation Architecture (mlb-moneyline-market-residual-v10).

Architecture:
    logit(P_true) = logit(P_market) + f(X)

Where P_market is the devigged market probability prior, and f(X) learns the residual mispricing
across feature families:
1. Lineup information delta: projected vs confirmed wOBA, ISO, K%, missing regulars gap.
2. Starter information delta: starter change, velocity delta, CSW%, K-BB%, xwOBA allowed.
3. Bullpen: high-leverage availability, closer availability, 1d/2d/3d IP workload, freshness gap.
4. Matchup: platoon advantage, handedness matchup, pitch-mix matchup.
5. Context: weather change since open, rest disparity, travel fatigue gap.
6. Market dynamics: open to decision probability drift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MLB_RESIDUAL_V10_MODEL_VERSION = "mlb-moneyline-market-residual-v10"


def _logit(p: float, eps: float = 1e-6) -> float:
    p_c = max(eps, min(1.0 - eps, p))
    return math.log(p_c / (1.0 - p_c))


def _expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass(frozen=True)
class MLBResidualFeatures:
    """Standardized point-in-time delta feature families."""

    # 1. Market prior
    market_fair_prob_home: float
    market_open_prob_home: float

    # 2. Lineup delta
    lineup_woba_delta_home: float = 0.0
    lineup_woba_delta_away: float = 0.0
    missing_regulars_gap: float = 0.0

    # 3. Starter delta
    starter_changed_home: bool = False
    starter_changed_away: bool = False
    starter_csw_delta: float = 0.0
    starter_xwoba_allowed_delta: float = 0.0

    # 4. Bullpen
    bullpen_freshness_gap: float = 0.0
    closer_available_gap: float = 0.0
    bullpen_workload_3d_gap: float = 0.0

    # 5. Context
    weather_change_temp: float = 0.0
    rest_gap_days: int = 0
    travel_distance_gap_km: float = 0.0


@dataclass(frozen=True)
class MLBResidualForecast:
    """Forecast output combining market prior and residual shrinkage."""

    market_prob_home: float
    residual_adjustment_logit: float
    p_home_win: float
    p_away_win: float
    edge_vs_market: float
    family_contributions: dict[str, float]


class MLBMarketResidualV10Model:
    """Market-residual logistic learning engine."""

    version: str = MLB_RESIDUAL_V10_MODEL_VERSION

    def __init__(
        self,
        lineup_weight: float = 0.35,
        starter_weight: float = 0.40,
        bullpen_weight: float = 0.25,
        context_weight: float = 0.15,
        l2_shrinkage: float = 0.85,
    ) -> None:
        self.lineup_weight = lineup_weight
        self.starter_weight = starter_weight
        self.bullpen_weight = bullpen_weight
        self.context_weight = context_weight
        self.l2_shrinkage = l2_shrinkage

    def forecast_matchup(self, features: MLBResidualFeatures) -> MLBResidualForecast:
        """Compute posterior win probability via market logit + f(X)."""
        prior_logit = _logit(features.market_fair_prob_home)

        # 1. Lineup contribution
        lineup_eff = (features.lineup_woba_delta_home - features.lineup_woba_delta_away) * 3.2
        lineup_eff -= features.missing_regulars_gap * 0.08
        contrib_lineup = self.lineup_weight * lineup_eff

        # 2. Starter contribution
        starter_eff = features.starter_csw_delta * 2.5 - features.starter_xwoba_allowed_delta * 3.0
        if features.starter_changed_home:
            starter_eff -= 0.12
        if features.starter_changed_away:
            starter_eff += 0.12
        contrib_starter = self.starter_weight * starter_eff

        # 3. Bullpen contribution
        bp_eff = (
            features.bullpen_freshness_gap * 0.15
            + features.closer_available_gap * 0.08
            - features.bullpen_workload_3d_gap * 0.03
        )
        contrib_bullpen = self.bullpen_weight * bp_eff

        # 4. Context contribution
        ctx_eff = features.rest_gap_days * 0.04 - (features.travel_distance_gap_km / 1000.0) * 0.02
        contrib_context = self.context_weight * ctx_eff

        # Net residual logit adjustment with L2 shrinkage
        raw_residual = contrib_lineup + contrib_starter + contrib_bullpen + contrib_context
        shrunk_residual = raw_residual * self.l2_shrinkage

        # Posterior probability
        posterior_logit = prior_logit + shrunk_residual
        p_home = _expit(posterior_logit)
        p_away = 1.0 - p_home

        return MLBResidualForecast(
            market_prob_home=round(features.market_fair_prob_home, 4),
            residual_adjustment_logit=round(shrunk_residual, 4),
            p_home_win=round(p_home, 4),
            p_away_win=round(p_away, 4),
            edge_vs_market=round(p_home - features.market_fair_prob_home, 4),
            family_contributions={
                "lineup": round(contrib_lineup, 4),
                "starter": round(contrib_starter, 4),
                "bullpen": round(contrib_bullpen, 4),
                "context": round(contrib_context, 4),
            },
        )
