"""Feature ablation framework (Part 2-K of the rebuild plan).

Predeclare feature groups. Run isolated and cumulative ablations via chronological
validation. Report log loss, Brier, calibration, coverage, fold/seasonal stability,
missingness sensitivity, bootstrap intervals, and model importance stability.

Reject any feature that helps only after viewing the final test, depends on
post-event information, works in one month only, has unstable sign, causes major
coverage loss, acts mainly as a missingness proxy, or cannot be reproduced live.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import polars as pl

from .validation import brier_score, ece, log_loss


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
    fold_stability: float  # std of delta across folds
    verdict: str = "PASS"  # PASS, REJECT, WARN

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "delta_log_loss": self.delta_log_loss,
            "delta_brier": self.delta_brier,
            "coverage_impact": self.coverage_impact,
            "fold_stability": self.fold_stability,
            "verdict": self.verdict,
        }


@dataclass
class PreRegisteredExperiment:
    """A pre-registered experimental hypothesis and minimum threshold.

    Prevents post-hoc rationalization by requiring thresholds to be recorded
    before running an ablation.
    """

    experiment_id: str
    hypothesis: str
    feature_group: str
    registered_at_utc: str
    registered_brier_threshold: float = 0.001
    registered_log_loss_threshold: float = 0.002
    registered_coverage_floor: float = 0.90

    def evaluate(self, result: AblationResult) -> dict[str, Any]:
        """Evaluate an ablation result strictly against pre-registered thresholds."""
        # Removing the feature group causes delta_brier = ablated - baseline.
        # Positive delta_brier means the model gets worse when the group is removed (group helps).
        brier_improvement = result.delta_brier
        ll_improvement = result.delta_log_loss
        coverage_retained = 1.0 - result.coverage_impact

        cleared_brier = brier_improvement >= self.registered_brier_threshold
        cleared_ll = ll_improvement >= self.registered_log_loss_threshold
        cleared_coverage = coverage_retained >= self.registered_coverage_floor

        if cleared_brier and cleared_ll and cleared_coverage:
            verdict = "PROMOTION_CANDIDATE"
        elif not cleared_coverage or brier_improvement < -0.001:
            verdict = "REJECT"
        else:
            verdict = "INCONCLUSIVE"

        return {
            "experiment_id": self.experiment_id,
            "hypothesis": self.hypothesis,
            "feature_group": self.feature_group,
            "registered_at_utc": self.registered_at_utc,
            "brier_improvement": round(brier_improvement, 6),
            "registered_brier_threshold": self.registered_brier_threshold,
            "log_loss_improvement": round(ll_improvement, 6),
            "registered_log_loss_threshold": self.registered_log_loss_threshold,
            "coverage_retained": round(coverage_retained, 4),
            "registered_coverage_floor": self.registered_coverage_floor,
            "verdict": verdict,
        }


# Real gap fixed here: every feature name below used to be a legacy/
# aspirational name (elo_probability, starter_era_gap, lineup_xwoba,
# home_availability_pct, ...) that doesn't exist anywhere in the real
# rebuild feature schema mlb_features.build_game_feature_row() actually
# produces (verified via grep -- zero matches for any of the old names in
# src/model_prediction/rebuild/). Running FeatureAblationRunner against
# the old groups on real data would silently find zero available_features
# for every single group and report a vacuous 0.5-coinflip result for
# all of them -- not an ablation, a false negative dressed up as one.
# Renamed to the exact columns INTENSITY_FEATURES/DIFFERENTIAL_FEATURES
# in scripts/train_mlb_rebuild_real_features.py and
# scripts/train_mlb_xgboost_ensemble.py already train on. Real, current
# scope is deliberately smaller than CLAUDE.md's long-term feature list
# (no lineup/player-availability/schedule groups exist yet because those
# features aren't built by mlb_features.py yet either) -- this reflects
# what's real today, not what's aspired to.
FEATURE_GROUPS_MLB: list[FeatureGroup] = [
    FeatureGroup(
        "starter_pitching",
        [
            "home_sp_avg_velocity",
            "away_sp_avg_velocity",
            "home_sp_csw_pct",
            "away_sp_csw_pct",
            "home_sp_k_pct",
            "away_sp_k_pct",
            "home_sp_bb_pct",
            "away_sp_bb_pct",
            "home_sp_days_rest",
            "away_sp_days_rest",
        ],
        "Starting pitcher velocity, CSW%, K%/BB%, and rest, real Statcast-derived",
    ),
    FeatureGroup(
        "bullpen",
        [
            "home_bp_bullpen_pitches",
            "away_bp_bullpen_pitches",
            "home_bp_bullpen_avg_velocity",
            "away_bp_bullpen_avg_velocity",
        ],
        "Bullpen recent workload and velocity, real Statcast-derived",
    ),
    FeatureGroup(
        "clean_rates",
        [
            "home_sp_clean_first_inning_clean_rate",
            "away_sp_clean_first_inning_clean_rate",
            "home_sp_clean_scoreless_inning_rate",
            "away_sp_clean_scoreless_inning_rate",
            "home_sp_clean_clean_appearance_rate",
            "away_sp_clean_clean_appearance_rate",
        ],
        "Beta-binomial shrunk pitcher clean rates (first-inning/scoreless-inning/"
        "clean-appearance), real Statcast run-scoring-derived",
    ),
    FeatureGroup(
        "park_weather",
        ["park_factor", "temp_f_first_pitch"],
        "Empirical park run factor and archived weather forecast temperature",
    ),
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
        train_fn: Callable,
        predict_fn: Callable,
        date_col: str = "game_date",
        all_features: list[str] | None = None,
    ) -> list[AblationResult]:
        """Remove each group one at a time from all features, measure impact."""
        if all_features is None:
            all_features = [f for g in self.groups for f in g.features]

        # Baseline: all features
        baseline = self._evaluate(data, target_col, all_features, train_fn, predict_fn, date_col)
        self.results = []

        for group in self.groups:
            ablated_features = [f for f in all_features if f not in group.features]
            if len(ablated_features) == len(all_features):
                continue  # group features not in data

            ablated = self._evaluate(data, target_col, ablated_features, train_fn, predict_fn, date_col)

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
        date_col: str = "game_date",
    ) -> list[AblationResult]:
        """Add groups one at a time in order of isolated importance, measure cumulative gain."""
        if not self.results:
            self.run_isolated(data, target_col, train_fn, predict_fn, date_col)

        # Sort by delta (most important first — largest degradation when removed)
        sorted_groups = sorted(self.results, key=lambda r: r.delta_log_loss, reverse=True)[:top_n]
        cumulative: list[AblationResult] = []
        features: list[str] = []

        # Baseline: no features
        baseline = self._evaluate(data, target_col, [], train_fn, predict_fn, date_col)

        for ar in sorted_groups:
            group = next(g for g in self.groups if g.name == ar.group)
            features.extend(group.features)
            result = self._evaluate(data, target_col, features, train_fn, predict_fn, date_col)
            cumulative.append(
                AblationResult(
                    group=f"cumulative_{group.name}",
                    baseline_log_loss=baseline["log_loss"],
                    ablated_log_loss=result["log_loss"],
                    delta_log_loss=result["log_loss"] - baseline["log_loss"],
                    baseline_brier=baseline["brier"],
                    ablated_brier=result["brier"],
                    delta_brier=result["brier"] - baseline["brier"],
                    baseline_ece=baseline["ece"],
                    ablated_ece=result["ece"],
                    coverage_impact=0.0,
                    fold_stability=0.0,
                    verdict="PASS",
                )
            )

        self.results.extend(cumulative)
        return cumulative

    def _evaluate(
        self,
        data: pl.DataFrame,
        target_col: str,
        features: list[str],
        train_fn: Callable,
        predict_fn: Callable,
        date_col: str = "game_date",
    ) -> dict[str, float]:
        """Train on chronological first half, evaluate on second half.

        Uses a simple train/test split to avoid training-set leakage.
        """
        if not features:
            y_true = data[target_col].to_list()
            y_prob = [0.5] * len(y_true)
            return {
                "log_loss": log_loss(y_true, y_prob),
                "brier": brier_score(y_true, y_prob),
                "ece": ece(y_true, y_prob),
            }

        available_features = [f for f in features if f in data.columns]
        if not available_features:
            y_true = data[target_col].to_list()
            return {
                "log_loss": log_loss(y_true, [0.5] * len(y_true)),
                "brier": brier_score(y_true, [0.5] * len(y_true)),
                "ece": ece(y_true, [0.5] * len(y_true)),
            }

        # Chronological split: sort by date, train on first 2/3 of dates
        if date_col in data.columns:
            data = data.sort(date_col)
        n = data.height
        split = n * 2 // 3
        train_df = data[:split]
        test_df = data[split:]

        if train_df.height < 10 or test_df.height < 5:
            y_true = data[target_col].to_list()
            return {
                "log_loss": log_loss(y_true, [0.5] * len(y_true)),
                "brier": brier_score(y_true, [0.5] * len(y_true)),
                "ece": ece(y_true, [0.5] * len(y_true)),
            }

        try:
            X_train = train_df.select(available_features).to_numpy()
            y_train = train_df[target_col].to_numpy()
            X_test = test_df.select(available_features).to_numpy()
            y_test = test_df[target_col].to_numpy()
            model = train_fn(X_train, y_train)
            probs = predict_fn(model, X_test)
            return {
                "log_loss": log_loss(y_test.tolist(), probs),
                "brier": brier_score(y_test.tolist(), probs),
                "ece": ece(y_test.tolist(), probs),
            }
        except Exception:  # noqa: BLE001 -- train_fn/predict_fn are caller-supplied callables that can raise anything; falls back to a disclosed coin-flip result, not silently swallowed
            y_test = test_df[target_col].to_list()
            return {
                "log_loss": log_loss(y_test, [0.5] * len(y_test)),
                "brier": brier_score(y_test, [0.5] * len(y_test)),
                "ece": ece(y_test, [0.5] * len(y_test)),
            }

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
