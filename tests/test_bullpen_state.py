"""Unit tests for dynamic bullpen capability and availability module."""

from __future__ import annotations

from model_prediction.features.bullpen_state import (
    PointInTimeBullpenEngine,
    RelieverAppearance,
    RelieverProfile,
    RelieverRole,
    calculate_reliever_availability,
)


def test_calculate_reliever_availability():
    # Fully rested
    p_rested = calculate_reliever_availability(pitches_1d=0, pitches_2d=0, pitches_3d=0, consecutive_days=0)
    assert p_rested >= 0.95

    # Heavy workload yesterday (35+ pitches)
    p_fatigued = calculate_reliever_availability(
        pitches_1d=38, pitches_2d=0, pitches_3d=0, consecutive_days=1
    )
    assert p_fatigued < 0.15

    # 3 consecutive days pitched
    p_consec = calculate_reliever_availability(
        pitches_1d=12, pitches_2d=15, pitches_3d=10, consecutive_days=3
    )
    assert p_consec < 0.15

    # Moderate workload (20 pitches yesterday)
    p_mod = calculate_reliever_availability(pitches_1d=22, pitches_2d=0, pitches_3d=0, consecutive_days=1)
    assert 0.40 <= p_mod <= 0.60


def test_bullpen_engine_roster_and_snapshot():
    engine = PointInTimeBullpenEngine()
    roster = [
        RelieverProfile(player_id="reliever_1", team_id="NYY", role=RelieverRole.CLOSER),
        RelieverProfile(player_id="reliever_2", team_id="NYY", role=RelieverRole.MIDDLE_RELIEF),
    ]
    engine.register_bullpen_roster("NYY", "2026-05-01", roster)

    # Record appearance on May 1
    engine.update_reliever_appearance(
        RelieverAppearance(
            player_id="reliever_1",
            team_id="NYY",
            game_date="2026-05-01",
            innings_pitched=1.0,
            pitches_thrown=15,
            batters_faced=3,
            strikeouts=2,
            walks=0,
        )
    )

    snap = engine.evaluate_bullpen("NYY", as_of_date="2026-05-03")
    assert snap.active_relievers_count == 2
    assert snap.available_relievers_count >= 1
    assert snap.aggregate_index > 0


def test_bullpen_engine_matchup_advantage():
    engine = PointInTimeBullpenEngine()
    engine.register_bullpen_roster(
        "NYY", "2026-05-01", [RelieverProfile(player_id="nyy_cl", team_id="NYY", role=RelieverRole.CLOSER)]
    )
    engine.register_bullpen_roster(
        "BOS", "2026-05-01", [RelieverProfile(player_id="bos_cl", team_id="BOS", role=RelieverRole.CLOSER)]
    )

    # NYY closer pitched 45 pitches yesterday (May 1)
    engine.update_reliever_appearance(
        RelieverAppearance(
            player_id="nyy_cl",
            team_id="NYY",
            game_date="2026-05-01",
            pitches_thrown=45,
            batters_faced=8,
        )
    )
    # BOS closer is fully rested (no appearances)

    matchup = engine.evaluate_matchup("NYY", "BOS", as_of_date="2026-05-02")
    # BOS bullpen is more rested/available than NYY
    assert matchup.availability_gap < 0  # Away (BOS) has advantage
