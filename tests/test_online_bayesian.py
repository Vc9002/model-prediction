"""Micro-test for Online Bayesian Drift Correction."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from model_prediction.online_bayesian import OnlineBayesianTracker


def test_online_bayesian_lifecycle_and_convergence():
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = Path(tmp_dir) / "drift.json"
        tracker = OnlineBayesianTracker(state_file=state_file)

        # Base case: initial adjustment is neutral
        raw = 0.55
        adj = tracker.adjust_probability("tennis", raw)
        assert adj == raw

        # Model is consistently underestimating (5 consecutive wins for a 0.50 team)
        for _ in range(5):
            tracker.update_with_outcome("tennis", raw_prob=0.50, outcome=1, persist=False)

        state = tracker.get_state("tennis")
        assert state.mu > 0.0  # Bias shifted upward
        assert state.n_updates == 5

        # Raw 0.50 should now be adjusted upward
        adjusted = tracker.adjust_probability("tennis", 0.50)
        assert adjusted > 0.50

        # Persistence check
        tracker.save()
        reloaded = OnlineBayesianTracker(state_file=state_file)
        assert reloaded.get_state("tennis").mu == pytest.approx(state.mu)
