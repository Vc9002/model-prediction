"""Unit tests for Point-in-time multidimensional starter state engine."""

from __future__ import annotations

from model_prediction.features.starter_state import (
    PointInTimeStarterEngine,
    StarterGameRecord,
    StarterStateAccumulator,
)


def test_starter_state_accumulator_basic():
    acc = StarterStateAccumulator()
    rec = StarterGameRecord(
        pitcher_id="sp1",
        game_date="2026-05-01",
        innings_pitched=6.0,
        pitches_thrown=90,
        batters_faced=24,
        strikeouts=7,
        walks=2,
        earned_runs=2,
        called_strikes=18,
        whiffs=12,
        first_pitch_strikes=16,
        fastball_velocity_avg=95.2,
        fastball_pitches=50,
    )
    metrics = acc.compute_metrics([rec])

    assert metrics.starts_count == 1
    assert metrics.innings_pitched == 6.0
    assert metrics.k_pct > 0.20
    assert metrics.csw_pct > 0.25
    assert metrics.fastball_velocity is not None and metrics.fastball_velocity > 94.0


def test_starter_engine_pit_isolation():
    engine = PointInTimeStarterEngine()
    engine.update_starter_game(
        StarterGameRecord(
            pitcher_id="sp1",
            game_date="2026-05-01",
            innings_pitched=6.0,
            pitches_thrown=90,
            batters_faced=24,
            strikeouts=8,
            walks=1,
        )
    )
    engine.update_starter_game(
        StarterGameRecord(
            pitcher_id="sp1",
            game_date="2026-05-15",
            innings_pitched=5.0,
            pitches_thrown=80,
            batters_faced=20,
            strikeouts=4,
            walks=3,
        )
    )

    # As of May 10, only May 1 game is visible
    snap_early = engine.get_starter_state("sp1", as_of_date="2026-05-10")
    assert snap_early.season_metrics.starts_count == 1

    # As of May 20, both games are visible
    snap_late = engine.get_starter_state("sp1", as_of_date="2026-05-20")
    assert snap_late.season_metrics.starts_count == 2


def test_starter_matchup_evaluation():
    engine = PointInTimeStarterEngine()
    engine.update_starter_game(
        StarterGameRecord(
            pitcher_id="sp_home",
            game_date="2026-05-01",
            innings_pitched=7.0,
            pitches_thrown=95,
            batters_faced=26,
            strikeouts=9,
            walks=1,
            earned_runs=1,
            called_strikes=20,
            whiffs=15,
        )
    )
    engine.update_starter_game(
        StarterGameRecord(
            pitcher_id="sp_away",
            game_date="2026-05-01",
            innings_pitched=4.0,
            pitches_thrown=85,
            batters_faced=22,
            strikeouts=2,
            walks=4,
            earned_runs=5,
            called_strikes=10,
            whiffs=3,
        )
    )

    matchup = engine.evaluate_matchup("sp_home", "sp_away", as_of_date="2026-05-10")
    assert matchup.k_bb_gap > 0  # Home SP has higher K-BB%
    assert matchup.csw_gap > 0  # Home SP misses more bats
