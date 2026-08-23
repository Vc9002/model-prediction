"""Tests for starting pitcher state vector & expected depth engine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from model_prediction.features import starter_history
from model_prediction.features.starter_state import (
    estimate_expected_starter_depth,
    get_starter_state_vector,
    starter_state_matchup_gaps,
)


def _write_snapshots(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset() -> None:
    starter_history._STARTER_INDEX_CACHE.clear()


def test_estimate_expected_starter_depth() -> None:
    # Empty history shrinks to prior mean ~5.30
    assert estimate_expected_starter_depth([]) == pytest.approx(5.30)

    # Workhorse starter averaging 7.0 IP over 5 starts
    workhorse_starts = [
        (datetime(2026, 5, i, tzinfo=UTC), 7.0, 1.0, 8.0, 1.0, 0.0, 0.0, 26.0) for i in range(1, 6)
    ]
    depth = estimate_expected_starter_depth(workhorse_starts)
    assert depth > 6.0


def test_starter_state_vector_and_matchup(tmp_path: Path) -> None:
    path = tmp_path / "game_snapshots.jsonl"
    _write_snapshots(path, [])

    home_vec = get_starter_state_vector("Gerrit Cole", datetime(2026, 6, 1, tzinfo=UTC), snapshot_path=path)
    away_vec = get_starter_state_vector(
        "Opposing Pitcher", datetime(2026, 6, 1, tzinfo=UTC), snapshot_path=path
    )

    assert 0.0 < home_vec.k_pct < 1.0
    assert home_vec.expected_depth_ip > 0
    assert away_vec.expected_depth_ip > 0

    gaps = starter_state_matchup_gaps(
        "Gerrit Cole", "Opposing Pitcher", datetime(2026, 6, 1, tzinfo=UTC), snapshot_path=path
    )
    assert "starter_k_pct_gap" in gaps
    assert "starter_depth_gap" in gaps
    assert "home_expected_starter_ip" in gaps
