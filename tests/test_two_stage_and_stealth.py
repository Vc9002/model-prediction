"""Micro-test for Two-Stage Decoupled Engine and Stealth Runtime."""

from __future__ import annotations

import ctypes
import sys
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows memory priority API")
def test_low_memory_priority_reads_back_low_on_windows():
    from model_prediction import stealth_runtime

    class _MemoryPriorityInformation(ctypes.Structure):
        _fields_ = [("MemoryPriority", ctypes.c_ulong)]

    assert stealth_runtime._set_low_memory_priority() is True

    # Read back through a private WinDLL so argtypes changes stay local.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetProcessInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    kernel32.GetProcessInformation.restype = ctypes.c_int
    info = _MemoryPriorityInformation()
    ok = kernel32.GetProcessInformation(
        kernel32.GetCurrentProcess(), 0, ctypes.byref(info), ctypes.sizeof(info)
    )
    assert ok
    assert info.MemoryPriority == 2


def test_low_memory_priority_failure_is_soft(monkeypatch):
    from model_prediction import stealth_runtime

    def _boom(*args, **kwargs):
        raise OSError("no kernel32")

    monkeypatch.setattr(ctypes, "WinDLL", _boom, raising=False)

    assert stealth_runtime._set_low_memory_priority() is False
    assert isinstance(enable_background_priority(), bool)


def test_posix_nice_is_raised_to_the_target_not_stacked(monkeypatch):
    # run_supervisor main() demotes the supervisor and the auto-buyer worker demotes itself again;
    # os.nice adds to the current value, so a naive second call would push 10 to 19.
    from model_prediction import stealth_runtime

    state = {"nice": 0}

    def _fake_nice(increment: int) -> int:
        state["nice"] = min(19, state["nice"] + increment)
        return state["nice"]

    monkeypatch.setattr(stealth_runtime.sys, "platform", "linux")
    monkeypatch.setattr(stealth_runtime.os, "nice", _fake_nice, raising=False)

    assert enable_background_priority() is True
    assert enable_background_priority() is True
    assert state["nice"] == 10
    assert enable_background_priority(idle_only=True) is True
    assert state["nice"] == 19


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
