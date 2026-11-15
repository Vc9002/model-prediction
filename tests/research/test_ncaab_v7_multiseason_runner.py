"""Research-runner guards for the NCAAB v7 multi-season replay (quick 261115-m5r).

Run on demand: ``pytest tests/research/test_ncaab_v7_multiseason_runner.py -o addopts='' -q``.
"""

from __future__ import annotations

import numpy as np
import pytest


def _load_runner():
    import importlib
    import sys
    from pathlib import Path

    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("ncaab_v7_multiseason")


def test_runner_outcome_loader_refuses_outcome_free_stages() -> None:
    runner = _load_runner()
    for stage in ("components", "gate"):
        with pytest.raises(RuntimeError, match="may not read outcomes"):
            runner.load_outcomes(stage)


def test_pin_windows_are_disjoint_and_line_joined_seasons_exclude_contaminated_ones() -> None:
    runner = _load_runner()
    windows = runner.PIN["windows_by_espn_season_year"]
    seasons = [y for ys in windows.values() for y in ys]
    assert len(seasons) == len(set(seasons))
    assert set(runner.LINE_SEASONS) == {2023, 2024, 2025}
    assert not set(runner.LINE_SEASONS) & set(
        windows["contaminated_2025_26"] + windows["contaminated_2026_27_partial"]
    )


def test_games_played_buckets_follow_the_pinned_edges() -> None:
    runner = _load_runner()
    assert runner.sbucket(np.array([0, 1, 2, 3, 7, 8, 30])).tolist() == [0, 1, 1, 2, 2, 3, 3]
