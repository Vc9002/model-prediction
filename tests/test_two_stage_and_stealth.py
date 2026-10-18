"""Micro-test for Two-Stage Decoupled Engine and Stealth Runtime."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from model_prediction.stealth_runtime import enable_background_priority
from model_prediction.two_stage import (
    Stage1CacheStore,
    Stage1Probability,
    Stage2ValuationEngine,
)


def test_stealth_runtime_priority_activation():
    # Should safely succeed on Windows or POSIX without raising errors
    res = enable_background_priority()
    assert isinstance(res, bool)


def test_two_stage_separation_and_hard_edge_gate():
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_path = Path(tmp_dir) / "stage1.json"
        store = Stage1CacheStore(cache_file=cache_path)

        prob = Stage1Probability(
            event_id="nfl_chiefs_bills_2026",
            sport="NFL",
            game_date="2026-10-20",
            home_team="Bills",
            away_team="Chiefs",
            market_type="moneyline",
            selection="home",
            true_prob=0.58,
            model_id="nfl-elo-trend-lr-v4",
        )
        store.put(prob)
        store.save()

        # Reload store from disk
        reloaded_store = Stage1CacheStore(cache_file=cache_path)
        cached = reloaded_store.get("nfl_chiefs_bills_2026", "moneyline", "home")
        assert cached is not None
        assert cached.true_prob == 0.58

        engine = Stage2ValuationEngine(min_edge=0.02, max_edge=0.08)

        # Case 1: Qualified edge (true_prob 0.58 vs ask 0.53 -> edge 0.05)
        ticket1 = engine.evaluate(cached, "polymarket-nfl-bills-ml", market_ask=0.53)
        assert ticket1.qualified is True
        assert ticket1.net_edge == pytest.approx(0.05)
        assert ticket1.recommended_stake_usd > 0

        # Case 2: Below min edge (true_prob 0.58 vs ask 0.57 -> edge 0.01)
        ticket2 = engine.evaluate(cached, "polymarket-nfl-bills-ml", market_ask=0.57)
        assert ticket2.qualified is False
        assert "LOW_EDGE" in ticket2.rejection_reason

        # Case 3: Above max edge (true_prob 0.58 vs ask 0.45 -> edge 0.13, excessive/stale line)
        ticket3 = engine.evaluate(cached, "polymarket-nfl-bills-ml", market_ask=0.45)
        assert ticket3.qualified is False
        assert "EXCESSIVE_EDGE_GATE" in ticket3.rejection_reason
