"""Nested chronological validation framework.

Expanding or rolling folds grouped by complete event dates. No random K-fold.
One untouched final test per model family. Date-cluster and team-cluster bootstrap.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, UTC
from typing import Any

import numpy as np
import polars as pl
from sklearn.model_selection import TimeSeriesSplit

# ── Fold definitions ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChronologicalFold:
    """One fold: train on dates < train_end, validate on val_dates, test (if supplied) untouched."""
    fold_index: int
    train_end: str          # ISO date — everything before this is train
    val_start: str          # ISO date
    val_end: str            # ISO date
    test_start: str | None = None   # separate economic test, not used in Part 2
    test_end: str | None = None


def expanding_folds(
    dates: Sequence[str],
    n_splits: int = 5,
    val_size: int = 30,     # days
    gap: int = 1,           # publication embargo
    test_size: int = 60,    # untouched final test
) -> list[ChronologicalFold]:
    """Expanding window: train grows, validation slides forward.
    Last `test_size` days are the untouched final test.
    """
    sorted_dates = sorted(set(dates))
    if len(sorted_dates) < val_size + test_size + gap:
        return []
    train_dates = sorted_dates[: -(val_size + test_size + gap)]
    folds: list[ChronologicalFold] = []
    step = max(1, len(train_dates) // n_splits)
    for i in range(n_splits):
        train_end_idx = min(len(train_dates), (i + 1) * step)
        val_start_idx = train_end_idx + gap
        val_end_idx = min(len(sorted_dates) - test_size, val_start_idx + val_size)
        if val_end_idx <= val_start_idx:
            break
        folds.append(ChronologicalFold(
            fold_index=i,
            train_end=train_dates[train_end_idx - 1],
            val_start=sorted_dates[val_start_idx],
            val_end=sorted_dates[val_end_idx - 1],
            test_start=sorted_dates[-test_size] if test_size > 0 else None,
            test_end=sorted_dates[-1] if test_size > 0 else None,
        ))
    return folds


def rolling_folds(
    dates: Sequence[str],
    n_splits: int = 5,
    train_size: int = 180,
    val_size: int = 30,
    gap: int = 1,
    test_size: int = 60,
) -> list[ChronologicalFold]:
    """Rolling window: fixed train size slides forward."""
    sorted_dates = sorted(set(dates))
    folds: list[ChronologicalFold] = []
    step = max(1, (len(sorted_dates) - train_size - val_size - test_size - gap) // n_splits)
    for i in range(n_splits):
        offset = i * step
        train_end_idx = offset + train_size
        val_start_idx = train_end_idx + gap
        val_end_idx = val_start_idx + val_size
        if val_end_idx >= len(sorted_dates) - test_size:
            break
        folds.append(ChronologicalFold(
            fold_index=i,
            train_end=sorted_dates[train_end_idx - 1],
            val_start=sorted_dates[val_start_idx],
            val_end=sorted_dates[val_end_idx - 1],
            test_start=sorted_dates[-test_size] if test_size > 0 else None,
            test_end=sorted_dates[-1] if test_size > 0 else None,
        ))
    return folds


# ── Bootstrap ────────────────────────────────────────────────────────────────


def date_cluster_bootstrap(
    values: Sequence[float],
    dates: Sequence[str],
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> dict[str, float]:
    """Bootstrap with date clusters (preserves within-day correlation)."""
    rng = np.random.default_rng(seed)
    date_groups: dict[str, list[float]] = defaultdict(list)
    for d, v in zip(dates, values, strict=True):
        date_groups[d].append(v)
    group_keys = list(date_groups.keys())
    means: list[float] = []
    for _ in range(n_bootstrap):
        sampled = rng.choice(group_keys, size=len(group_keys), replace=True)
        sample_vals = [v for k in sampled for v in date_groups[k]]
        if sample_vals:
            means.append(float(np.mean(sample_vals)))
    means_arr = np.array(means)
    return {
        "mean": float(np.mean(means_arr)),
        "ci_lower": float(np.percentile(means_arr, 2.5)),
        "ci_upper": float(np.percentile(means_arr, 97.5)),
        "std": float(np.std(means_arr)),
        "n_bootstrap": n_bootstrap,
    }


# ── Metrics ──────────────────────────────────────────────────────────────────


def log_loss(y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    """Binary log loss. Clips probabilities to [1e-15, 1-1e-15]."""
    eps = 1e-15
    total = 0.0
    for y, p in zip(y_true, y_prob, strict=True):
        p_safe = max(eps, min(1 - eps, p))
        total += y * math.log(p_safe) + (1 - y) * math.log(1 - p_safe)
    return float(-total / len(y_true))


def brier_score(y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    return float(np.mean((np.array(y_prob) - np.array(y_true)) ** 2))


def ece(y_true: Sequence[int], y_prob: Sequence[float], n_bins: int = 10) -> float:
    """Expected Calibration Error."""
    yt = np.array(y_true)
    yp = np.array(y_prob)
    bins = np.linspace(0, 1, n_bins + 1)
    ece_val = 0.0
    for i in range(n_bins):
        mask = (yp >= bins[i]) & (yp < bins[i + 1])
        if mask.sum() > 0:
            bin_acc = yt[mask].mean()
            bin_conf = yp[mask].mean()
            ece_val += (mask.sum() / len(yt)) * abs(bin_acc - bin_conf)
    return float(ece_val)


def calibration_curve(
    y_true: Sequence[int], y_prob: Sequence[float], n_bins: int = 10,
) -> dict[str, list[float]]:
    """Reliability curve: bin centers, actual fraction of positives."""
    yt = np.array(y_true)
    yp = np.array(y_prob)
    bins = np.linspace(0, 1, n_bins + 1)
    centers: list[float] = []
    actuals: list[float] = []
    counts: list[int] = []
    for i in range(n_bins):
        mask = (yp >= bins[i]) & (yp < bins[i + 1])
        if mask.sum() >= 0:
            centers.append(float((bins[i] + bins[i + 1]) / 2))
            actuals.append(float(yt[mask].mean()) if mask.sum() > 0 else float("nan"))
            counts.append(int(mask.sum()))
    return {"bin_centers": centers, "actual_fraction": actuals, "counts": counts}


def directional_accuracy(y_true: Sequence[int], y_pred: Sequence[int]) -> float:
    return float(np.mean(np.array(y_true) == np.array(y_pred)))


# ── Chronological evaluator ──────────────────────────────────────────────────


@dataclass
class ChronologicalEvaluator:
    """Runs chronological validation across folds, collects out-of-fold predictions."""

    folds: list[ChronologicalFold]
    metrics_history: list[dict[str, Any]] = field(default_factory=list)
    oof_predictions: list[dict[str, Any]] = field(default_factory=list)

    def evaluate(
        self,
        data: pl.DataFrame,
        date_col: str = "game_date",
        target_col: str = "home_win",
        train_fn: Any = None,
        predict_fn: Any = None,
    ) -> dict[str, Any]:
        """Run all folds. train_fn(data_before_date) -> model, predict_fn(model, data) -> probs."""
        all_metrics: list[dict[str, Any]] = []
        all_oof: list[dict[str, Any]] = []

        for fold in self.folds:
            train_mask = pl.col(date_col) <= fold.train_end
            val_mask = (pl.col(date_col) >= fold.val_start) & (pl.col(date_col) <= fold.val_end)

            train_df = data.filter(train_mask)
            val_df = data.filter(val_mask)

            if train_df.height == 0 or val_df.height == 0:
                continue

            model = train_fn(train_df, target_col) if train_fn else None
            oof_probs = predict_fn(model, val_df) if predict_fn else [0.5] * val_df.height

            y_true = val_df[target_col].to_list()
            metrics = {
                "fold": fold.fold_index,
                "train_n": train_df.height,
                "val_n": val_df.height,
                "log_loss": log_loss(y_true, oof_probs),
                "brier": brier_score(y_true, oof_probs),
                "ece": ece(y_true, oof_probs),
                "accuracy": directional_accuracy(y_true, [int(p >= 0.5) for p in oof_probs]),
            }
            all_metrics.append(metrics)

            dates = val_df[date_col].to_list()
            for d, p, y in zip(dates, oof_probs, y_true, strict=True):
                all_oof.append({"date": d, "prob": p, "outcome": y, "fold": fold.fold_index})

        self.metrics_history = all_metrics
        self.oof_predictions = all_oof
        return {"folds": len(all_metrics), "metrics": all_metrics, "oof_count": len(all_oof)}

    def summary(self) -> dict[str, Any]:
        if not self.oof_predictions:
            return {"status": "no_predictions"}
        probs = [r["prob"] for r in self.oof_predictions]
        outcomes = [r["outcome"] for r in self.oof_predictions]
        dates = [r["date"] for r in self.oof_predictions]
        return {
            "n": len(probs),
            "log_loss": log_loss(outcomes, probs),
            "brier": brier_score(outcomes, probs),
            "ece": ece(outcomes, probs),
            "bootstrap_ci": date_cluster_bootstrap(
                [p - o for p, o in zip(probs, outcomes)], dates
            ),
        }
