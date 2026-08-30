"""Nested chronological validation framework.

Expanding or rolling folds grouped by complete event dates. No random K-fold.
One untouched final test per model family. Date-cluster and team-cluster bootstrap.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

# ── Fold definitions ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChronologicalFold:
    """One fold: train on dates < train_end, validate on val_dates, test (if supplied) untouched.

    train_start/embargo_start/embargo_end are additive (CLAUDE.md Part 2
    SS2's exact split-manifest schema names all three) -- real gap this
    closes: expanding_folds()/rolling_folds() already enforced the
    embargo (the `gap` parameter genuinely separates train_end from
    val_start), but never recorded the embargo's own date boundaries or
    where training data actually started, so a persisted split manifest
    couldn't show either even though both were real, already-correct
    behavior."""

    fold_index: int
    train_end: str  # ISO date — everything before this is train
    val_start: str  # ISO date
    val_end: str  # ISO date
    train_start: str | None = None  # first date included in this fold's training window
    embargo_start: str | None = None  # first date excluded as publication embargo (day after train_end)
    embargo_end: str | None = None  # last date excluded as publication embargo (day before val_start)
    test_start: str | None = None  # separate economic test, not used in Part 2
    test_end: str | None = None


def expanding_folds(
    dates: Sequence[str],
    n_splits: int = 5,
    val_size: int = 30,  # days
    gap: int = 1,  # publication embargo
    test_size: int = 60,  # untouched final test
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
        has_embargo = val_start_idx > train_end_idx
        folds.append(
            ChronologicalFold(
                fold_index=i,
                train_end=train_dates[train_end_idx - 1],
                val_start=sorted_dates[val_start_idx],
                val_end=sorted_dates[val_end_idx - 1],
                train_start=train_dates[0],  # expanding: every fold's window starts at the same earliest date
                embargo_start=sorted_dates[train_end_idx] if has_embargo else None,
                embargo_end=sorted_dates[val_start_idx - 1] if has_embargo else None,
                test_start=sorted_dates[-test_size] if test_size > 0 else None,
                test_end=sorted_dates[-1] if test_size > 0 else None,
            )
        )
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
        has_embargo = val_start_idx > train_end_idx
        folds.append(
            ChronologicalFold(
                fold_index=i,
                train_end=sorted_dates[train_end_idx - 1],
                val_start=sorted_dates[val_start_idx],
                val_end=sorted_dates[val_end_idx - 1],
                train_start=sorted_dates[offset],  # rolling: fixed-size window slides forward with i
                embargo_start=sorted_dates[train_end_idx] if has_embargo else None,
                embargo_end=sorted_dates[val_start_idx - 1] if has_embargo else None,
                test_start=sorted_dates[-test_size] if test_size > 0 else None,
                test_end=sorted_dates[-1] if test_size > 0 else None,
            )
        )
    return folds


def date_cluster_split(
    dates: Sequence[str],
    test_size: int,
    calib_size: int = 0,
) -> tuple[list[str], list[str], list[str]]:
    """Split real calendar dates into (train_dates, calib_dates, test_dates)
    -- never splits a single date's games across buckets.

    Real bug this closes (see outputs/rebuild/takeover_status.md Task 8):
    the actual final-test split in train_mlb_rebuild_real_features.py
    sliced the sorted feature table by a game *count*
    (`features[n - test_size:]`), not by real calendar date. Two games on
    the identical calendar date can land on opposite sides of that
    positional cut -- exactly the "same-day contamination" CLAUDE.md's
    own Part 2 SS2 names as the reason folds must be grouped by complete
    event dates: "No game on the same calendar date may be placed in
    training while another game from that date is in outer validation."
    This applies the same real invariant to the final train/calibration/
    test split, not just the cross-validation folds expanding_folds()
    already handles.

    Returns real dates only -- the caller re-joins them against its own
    feature table (e.g. `pl.col("game_date").is_in(test_dates)`), so
    every game sharing a selected date moves together as one indivisible
    cluster. If there aren't enough real distinct dates for the requested
    sizes, returns everything as train and empty calib/test lists --
    honest under-sizing, never a fabricated split."""
    unique_dates = sorted(set(dates))
    if len(unique_dates) < test_size + calib_size + 1:
        return unique_dates, [], []
    test_dates = unique_dates[-test_size:] if test_size > 0 else []
    remaining = unique_dates[: len(unique_dates) - test_size]
    calib_dates = remaining[-calib_size:] if calib_size > 0 else []
    train_dates = remaining[: len(remaining) - calib_size] if calib_size > 0 else remaining
    return train_dates, calib_dates, test_dates


def build_split_manifest(
    sport: str,
    horizon: str,
    dataset_hash: str,
    folds: Sequence[ChronologicalFold],
    final_test_start: str,
    final_test_end: str,
    final_test_consumed: bool = False,
) -> dict[str, Any]:
    """Real split manifest in CLAUDE.md Part 2 SS2's exact required shape --
    real gap this closes: the only real caller that persisted a manifest
    (scripts/train_mlb_rebuild_real_features.py) built its own ad-hoc dict
    with per-fold *metrics* (fold_metrics: log_loss/brier/ece) but never
    the per-fold *date ranges* CLAUDE.md's own spec names (folds[].
    train_start/train_end/embargo_start/embargo_end/validation_start/
    validation_end) -- those boundaries existed only as positional slice
    indices in the script's own memory while it ran, unrecoverable from
    any persisted artifact afterward. This doesn't replace that script's
    richer fold_metrics (real per-fold predictive scores are useful and
    stay); it's the missing complementary piece -- real fold provenance,
    not fold performance."""
    return {
        "sport": sport,
        "horizon": horizon,
        "dataset_hash": dataset_hash,
        "folds": [
            {
                "train_start": f.train_start,
                "train_end": f.train_end,
                "embargo_start": f.embargo_start,
                "embargo_end": f.embargo_end,
                "validation_start": f.val_start,
                "validation_end": f.val_end,
            }
            for f in folds
        ],
        "final_test_start": final_test_start,
        "final_test_end": final_test_end,
        "final_test_consumed": final_test_consumed,
    }


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


def team_cluster_bootstrap(
    values: Sequence[float],
    teams: Sequence[str],
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> dict[str, float]:
    """Bootstrap with team clusters (preserves within-team correlation --
    e.g. a genuinely mispriced park factor or a systematically miscalibrated
    matchup feature affects every game a given team plays in, not just one
    independent observation). CLAUDE.md Part 2 SS2 names both "date-cluster
    and team-cluster bootstrap intervals" as required; only the date-cluster
    version existed until now. Same resampling shape as
    date_cluster_bootstrap(), clustering on `teams` instead of `dates`.

    `teams` should carry one cluster key per value -- for a two-team game,
    callers report the same value under both team keys (one row per team
    per game), matching how a team-level correlation risk actually shows
    up (a bad Rockies-specific feature affects every Rockies game, home or
    away)."""
    rng = np.random.default_rng(seed)
    team_groups: dict[str, list[float]] = defaultdict(list)
    for t, v in zip(teams, values, strict=True):
        team_groups[t].append(v)
    group_keys = list(team_groups.keys())
    means: list[float] = []
    for _ in range(n_bootstrap):
        sampled = rng.choice(group_keys, size=len(group_keys), replace=True)
        sample_vals = [v for k in sampled for v in team_groups[k]]
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


def season_block_bootstrap(
    values: Sequence[float],
    dates: Sequence[str],
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> dict[str, float]:
    """Hierarchical season-block bootstrap for MLB/sports data.

    Preserves both within-season temporal autocorrelation and year-over-year
    structural shifts (rule changes, ball aerodynamics, run environments).
    Resamples season blocks, and then resamples date clusters within each season.
    """
    rng = np.random.default_rng(seed)
    season_date_groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for d, v in zip(dates, values, strict=True):
        season = d[:4] if len(d) >= 4 else "unknown"
        season_date_groups[season][d].append(v)

    seasons = list(season_date_groups.keys())
    if not seasons:
        return {
            "mean": 0.0,
            "ci_lower": 0.0,
            "ci_upper": 0.0,
            "std": 0.0,
            "n_bootstrap": n_bootstrap,
        }

    means: list[float] = []
    for _ in range(n_bootstrap):
        sampled_seasons = rng.choice(seasons, size=len(seasons), replace=True)
        sample_vals: list[float] = []
        for s in sampled_seasons:
            date_keys = list(season_date_groups[s].keys())
            sampled_dates = rng.choice(date_keys, size=len(date_keys), replace=True)
            for dk in sampled_dates:
                sample_vals.extend(season_date_groups[s][dk])
        if sample_vals:
            means.append(float(np.mean(sample_vals)))

    means_arr = np.array(means) if means else np.array([0.0])
    return {
        "mean": float(np.mean(means_arr)),
        "ci_lower": float(np.percentile(means_arr, 2.5)),
        "ci_upper": float(np.percentile(means_arr, 97.5)),
        "std": float(np.std(means_arr)),
        "n_bootstrap": n_bootstrap,
    }


def minimum_detectable_effect(
    n_samples: int,
    alpha: float = 0.05,
    power: float = 0.80,
    baseline_sd: float = 0.50,
) -> dict[str, Any]:
    """Calculate Minimum Detectable Effect (MDE) pre-check before feature ablation.

    Standard formula: MDE = (z_{1 - alpha/2} + z_{power}) * (sigma / sqrt(N)).
    Prevents false negatives from running underpowered tests where plausible
    alpha (< 2%) cannot be statistically distinguished from zero.
    """
    if n_samples <= 0:
        return {
            "n_samples": 0,
            "mde_absolute": float("inf"),
            "mde_percentage": float("inf"),
            "is_powered_for_standard_edge": False,
            "note": "Sample size must be positive",
        }

    from scipy.stats import norm

    z_alpha = float(norm.ppf(1.0 - alpha / 2.0))
    z_power = float(norm.ppf(power))

    mde_abs = (z_alpha + z_power) * (baseline_sd / math.sqrt(n_samples))

    return {
        "n_samples": n_samples,
        "alpha": alpha,
        "power": power,
        "baseline_sd": baseline_sd,
        "mde_absolute": round(float(mde_abs), 5),
        "mde_percentage": round(float(mde_abs * 100.0), 2),
        "is_powered_for_standard_edge": bool(mde_abs <= 0.02),  # can detect <= 2.0% delta
        "note": "Powered"
        if mde_abs <= 0.02
        else f"Underpowered: cannot detect deltas < {mde_abs * 100:.2f}%",
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
    y_true: Sequence[int],
    y_prob: Sequence[float],
    n_bins: int = 10,
) -> dict[str, Any]:
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
            "bootstrap_ci": date_cluster_bootstrap([p - o for p, o in zip(probs, outcomes)], dates),
        }
