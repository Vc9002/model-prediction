"""Market residual model — learns whether the model-market disagreement is genuine.

Inputs: calibrated probability, market no-vig, spread, depth, quote age, time to start.
Target: positive cost-adjusted return, closing-line improvement, or genuine edge probability.

Never rewrites the independent sports probability. Market isolation is absolute.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


def _logit(p: float) -> float:
    clipped = max(1e-12, min(1 - 1e-12, p))
    return np.log(clipped / (1 - clipped))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + np.exp(-x))
    return np.exp(x) / (1.0 + np.exp(x))


@dataclass
class MarketResidualFeatures:
    """Features for the market residual model — market-side only, no sport probability mutation."""

    logit_model: float  # logit(calibrated sport probability)
    logit_market: float  # logit(market no-vig probability)
    spread: float  # bid-ask spread in probability units
    depth_ask: float  # available size on the ask
    quote_age_seconds: float  # seconds since quote was observed
    time_to_start_hours: float  # hours until event starts
    model_uncertainty: float  # model's own uncertainty estimate
    horizon: str = "mid"  # early/mid/late

    def to_array(self) -> np.ndarray:
        return np.array(
            [
                self.logit_model,
                self.logit_market,
                self.spread,
                min(self.depth_ask, 10000),
                min(self.quote_age_seconds, 3600),
                self.time_to_start_hours,
                self.model_uncertainty,
            ]
        )


class MarketResidualModel:
    """Logistic regression on chronological out-of-fold data.

    Predicts probability that the model-market disagreement leads to positive
    cost-adjusted return. Never touches the independent sports probability.

    Usage:
        residual = MarketResidualModel()
        residual.fit(oof_features, oof_labels)
        genuine_prob = residual.predict(features)
    """

    MODEL_VERSION = "market-residual-v2"

    def __init__(self) -> None:
        self.model = LogisticRegression(l1_ratio=0, C=1.0, solver="lbfgs", max_iter=1000)
        self.scaler = StandardScaler()
        self._fitted = False

    def fit(
        self,
        features: Sequence[MarketResidualFeatures],
        labels: Sequence[int],  # 1 = positive cost-adjusted return, 0 = negative
    ) -> MarketResidualModel:
        X = np.array([f.to_array() for f in features])
        y = np.array(labels)
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled, y)
        self._fitted = True
        return self

    def predict_genuine_edge_prob(self, features: MarketResidualFeatures) -> float:
        """Probability that the model-market difference represents a genuine edge."""
        if not self._fitted:
            return 0.5
        X_scaled = self.scaler.transform(features.to_array().reshape(1, -1))
        return float(self.model.predict_proba(X_scaled)[0, 1])

    def should_trade(
        self,
        features: MarketResidualFeatures,
        threshold: float = 0.6,
    ) -> tuple[bool, float]:
        """Trade decision: genuine edge probability must exceed threshold."""
        prob = self.predict_genuine_edge_prob(features)
        return prob >= threshold, prob


# ── Executable edge calculation ──────────────────────────────────────────────


def executable_edge(
    model_prob: float,
    conservative_prob: float,
    best_ask: float,
    spread: float = 0.0,
    fee_rate: float = 0.0,
) -> dict[str, float]:
    """Calculate the cost-adjusted edge against a real executable ask.

    Args:
        model_prob: the model's probability for the selection
        conservative_prob: lower-bound estimate (model - uncertainty)
        best_ask: the best executable ask price (in probability units, e.g., 0.55)
        spread: bid-ask spread in probability units
        fee_rate: platform fee rate (e.g., 0.0 for Polymarket)

    Returns dict with raw_edge, cost_adjusted_edge, expected_value.
    """
    raw_edge = model_prob - best_ask
    cost_adjusted = conservative_prob - best_ask - spread * 0.5 - fee_rate
    # For a binary contract bought at price best_ask, expected profit = p - c
    ev = conservative_prob - best_ask - fee_rate
    return {
        "raw_edge": float(raw_edge),
        "cost_adjusted_edge": float(cost_adjusted),
        "expected_value": float(ev),
        "model_prob": model_prob,
        "conservative_prob": conservative_prob,
        "best_ask": best_ask,
        "spread": spread,
        "fee_rate": fee_rate,
    }


def is_tradeable(edge_result: dict[str, float], min_edge: float = 0.02) -> bool:
    """A trade is only paper-simulatable when the cost-adjusted edge clears the minimum."""
    return edge_result["cost_adjusted_edge"] >= min_edge
