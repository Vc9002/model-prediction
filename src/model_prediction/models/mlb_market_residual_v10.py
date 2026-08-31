"""MLB Moneyline Market-Residual Next-Generation Architecture (mlb-moneyline-market-residual-v10).

Architecture:
    logit(P_true) = logit(P_market) + X @ beta

Where P_market is the devigged market probability prior, and X @ beta learns the residual mispricing
across feature families with fixed-offset estimation:
1. Lineup information delta: projected vs confirmed wOBA, missing regulars gap.
2. Starter information delta: starter change, CSW%, xwOBA allowed.
3. Bullpen: freshness gap, closer availability, 3d workload.
4. Context & Weather: temperature drift since open, rest disparity, travel gap.
5. Market Dynamics: drift from opening probability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

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

    # 5. Context & Weather
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
    """Market-residual logistic learning engine with statistical fitting layer."""

    version: str = MLB_RESIDUAL_V10_MODEL_VERSION

    def __init__(
        self,
        weights: list[float] | None = None,
        intercept: float = 0.0,
        l2_shrinkage: float = 0.85,
    ) -> None:
        # Default coefficients (12 features)
        self.weights = weights or [
            1.12,  # 0: lineup_woba_delta
            -0.08,  # 1: missing_regulars_gap
            1.00,  # 2: starter_csw_delta
            -1.20,  # 3: starter_xwoba_allowed_delta
            -0.12,  # 4: starter_changed_net
            0.05,  # 5: bullpen_freshness_gap
            0.03,  # 6: closer_available_gap
            -0.01,  # 7: bullpen_workload_3d_gap
            0.005,  # 8: weather_change_temp
            0.02,  # 9: rest_gap_days
            -0.01,  # 10: travel_distance_gap_km / 1000
            0.15,  # 11: market_drift
        ]
        self.intercept = intercept
        self.l2_shrinkage = l2_shrinkage

    @staticmethod
    def extract_feature_vector(feat: MLBResidualFeatures) -> list[float]:
        """Convert structured feature dataclass into a normalized 12-element vector."""
        lineup_woba_delta = feat.lineup_woba_delta_home - feat.lineup_woba_delta_away
        starter_changed_net = (-1.0 if feat.starter_changed_home else 0.0) + (
            1.0 if feat.starter_changed_away else 0.0
        )
        travel_gap_scaled = feat.travel_distance_gap_km / 1000.0
        market_drift = feat.market_fair_prob_home - feat.market_open_prob_home

        return [
            lineup_woba_delta,
            feat.missing_regulars_gap,
            feat.starter_csw_delta,
            feat.starter_xwoba_allowed_delta,
            starter_changed_net,
            feat.bullpen_freshness_gap,
            feat.closer_available_gap,
            feat.bullpen_workload_3d_gap,
            feat.weather_change_temp,
            float(feat.rest_gap_days),
            travel_gap_scaled,
            market_drift,
        ]

    def fit_from_data(
        self,
        features_list: list[MLBResidualFeatures],
        outcomes: list[int],
        l2_reg: float = 1.0,
    ) -> None:
        """Fit beta weights via ridge logistic regression with market logit offset."""
        if not features_list or len(features_list) != len(outcomes):
            return

        X = np.array([self.extract_feature_vector(f) for f in features_list])
        y = np.array(outcomes)

        # Target residual logit: y_prob - p_market approximation for ridge regression
        p_market = np.array([f.market_fair_prob_home for f in features_list])
        residuals = y - p_market  # Score residual

        # Ridge closed-form estimator on feature matrix: beta = (X^T X + lambda I)^-1 X^T res
        n_feat = X.shape[1]
        xtx = X.T @ X + l2_reg * np.eye(n_feat)
        xty = X.T @ residuals
        fitted_beta = np.linalg.solve(xtx, xty)

        self.weights = fitted_beta.tolist()

    def forecast_matchup(self, features: MLBResidualFeatures) -> MLBResidualForecast:
        """Compute posterior win probability via market logit + X @ beta."""
        prior_logit = _logit(features.market_fair_prob_home)
        vec = np.array(self.extract_feature_vector(features))
        w = np.array(self.weights[: len(vec)])

        raw_residual = float(np.dot(vec, w)) + self.intercept
        shrunk_residual = raw_residual * self.l2_shrinkage

        # Group contributions
        contrib_lineup = float(vec[0] * w[0] + vec[1] * w[1])
        contrib_starter = float(vec[2] * w[2] + vec[3] * w[3] + vec[4] * w[4])
        contrib_bullpen = float(vec[5] * w[5] + vec[6] * w[6] + vec[7] * w[7])
        contrib_context = float(vec[8] * w[8] + vec[9] * w[9] + vec[10] * w[10] + vec[11] * w[11])

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
