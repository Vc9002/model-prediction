"""Shared real chronological OOF generation for MLB model-family comparison
scripts.

Real gap this closes: train_mlb_calibration_comparison.py (Task 14) and
train_mlb_calibrated_ensemble_comparison.py (Task 15) both need the
identical real out-of-fold moneyline predictions for the same three model
families (two_head, xgb_two_head, xgb_direct) on the same real
date-cluster-safe folds -- exactly the kind of independently-rebuilt-loop
drift Task 4 eliminated for the historical dataset builder itself, just one
layer up, for which models get compared. One shared function here instead
of two near-identical copies.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import polars as pl

from .mlb_features import MLB_DIFFERENTIAL_FEATURES, MLB_INTENSITY_FEATURES
from .models import MLBTwoHeadModel, XGBoostTwoHeadModel
from .xgboost_stress import nested_xgboost_fold

INTENSITY_FEATURES = MLB_INTENSITY_FEATURES
DIFFERENTIAL_FEATURES = MLB_DIFFERENTIAL_FEATURES
XGB_DIRECT_FEATURES = list(dict.fromkeys(INTENSITY_FEATURES + DIFFERENTIAL_FEATURES))

MODEL_NAMES = ("two_head", "xgb_two_head", "xgb_direct")


def _home_win_labels(df: pl.DataFrame) -> list[int]:
    return [1 if r["home_score"] > r["away_score"] else 0 for r in df.iter_rows(named=True)]


def _cohort(row: dict) -> dict[str, str]:
    """Real data-quality cohort tags for one row -- diagnostic only."""
    both_starters = row.get("starters_known", 0.0) == 1.0
    return {
        "starters": "both_available" if both_starters else "one_or_both_missing",
        "weather": "available" if row.get("weather_availability", 0.0) == 1.0 else "unavailable",
    }


def build_mlb_moneyline_oof(features: pl.DataFrame, folds) -> dict[str, dict[str, list]]:
    """Real chronological OOF moneyline predictions for all three real
    model families, using the identical date-cluster-safe folds every
    real training script in this codebase uses. Returns
    {model_name: {"probs": [...], "labels": [...], "cohorts": [...]}}
    with every list in real chronological order -- required by any
    downstream chronological cross-fitting (calibration or ensemble)."""
    oof: dict[str, dict[str, list]] = {
        name: {"probs": [], "labels": [], "cohorts": []} for name in MODEL_NAMES
    }

    for fold in folds:
        train_df = features.filter(pl.col("game_date") <= fold.train_end)
        val_df = features.filter(
            (pl.col("game_date") >= fold.val_start) & (pl.col("game_date") <= fold.val_end)
        )
        if train_df.height < 10 or val_df.height < 3:
            continue

        y_val_fold = _home_win_labels(val_df)
        val_rows = list(val_df.iter_rows(named=True))
        cohorts = [_cohort(r) for r in val_rows]

        two_head = MLBTwoHeadModel(seed=42)
        two_head.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        two_head_probs = [two_head.predict_row(r["event_id"], r).home_win_prob for r in val_rows]

        xgb_two_head = XGBoostTwoHeadModel(seed=42)
        xgb_two_head.fit(train_df, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        xgb_two_head_probs = [xgb_two_head.predict_row(r["event_id"], r).home_win_prob for r in val_rows]

        X_train = train_df.select(XGB_DIRECT_FEATURES).to_numpy()
        y_train_arr = (
            train_df.select((pl.col("home_score") > pl.col("away_score")).cast(pl.Int8).alias("y"))
            .to_numpy()
            .ravel()
        )
        X_val = val_df.select(XGB_DIRECT_FEATURES).to_numpy()
        nested_result = nested_xgboost_fold(
            X_train, y_train_arr, X_val, np.array(y_val_fold), fold_index=fold.fold_index
        )

        oof["two_head"]["probs"].extend(two_head_probs)
        oof["xgb_two_head"]["probs"].extend(xgb_two_head_probs)
        oof["xgb_direct"]["probs"].extend(nested_result.outer_probs)
        for name in oof:
            oof[name]["labels"].extend(y_val_fold)
            oof[name]["cohorts"].extend(cohorts)

    return oof


HEAD_FACTORIES: dict[str, Callable[..., Any]] = {
    "sklearn": MLBTwoHeadModel,
    "xgboost": XGBoostTwoHeadModel,
}


def build_mlb_coherent_oof_for_combo(
    features: pl.DataFrame,
    folds,
    head_family: str,
    method: str,
    intensity_features: list[str] | None = None,
    differential_features: list[str] | None = None,
) -> dict[str, list]:
    """Real chronological OOF moneyline predictions for one exact
    (head_family, distribution method) combination -- e.g.
    ("xgboost", "negative_binomial"). Used by the real Cartesian
    head-family x distribution comparison: `build_mlb_moneyline_oof()`
    only ever exercises each head family against its constructor's
    default distribution (independent_poisson for both), so it cannot by
    itself validate a combination like XGBoost heads + negative binomial
    -- that combination must be actually fit and OOF-scored, not
    assembled after the fact from two separate single-family
    experiments. `intensity_features`/`differential_features` default to
    the real MLB feature schema (module-level constants) but are
    overridable so this can be exercised against a small synthetic
    dataset in tests. Returns {"probs": [...], "labels": [...]} in real
    chronological order."""
    intensity_features = intensity_features if intensity_features is not None else INTENSITY_FEATURES
    differential_features = (
        differential_features if differential_features is not None else DIFFERENTIAL_FEATURES
    )
    factory = HEAD_FACTORIES[head_family]
    result: dict[str, list] = {"probs": [], "labels": []}
    for fold in folds:
        train_df = features.filter(pl.col("game_date") <= fold.train_end)
        val_df = features.filter(
            (pl.col("game_date") >= fold.val_start) & (pl.col("game_date") <= fold.val_end)
        )
        if train_df.height < 10 or val_df.height < 3:
            continue
        y_val_fold = _home_win_labels(val_df)
        val_rows = list(val_df.iter_rows(named=True))

        model = factory(seed=42, method=method)
        model.fit(train_df, intensity_features, differential_features)
        probs = [model.predict_row(r["event_id"], r).home_win_prob for r in val_rows]

        result["probs"].extend(probs)
        result["labels"].extend(y_val_fold)
    return result
