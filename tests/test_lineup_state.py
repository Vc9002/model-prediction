"""Tests for lineup state aggregation module."""

from __future__ import annotations

import pytest

from model_prediction.features.batter_priors import (
    BatterGameRecord,
    PointInTimeBatterPriorEngine,
)
from model_prediction.features.lineup_state import (
    ConfirmedLineup,
    LineupBatter,
    LineupStateEngine,
)


@pytest.fixture
def populated_prior_engine():
    engine = PointInTimeBatterPriorEngine()
    # Populate high-talent players for NYY
    for i in range(1, 10):
        engine.update_player_game(
            BatterGameRecord(
                player_id=f"nyy_batter_{i}",
                team_id="NYY",
                game_date="2026-05-01",
                pa=50,
                ab=40,
                hits=15,
                doubles=4,
                triples=0,
                home_runs=4,
                strikeouts=8,
                walks=8,
                bip_count=28,
                hard_hit_count=14,
                barrel_count=5,
            )
        )
    # Populate low-talent players for OAK
    for i in range(1, 10):
        engine.update_player_game(
            BatterGameRecord(
                player_id=f"oak_batter_{i}",
                team_id="OAK",
                game_date="2026-05-01",
                pa=50,
                ab=45,
                hits=8,
                doubles=1,
                triples=0,
                home_runs=1,
                strikeouts=18,
                walks=3,
                bip_count=26,
                hard_hit_count=6,
                barrel_count=1,
            )
        )
    return engine


def test_lineup_state_confirmed_evaluation(populated_prior_engine):
    lineup_engine = LineupStateEngine(populated_prior_engine)

    nyy_lineup = ConfirmedLineup(
        team_id="NYY",
        game_date="2026-05-10",
        batters=[LineupBatter(player_id=f"nyy_batter_{i}", batting_order=i) for i in range(1, 10)],
    )
    oak_lineup = ConfirmedLineup(
        team_id="OAK",
        game_date="2026-05-10",
        batters=[LineupBatter(player_id=f"oak_batter_{i}", batting_order=i) for i in range(1, 10)],
    )

    lineup_engine.register_confirmed_lineup(nyy_lineup)
    lineup_engine.register_confirmed_lineup(oak_lineup)

    adv = lineup_engine.evaluate_matchup("NYY", "OAK", "2026-05-10")
    assert adv.is_confirmed is True
    assert adv.xwoba_gap > 0  # NYY has higher xwOBA than OAK
    assert adv.k_pct_gap > 0  # NYY has lower K% than OAK (positive gap is good for home)
    assert adv.iso_gap > 0  # NYY has higher power


def test_lineup_state_unconfirmed_fallback(populated_prior_engine):
    lineup_engine = LineupStateEngine(populated_prior_engine)
    adv = lineup_engine.evaluate_matchup("NYY", "OAK", "2026-05-10")
    assert adv.is_confirmed is False
    assert adv.xwoba_gap > 0
