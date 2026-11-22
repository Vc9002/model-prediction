"""NBA research replay harness: scoring math and the holdout guard."""

from __future__ import annotations

import numpy as np
import pytest

from model_prediction.nba_replay_harness import (
    assign_splits,
    diff_golden,
    gap_statistics,
    split_boundaries,
)


def _dates(n: int) -> list[str]:
    return [f"2025-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}" for i in range(n)]


def test_lambda_recovers_known_shrink_weight() -> None:
    rng = np.random.default_rng(0)
    n = 3000
    line = rng.normal(0, 7, n)
    gap = rng.normal(0, 4, n)
    actual = line + 0.4 * gap + rng.normal(0, 3, n)
    corr, lam = gap_statistics(line + gap, line, actual, _dates(n), draws=300)
    assert lam.low < 0.4 < lam.high
    assert lam.low > 0 and corr.low > 0


def test_noise_model_gets_lambda_ci_spanning_zero() -> None:
    rng = np.random.default_rng(1)
    n = 3000
    line = rng.normal(0, 7, n)
    actual = line + rng.normal(0, 13, n)
    model = line + rng.normal(0, 4, n)
    corr, lam = gap_statistics(model, line, actual, _dates(n), draws=300)
    assert lam.low < 0 < lam.high
    assert corr.low < 0 < corr.high


def test_split_boundaries_cap_holdout() -> None:
    dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
    val_start, holdout_start = split_boundaries(dates, holdout_cap="2026-10-01")
    assert val_start < holdout_start == "2026-10-01"


def test_holdout_rows_never_reach_scoring() -> None:
    rows = [{"date": "2026-02-23", "event_id": "x"}]
    with pytest.raises(ValueError, match="holdout"):
        assign_splits(rows, "2025-11-03", "2026-02-23")


def test_diff_golden_counts_changes() -> None:
    base = {
        "date": "d",
        "model_margin": 1.0,
        "model_total": 2.0,
        "p_away_cover": 0.5,
        "p_over": 0.5,
        "p_home_ml": 0.5,
    }
    golden = [{"event_id": "a", **base}, {"event_id": "b", **base}]
    rows = [{"event_id": "a", **base}, {"event_id": "b", **base, "model_total": 2.0000001}]
    assert diff_golden(golden, rows) == {"golden": 2, "missing": 0, "extra": 0, "changed": 1}
