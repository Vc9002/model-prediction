from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import numpy as np


def split_complete_dates(
    games: Sequence[Any],
    train_ratio: float = 0.60,
    val_ratio: float = 0.20,
) -> tuple[list[Any], list[Any], list[Any], dict[str, dict[str, Any]]]:
    """Split games by chronological complete dates into train/validation/holdout.

    Guarantees that no single calendar date is ever partitioned across cohorts.
    """
    games_by_date: dict[str, list[Any]] = defaultdict(list)
    for g in games:
        d = g.event_start_utc[:10]
        games_by_date[d].append(g)

    unique_dates = sorted(games_by_date.keys())
    n_dates = len(unique_dates)

    n_train = math.floor(n_dates * train_ratio)
    n_val = math.floor(n_dates * val_ratio)

    train_dates = unique_dates[:n_train]
    val_dates = unique_dates[n_train : n_train + n_val]
    holdout_dates = unique_dates[n_train + n_val :]

    train_games = [g for d in train_dates for g in games_by_date[d]]
    val_games = [g for d in val_dates for g in games_by_date[d]]
    holdout_games = [g for d in holdout_dates for g in games_by_date[d]]

    metadata = {
        "train": {"dates": len(train_dates), "games": len(train_games)},
        "validation": {"dates": len(val_dates), "games": len(val_games)},
        "holdout": {"dates": len(holdout_dates), "games": len(holdout_games)},
    }
    return train_games, val_games, holdout_games, metadata


def reproduction_gate(
    observed: dict[str, Any],
    expected: dict[str, Any],
    rel_tol: float = 0.05,
    abs_tol: float = 0.05,
) -> dict[str, Any]:
    """Verify that observed metrics match expected metrics within tolerance."""
    checks: dict[str, bool] = {}

    # Sample size check
    obs_n = observed.get("n_games", observed.get("sample_size"))
    exp_n = expected.get("sample_size", expected.get("n_games"))
    if obs_n is not None and exp_n is not None:
        checks["sample_size"] = bool(obs_n == exp_n)

    # MAE check
    if "mae" in expected and "mae" in observed:
        checks["mae"] = bool(math.isclose(observed["mae"], expected["mae"], rel_tol=rel_tol, abs_tol=abs_tol))

    # RMSE check
    if "rmse" in expected and "rmse" in observed:
        checks["rmse"] = bool(
            math.isclose(observed["rmse"], expected["rmse"], rel_tol=rel_tol, abs_tol=abs_tol)
        )

    all_passed = all(checks.values()) and bool(checks)
    status = "PASS_REPRODUCTION" if all_passed else "FAIL_REPRODUCTION"
    return {"status": status, "checks": checks}


def paired_date_bootstrap(
    rows: list[dict[str, Any]],
    iterations: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Cluster by date and bootstrap paired gain (control error minus candidate error)."""
    if not rows:
        return {"mean_gain": 0.0, "ci_95": [0.0, 0.0], "p_gain_positive": 0.0}

    # Group gains by date cluster
    date_clusters: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        gain = float(r["control_abs_error"]) - float(r["candidate_abs_error"])
        date_clusters[r["date"]].append(gain)

    dates = list(date_clusters.keys())
    all_gains = [g for cluster in date_clusters.values() for g in cluster]
    mean_gain = float(np.mean(all_gains))

    rng = np.random.default_rng(seed)
    boot_means = []
    n_dates = len(dates)

    for _ in range(iterations):
        sample_indices = rng.integers(0, n_dates, size=n_dates)
        boot_gains = []
        for idx in sample_indices:
            boot_gains.extend(date_clusters[dates[idx]])
        boot_means.append(float(np.mean(boot_gains)))

    ci_lower = float(np.percentile(boot_means, 2.5))
    ci_upper = float(np.percentile(boot_means, 97.5))
    p_pos = float(np.mean([m > 0 for m in boot_means]))

    return {
        "mean_gain": round(mean_gain, 4),
        "ci_95": [round(ci_lower, 4), round(ci_upper, 4)],
        "p_gain_positive": round(p_pos, 4),
    }
