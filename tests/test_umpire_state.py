"""Unit tests for MLB Point-in-Time Umpire Strike Zone Feature Engine."""

from __future__ import annotations

from model_prediction.features.umpire_state import (
    LEAGUE_NEUTRAL_CSAE,
    LEAGUE_NEUTRAL_RUN_FACTOR,
    PointInTimeUmpireEngine,
    UmpireGameRecord,
)


def test_umpire_neutral_cold_start():
    engine = PointInTimeUmpireEngine()
    state = engine.evaluate_umpire("ump_new", as_of_date="2026-06-01", umpire_name="New Ump")

    assert state.games_umpired == 0
    assert state.csae == LEAGUE_NEUTRAL_CSAE
    assert state.run_factor == LEAGUE_NEUTRAL_RUN_FACTOR
    assert state.k_factor == 1.0
    assert state.bb_factor == 1.0


def test_umpire_shrinkage_pitcher_friendly_zone():
    engine = PointInTimeUmpireEngine(stabilization_games=30.0)

    # Record 10 pitcher-friendly games (wide zone: +5% called strikes above expected, 6.0 runs/game)
    for i in range(1, 11):
        engine.record_game(
            UmpireGameRecord(
                game_id=f"g_{i}",
                game_date=f"2026-05-{i:02d}",
                umpire_id="ump_wide",
                umpire_name="Wide Zone Ump",
                total_pitches=290,
                called_pitches=120,
                called_strikes=40,
                expected_called_strikes=34.0,  # +6 strikes (~ +5% CSAE)
                total_runs_scored=6,
                total_strikeouts=20,
                total_walks=4,
            )
        )

    # Evaluate at 2026-05-15
    state = engine.evaluate_umpire("ump_wide", as_of_date="2026-05-15")
    assert state.games_umpired == 10

    # Shrunk CSAE should be positive but shrunk toward 0
    assert 0.0 < state.csae < state.raw_csae
    # Run factor should be below 1.0 (pitcher-friendly)
    assert state.run_factor < 1.0
    # K factor should be boosted (> 1.0) and BB factor dampened (< 1.0)
    assert state.k_factor > 1.0
    assert state.bb_factor < 1.0


def test_point_in_time_isolation():
    engine = PointInTimeUmpireEngine()

    engine.record_game(
        UmpireGameRecord(
            game_id="g_past",
            game_date="2026-05-01",
            umpire_id="ump_pit",
            umpire_name="PIT Ump",
            total_pitches=300,
            called_pitches=130,
            called_strikes=45,
            expected_called_strikes=39.0,
            total_runs_scored=5,
            total_strikeouts=22,
            total_walks=3,
        )
    )
    engine.record_game(
        UmpireGameRecord(
            game_id="g_future",
            game_date="2026-05-20",
            umpire_id="ump_pit",
            umpire_name="PIT Ump",
            total_pitches=300,
            called_pitches=130,
            called_strikes=45,
            expected_called_strikes=39.0,
            total_runs_scored=15,
            total_strikeouts=10,
            total_walks=10,
        )
    )

    # As of 2026-05-10, only g_past should be evaluated
    state = engine.evaluate_umpire("ump_pit", as_of_date="2026-05-10")
    assert state.games_umpired == 1
