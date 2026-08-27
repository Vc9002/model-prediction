"""Tests for projected offense feature engine."""

from __future__ import annotations

import pytest

from model_prediction.features.batter_priors import BatterGameRecord, BatterPriorEngine
from model_prediction.features.projected_offense import (
    compute_team_projected_offense,
    projected_offense_matchup_gaps,
)


@pytest.fixture
def sample_batter_engine() -> BatterPriorEngine:
    engine = BatterPriorEngine()
    # High quality sluggers for Home Team (Team A)
    for i in range(1, 10):
        p_id = f"player_home_{i}"
        for g in range(1, 10):
            engine.ingest_game_record(
                BatterGameRecord(
                    player_id=p_id,
                    team_id="team_a",
                    game_date=f"2026-05-{g:02d}",
                    pa=4,
                    ab=4,
                    hits=2,
                    doubles=1,
                    triples=0,
                    home_runs=1,
                    strikeouts=0,
                    walks=1,
                    hard_hit_count=3,
                    barrel_count=1,
                    bip_count=3,
                    xwoba_sum=1.8,
                    vs_hand="R",
                )
            )

    # Low quality hitters for Away Team (Team B)
    for i in range(1, 10):
        p_id = f"player_away_{i}"
        for g in range(1, 10):
            engine.ingest_game_record(
                BatterGameRecord(
                    player_id=p_id,
                    team_id="team_b",
                    game_date=f"2026-05-{g:02d}",
                    pa=4,
                    ab=4,
                    hits=0,
                    doubles=0,
                    triples=0,
                    home_runs=0,
                    strikeouts=3,
                    walks=0,
                    hard_hit_count=0,
                    barrel_count=0,
                    bip_count=1,
                    xwoba_sum=0.2,
                    vs_hand="R",
                )
            )
    return engine


def test_projected_offense_team_vectors(sample_batter_engine: BatterPriorEngine) -> None:
    home_vec = compute_team_projected_offense(
        sample_batter_engine, "team_a", as_of_date="2026-05-15", opposing_pitcher_hand="R"
    )
    away_vec = compute_team_projected_offense(
        sample_batter_engine, "team_b", as_of_date="2026-05-15", opposing_pitcher_hand="R"
    )

    assert home_vec.quality_xwoba > away_vec.quality_xwoba
    assert home_vec.power_iso > away_vec.power_iso
    assert home_vec.k_minus_bb_pct < away_vec.k_minus_bb_pct  # lower K-BB is better for hitters
    assert home_vec.sample_strength_pa > 0


def test_projected_offense_matchup_gaps(sample_batter_engine: BatterPriorEngine) -> None:
    gaps = projected_offense_matchup_gaps(
        sample_batter_engine,
        home_team_id="team_a",
        away_team_id="team_b",
        as_of_date="2026-05-15",
        home_sp_hand="R",
        away_sp_hand="R",
    )

    assert gaps["projected_offense_quality_gap"] > 0
    assert gaps["projected_offense_power_gap"] > 0
    assert gaps["projected_offense_sample_strength"] > 0
