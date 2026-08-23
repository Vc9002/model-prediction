"""Tests for platoon splits and starter matchup feature engine."""

from __future__ import annotations

from datetime import UTC, datetime

from model_prediction.features.platoon_matchup import (
    estimate_team_platoon_profile,
    platoon_matchup_gaps,
)


def test_platoon_profile_and_matchup() -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    prof = estimate_team_platoon_profile("New York Yankees", now)
    assert 0.20 < prof.woba_vs_lhp < 0.45
    assert 0.20 < prof.woba_vs_rhp < 0.45

    gaps = platoon_matchup_gaps("New York Yankees", "Boston Red Sox", "L", "R", now)
    assert "platoon_woba_advantage" in gaps
    assert "platoon_iso_advantage" in gaps
    assert "home_offense_matchup_woba" in gaps
