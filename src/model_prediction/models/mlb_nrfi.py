"""MLB Yes Run First Inning (YRFI) / No Run First Inning (NRFI) model.

Combines:
1. Component Poisson run-probability decomposition (half-top and half-bottom 1st inning).
2. Supervised Logistic Regression over starting pitcher priors, top-3 batter offense, and park/weather.
3. Fair-odds conversion, expected value (EV), and edge calculation vs sportsbook/prediction market quotes.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..features.yrfi_nrfi import compute_nrfi_features
from ..pricing import implied_probability, probability_to_american


@dataclass
class NRFIPrediction:
    p_nrfi: float
    p_yrfi: float
    fair_american_nrfi: int
    fair_american_yrfi: int
    half_top_expected_runs: float
    half_bot_expected_runs: float
    total_first_inning_expected_runs: float
    features: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "p_nrfi": self.p_nrfi,
            "p_yrfi": self.p_yrfi,
            "fair_american_nrfi": self.fair_american_nrfi,
            "fair_american_yrfi": self.fair_american_yrfi,
            "half_top_expected_runs": self.half_top_expected_runs,
            "half_bot_expected_runs": self.half_bot_expected_runs,
            "total_first_inning_expected_runs": self.total_first_inning_expected_runs,
            "features": self.features,
        }


class MLBNRFIModel:
    """Predictive model for MLB 1st-Inning Run Scoring (NRFI / YRFI)."""

    def __init__(
        self,
        *,
        model_version: str = "mlb-nrfi-v1",
        weights: dict[str, float] | None = None,
        intercept: float = 0.50,
        decomposed_blend_weight: float = 0.50,
    ) -> None:
        self.model_version = model_version
        self.intercept = intercept
        self.decomposed_blend_weight = decomposed_blend_weight
        self.weights = weights or {
            "home_sp_nrfi_rate": 0.12,
            "away_sp_nrfi_rate": 0.08,
            "home_sp_fip": -0.045,
            "away_sp_fip": -0.045,
            "home_sp_k_rate": 0.20,
            "away_sp_k_rate": 0.15,
            "park_factor": -0.50,
            "nrfi_decomposed_prob": 0.25,
        }

    def predict(
        self,
        home_team: str,
        away_team: str,
        decision: datetime,
        *,
        home_starter_id: int | None = None,
        away_starter_id: int | None = None,
        home_top3_ids: list[int] | None = None,
        away_top3_ids: list[int] | None = None,
        snapshot_path: str | Path | None = None,
        weather_factor: float = 1.0,
    ) -> NRFIPrediction:
        kwargs = {}
        if snapshot_path is not None:
            kwargs["snapshot_path"] = snapshot_path

        feats = compute_nrfi_features(
            home_team=home_team,
            away_team=away_team,
            decision=decision,
            home_starter_id=home_starter_id,
            away_starter_id=away_starter_id,
            home_top3_ids=home_top3_ids,
            away_top3_ids=away_top3_ids,
            weather_factor=weather_factor,
            **kwargs,
        )

        # 1. Decomposed Poisson probability
        p_decomposed = feats.nrfi_decomposed_prob

        # 2. Linear logit probability
        logit = self.intercept
        feat_dict = asdict(feats)
        for k, w in self.weights.items():
            if k in feat_dict:
                logit += w * float(feat_dict[k])
        p_logit = 1.0 / (1.0 + math.exp(-max(-10.0, min(10.0, logit))))

        # 3. Blended probability estimate
        w_d = self.decomposed_blend_weight
        p_nrfi = round(w_d * p_decomposed + (1.0 - w_d) * p_logit, 4)
        p_nrfi = max(0.20, min(0.80, p_nrfi))
        p_yrfi = round(1.0 - p_nrfi, 4)

        fair_nrfi = probability_to_american(p_nrfi)
        fair_yrfi = probability_to_american(p_yrfi)

        total_1st_runs = round(feats.half_top_expected_runs + feats.half_bot_expected_runs, 4)

        return NRFIPrediction(
            p_nrfi=p_nrfi,
            p_yrfi=p_yrfi,
            fair_american_nrfi=fair_nrfi,
            fair_american_yrfi=fair_yrfi,
            half_top_expected_runs=feats.half_top_expected_runs,
            half_bot_expected_runs=feats.half_bot_expected_runs,
            total_first_inning_expected_runs=total_1st_runs,
            features=feat_dict,
        )

    def evaluate_edge(
        self,
        prediction: NRFIPrediction,
        *,
        market_nrfi_american: int | None = None,
        market_yrfi_american: int | None = None,
        market_nrfi_prob: float | None = None,
        market_yrfi_prob: float | None = None,
    ) -> dict[str, Any]:
        """Evaluate betting edge and expected value against market quotes."""
        if market_nrfi_american is not None and market_nrfi_prob is None:
            market_nrfi_prob = implied_probability(market_nrfi_american)
        if market_yrfi_american is not None and market_yrfi_prob is None:
            market_yrfi_prob = implied_probability(market_yrfi_american)

        nrfi_edge = (prediction.p_nrfi - market_nrfi_prob) if market_nrfi_prob is not None else None
        yrfi_edge = (prediction.p_yrfi - market_yrfi_prob) if market_yrfi_prob is not None else None

        best_side = None
        best_edge = 0.0
        if nrfi_edge is not None and nrfi_edge > best_edge:
            best_side = "NRFI"
            best_edge = nrfi_edge
        if yrfi_edge is not None and yrfi_edge > best_edge:
            best_side = "YRFI"
            best_edge = yrfi_edge

        return {
            "model_p_nrfi": prediction.p_nrfi,
            "model_p_yrfi": prediction.p_yrfi,
            "market_nrfi_prob": round(market_nrfi_prob, 4) if market_nrfi_prob else None,
            "market_yrfi_prob": round(market_yrfi_prob, 4) if market_yrfi_prob else None,
            "nrfi_edge": round(nrfi_edge, 4) if nrfi_edge is not None else None,
            "yrfi_edge": round(yrfi_edge, 4) if yrfi_edge is not None else None,
            "recommended_side": best_side,
            "recommended_edge": round(best_edge, 4) if best_side else 0.0,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "intercept": self.intercept,
            "decomposed_blend_weight": self.decomposed_blend_weight,
            "weights": self.weights,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MLBNRFIModel:
        return cls(
            model_version=data.get("model_version", "mlb-nrfi-v1"),
            intercept=data.get("intercept", 0.0),
            decomposed_blend_weight=data.get("decomposed_blend_weight", 0.50),
            weights=data.get("weights"),
        )
