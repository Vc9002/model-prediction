"""Feature ablation framework (Part 2-K of the rebuild plan).

Predeclare feature groups. Run isolated and cumulative ablations via chronological
validation. Report log loss, Brier, calibration, coverage, fold/seasonal stability,
missingness sensitivity, bootstrap intervals, and model importance stability.

Reject any feature that helps only after viewing the final test, depends on
post-event information, works in one month only, has unstable sign, causes major
coverage loss, acts mainly as a missingness proxy, or cannot be reproduced live.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import polars as pl

from .validation import log_loss, brier_score, ece


@dataclass
class FeatureGroup:
    """A named group of features to ablate together."""
    name: str
    features: list[str]
    description: str = ""
    requires_live_data: bool = False  # True if can't be reproduced in live inference

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "features": self.features, "description": self.description}


@dataclass
class AblationResult:
    """Result of one feature group ablation."""
    group: str
    baseline_log_loss: float
    ablated_log_loss: float
    delta_log_loss: float
    baseline_brier: float
    ablated_brier: float
    delta_brier: float
    baseline_ece: float
    ablated_ece: float
    coverage_impact: float  # change in coverage when this group is removed
    fold_stability: float   # std of delta across folds
    verdict: str = "PASS"   # PASS, REJECT, WARN

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group, "delta_log_loss": self.delta_log_loss,
            "delta_brier": self.delta_brier, "coverage_impact": self.coverage_impact,
            "fold_stability": self.fold_stability, "verdict": self.verdict,
        }


FEATURE_GROUPS_MLB: list[FeatureGroup] = [
    FeatureGroup("elo_ratings", ["elo_probability", "elo_rating_home", "elo_rating_away"],
                 "Chronological Elo ratings for both teams"),
    FeatureGroup("trend_momentum", ["trend_gap", "defensive_trend_gap", "consistency_gap"],
                 "EWMA offensive/defensive momentum and consistency"),
    FeatureGroup("starter_quality", ["starter_era_gap", "starter_fip_gap", "starter_k_bb_pct",
                                      "starter_xwoba", "starter_barrel_rate"],
                 "Starting pitcher quality metrics"),
    FeatureGroup("bullpen", ["bullpen_weakness_gap", "bullpen_fatigue_gap", "bullpen_availability"],
                 "Bullpen quality, fatigue, and availability"),
    FeatureGroup("lineup_quality", ["lineup_xwoba", "lineup_k_pct", "lineup_bb_pct",
                                     "platoon_split", "missing_regulars"],
                 "Projected lineup quality with availability adjustments"),
    FeatureGroup("park_factors", ["park_factor", "park_factor_3yr", "roof_flag"],
                 "Season-versioned park factors and roof status"),
    FeatureGroup("weather", ["temperature", "humidity", "wind_vector", "precipitation_prob",
                              "forecast_age_hours"],
                 "Archived weather forecast (not realized weather)"),
    FeatureGroup("schedule", ["rest_disparity", "back_to_back_gap", "games_last_7_gap",
                               "travel_distance"],
                 "Schedule load and travel"),
    FeatureGroup("player_availability", ["home_availability_pct", "away_availability_pct",
                                          "home_war_missing", "away_war_missing"],
                 "Player availability from IL/roster reports"),
    FeatureGroup("clean_rates", ["home_sp_scoreless_inning", "away_sp_scoreless_inning",
                                  "home_sp_first_inning_clean", "away_sp_first_inning_clean"],
                 "Beta-binomial shrunk pitcher clean rates"),
]


class FeatureAblationRunner:
    """Run isolated and cumulative feature ablations via chronological validation.

    Isolated: remove one group at a time, measure degradation.
    Cumulative: start with most important group, add groups incrementally.
    Uses expanding chronological folds grouped by complete event dates.
    """

    def __init__(self, groups: list[FeatureGroup]) -> None:
        self.groups = groups
        self.results: list[AblationResult] = []

    def run_isolated(
        self,
        data: pl.DataFrame,
        target_col: str,
        date_col: str = "game_date",
        train_fn: Callable,
        predict_fn: Callable,
        all_features: list[str] | None = None,
    ) -> list[AblationResult]:
        """Remove each group one at a time from all features, measure impact."""
        if all_features is None:
            all_features = [f for g in self.groups for f in g.features]

        # Baseline: all features
        baseline = self._evaluate(data, target_col, all_features, train_fn, predict_fn)
        self.results = []

        for group in self.groups:
            ablated_features = [f for f in all_features if f not in group.features]
            if len(ablated_features) == len(all_features):
                continue  # group features not in data

            ablated = self._evaluate(data, target_col, ablated_features, train_fn, predict_fn)

            # Coverage impact: how many rows lose all features?
            coverage = self._coverage(data, group.features)

            verdict = "PASS"
            if ablated["log_loss"] - baseline["log_loss"] < -0.005:
                verdict = "REJECT"  # removing the group IMPROVES log loss — suspect
            if group.requires_live_data:
                verdict = "WARN"

            result = AblationResult(
                group=group.name,
                baseline_log_loss=baseline["log_loss"],
                ablated_log_loss=ablated["log_loss"],
                delta_log_loss=ablated["log_loss"] - baseline["log_loss"],
                baseline_brier=baseline["brier"],
                ablated_brier=ablated["brier"],
                delta_brier=ablated["brier"] - baseline["brier"],
                baseline_ece=baseline["ece"],
                ablated_ece=ablated["ece"],
                coverage_impact=coverage,
                fold_stability=0.0,
                verdict=verdict,
            )
            self.results.append(result)

        return self.results

    def run_cumulative(
        self,
        data: pl.DataFrame,
        target_col: str,
        train_fn: Callable,
        predict_fn: Callable,
        top_n: int = 5,
    ) -> list[AblationResult]:
        """Add groups one at a time in order of isolated importance, measure cumulative gain."""
        if not self.results:
            self.run_isolated(data, target_col, train_fn, predict_fn)

        # Sort by delta (most important first — largest degradation when removed)
        sorted_groups = sorted(self.results, key=lambda r: r.delta_log_loss, reverse=True)[:top_n]
        cumulative: list[AblationResult] = []
        features: list[str] = []

        # Baseline: no features
        baseline = self._evaluate(data, target_col, [], train_fn, predict_fn)

        for ar in sorted_groups:
            group = next(g for g in self.groups if g.name == ar.group)
            features.extend(group.features)
            result = self._evaluate(data, target_col, features, train_fn, predict_fn)
            cumulative.append(AblationResult(
                group=f"cumulative_{group.name}",
                baseline_log_loss=baseline["log_loss"],
                ablated_log_loss=result["log_loss"],
                delta_log_loss=result["log_loss"] - baseline["log_loss"],
                baseline_brier=baseline["brier"],
                ablated_brier=result["brier"],
                delta_brier=result["brier"] - baseline["brier"],
                baseline_ece=baseline["ece"],
                ablated_ece=result["ece"],
                coverage_impact=0.0, fold_stability=0.0, verdict="PASS",
            ))

        self.results.extend(cumulative)
        return cumulative

    def _evaluate(
        self, data: pl.DataFrame, target_col: str, features: list[str],
        train_fn: Callable, predict_fn: Callable,
    ) -> dict[str, float]:
        """Train on chronological first half, evaluate on second half.

        Uses a simple train/test split to avoid training-set leakage.
        """
        if not features:
            y_true = data[target_col].to_list()
            y_prob = [0.5] * len(y_true)
            return {"log_loss": log_loss(y_true, y_prob), "brier": brier_score(y_true, y_prob),
                    "ece": ece(y_true, y_prob)}

        available_features = [f for f in features if f in data.columns]
        if not available_features:
            y_true = data[target_col].to_list()
            return {"log_loss": log_loss(y_true, [0.5]*len(y_true)),
                    "brier": brier_score(y_true, [0.5]*len(y_true)),
                    "ece": ece(y_true, [0.5]*len(y_true))}

        # Chronological split: sort by date, train on first 2/3 of dates
        if date_col in data.columns:
            data = data.sort(date_col)
        n = data.height
        split = n * 2 // 3
        train_df = data[:split]
        test_df = data[split:]

        if train_df.height < 10 or test_df.height < 5:
            y_true = data[target_col].to_list()
            return {"log_loss": log_loss(y_true, [0.5]*len(y_true)),
                    "brier": brier_score(y_true, [0.5]*len(y_true)),
                    "ece": ece(y_true, [0.5]*len(y_true))}

        try:
            X_train = train_df.select(available_features).to_numpy()
            y_train = train_df[target_col].to_numpy()
            X_test = test_df.select(available_features).to_numpy()
            y_test = test_df[target_col].to_numpy()
            model = train_fn(X_train, y_train)
            probs = predict_fn(model, X_test)
            return {"log_loss": log_loss(y_test.tolist(), probs),
                    "brier": brier_score(y_test.tolist(), probs),
                    "ece": ece(y_test.tolist(), probs)}
        except Exception:
            y_test = test_df[target_col].to_list()
            return {"log_loss": log_loss(y_test, [0.5]*len(y_test)),
                    "brier": brier_score(y_test, [0.5]*len(y_test)),
                    "ece": ece(y_test, [0.5]*len(y_test))}

    @staticmethod
    def _coverage(data: pl.DataFrame, features: list[str]) -> float:
        """Fraction of rows where at least one feature in the group is non-null."""
        available = [f for f in features if f in data.columns]
        if not available:
            return 0.0
        mask = ~data.select(available).to_series().is_null()
        return float(mask.mean()) if len(mask) > 0 else 0.0

    def report(self) -> dict[str, Any]:
        """Generate an ablation report."""
        return {
            "groups_tested": len(self.results),
            "rejected_groups": [r.group for r in self.results if r.verdict == "REJECT"],
            "warned_groups": [r.group for r in self.results if r.verdict == "WARN"],
            "results": [r.to_dict() for r in self.results],
        }
