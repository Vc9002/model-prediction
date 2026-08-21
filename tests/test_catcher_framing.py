"""Unit tests for Point-in-time Statcast Catcher Framing engine."""

from __future__ import annotations

import pytest

from model_prediction.features.catcher_framing import (
    CatcherFramingAccumulator,
    CatcherGameRecord,
    PointInTimeCatcherFramingEngine,
)


def test_catcher_framing_accumulator_shrinkage():
    acc = CatcherFramingAccumulator(stabilization_takes=200.0)

    # 50 shadow takes, 35 strikes awarded vs 25 expected (+20% raw CSAE)
    rec = CatcherGameRecord(
        catcher_id="c_elite",
        game_date="2026-05-01",
        shadow_zone_takes=50,
        called_strikes_obtained=35,
        expected_called_strikes=25.0,
    )
    metrics = acc.compute_metrics("c_elite", [rec])

    assert metrics.games_caught == 1
    assert metrics.shadow_zone_takes == 50
    assert metrics.raw_csae == pytest.approx(0.20, abs=1e-4)
    # Shrunk weight: 50 / (50 + 200) = 0.20 -> shrunk = 0.20 * 0.20 + 0.80 * 0 = 0.04
    assert metrics.shrunk_csae == pytest.approx(0.04, abs=1e-4)
    assert metrics.estimated_runs_per_game > 0


def test_catcher_framing_pit_isolation():
    engine = PointInTimeCatcherFramingEngine()
    engine.record_catcher_game(
        CatcherGameRecord(
            catcher_id="c1",
            game_date="2026-05-01",
            shadow_zone_takes=100,
            called_strikes_obtained=60,
            expected_called_strikes=50.0,
        )
    )
    engine.record_catcher_game(
        CatcherGameRecord(
            catcher_id="c1",
            game_date="2026-05-15",
            shadow_zone_takes=100,
            called_strikes_obtained=40,
            expected_called_strikes=50.0,
        )
    )

    # As of May 10, only May 1 is visible
    snap_early = engine.get_catcher_metrics("c1", as_of_date="2026-05-10")
    assert snap_early.games_caught == 1
    assert snap_early.raw_csae == pytest.approx(0.10, abs=1e-4)

    # As of May 20, both are visible (net 0 CSAE)
    snap_late = engine.get_catcher_metrics("c1", as_of_date="2026-05-20")
    assert snap_late.games_caught == 2
    assert snap_late.raw_csae == pytest.approx(0.0, abs=1e-4)


def test_catcher_framing_matchup_differential():
    engine = PointInTimeCatcherFramingEngine()
    engine.record_catcher_game(
        CatcherGameRecord(
            catcher_id="c_home",
            game_date="2026-05-01",
            shadow_zone_takes=100,
            called_strikes_obtained=65,
            expected_called_strikes=50.0,
        )
    )
    engine.record_catcher_game(
        CatcherGameRecord(
            catcher_id="c_away",
            game_date="2026-05-01",
            shadow_zone_takes=100,
            called_strikes_obtained=35,
            expected_called_strikes=50.0,
        )
    )

    matchup = engine.evaluate_matchup("c_home", "c_away", as_of_date="2026-05-10")
    assert matchup.csae_differential > 0  # Home catcher advantage
