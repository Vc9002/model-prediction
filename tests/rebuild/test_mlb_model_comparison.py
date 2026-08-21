"""Tests for the shared real chronological OOF generation used by the MLB
model-comparison scripts (mlb_model_comparison.py), including the Cartesian
head-family x distribution combination generator that closes a real gap:
build_mlb_moneyline_oof() only ever exercises each head family against its
constructor's default distribution (independent_poisson for both), so a
combination like XGBoost heads + negative binomial was never actually fit
and OOF-scored anywhere -- build_mlb_coherent_oof_for_combo() does that
directly, for exactly the combination requested.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from model_prediction.rebuild.mlb_model_comparison import build_mlb_coherent_oof_for_combo
from model_prediction.rebuild.validation import expanding_folds


def _synthetic_dataset(n_days: int = 30, games_per_day: int = 4, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for day in range(n_days):
        date = f"2026-01-{day + 1:02d}"
        for g in range(games_per_day):
            f1 = rng.uniform(3, 6)
            f2 = rng.uniform(3, 6)
            g1 = rng.uniform(-2, 2)
            g2 = rng.uniform(-1, 1)
            total_runs = f1 + f2 + rng.normal(0, 0.5)
            home_margin = g1 + g2 + rng.normal(0, 0.5)
            home_score = float(max(0, round((total_runs + home_margin) / 2)))
            away_score = float(max(0, round((total_runs - home_margin) / 2)))
            rows.append(
                {
                    "event_id": f"e{day}_{g}",
                    "game_date": date,
                    "f1": f1,
                    "f2": f2,
                    "g1": g1,
                    "g2": g2,
                    "total_runs": total_runs,
                    "home_margin": home_margin,
                    "home_score": home_score,
                    "away_score": away_score,
                }
            )
    return pl.DataFrame(rows)


class TestBuildMlbCoherentOofForCombo:
    def _folds(self, features: pl.DataFrame):
        dates = features["game_date"].to_list()
        return expanding_folds(dates, n_splits=2, val_size=4, gap=0, test_size=4)

    def test_sklearn_head_negative_binomial_real_combo(self):
        features = _synthetic_dataset()
        folds = self._folds(features)
        assert folds, "real synthetic dataset must produce at least one real fold"
        result = build_mlb_coherent_oof_for_combo(
            features,
            folds,
            "sklearn",
            "negative_binomial",
            intensity_features=["f1", "f2"],
            differential_features=["g1", "g2"],
        )
        assert len(result["probs"]) == len(result["labels"])
        assert len(result["probs"]) > 0
        assert all(0.0 <= p <= 1.0 for p in result["probs"])

    def test_xgboost_head_negative_binomial_real_combo(self):
        features = _synthetic_dataset(seed=1)
        folds = self._folds(features)
        assert folds
        result = build_mlb_coherent_oof_for_combo(
            features,
            folds,
            "xgboost",
            "negative_binomial",
            intensity_features=["f1", "f2"],
            differential_features=["g1", "g2"],
        )
        assert len(result["probs"]) == len(result["labels"])
        assert len(result["probs"]) > 0
        assert all(0.0 <= p <= 1.0 for p in result["probs"])

    def test_different_distributions_for_the_same_head_family_produce_different_real_predictions(self):
        features = _synthetic_dataset(seed=2)
        folds = self._folds(features)
        assert folds
        kwargs = {"intensity_features": ["f1", "f2"], "differential_features": ["g1", "g2"]}
        poisson_result = build_mlb_coherent_oof_for_combo(
            features, folds, "xgboost", "independent_poisson", **kwargs
        )
        nb_result = build_mlb_coherent_oof_for_combo(
            features, folds, "xgboost", "negative_binomial", **kwargs
        )
        # Same real fitted heads (identical seed/features/folds), different
        # distribution reconciliation -- the real point of this combo
        # generator existing at all. Not required to differ by much, but
        # must not be a silent no-op (identical output regardless of
        # `method`) -- that would mean `method` was never actually wired
        # through to the distribution.
        assert poisson_result["probs"] != nb_result["probs"]
