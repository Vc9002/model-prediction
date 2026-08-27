"""Unit tests for Pitcher Arsenal Matchup Engine (Roadmap Phase 14)."""

from model_prediction.features.pitcher_arsenal_matchup import (
    LineupPitchMatchupProfile,
    PitcherArsenalProfile,
    compute_arsenal_matchup,
)


def test_compute_arsenal_matchup_pitcher_advantage():
    # Pitcher throws heavy slider (50%), opposing lineup struggles vs slider (-2.0 RV/100)
    pitcher = PitcherArsenalProfile(
        pitcher_name="Spencer Strider",
        pitch_usage={"four_seam": 0.40, "slider": 0.50, "changeup": 0.10},
        pitch_whiff_rate={"four_seam": 0.28, "slider": 0.52, "changeup": 0.25},
        total_pitches_sample=1500,
    )
    lineup = LineupPitchMatchupProfile(
        team="MIA",
        run_values_per_100={"four_seam": 0.2, "slider": -2.0, "changeup": -0.5},
        sample_pitches_seen=2000,
    )

    matchup = compute_arsenal_matchup(pitcher, lineup)
    assert matchup.expected_run_value_per_100 < 0.0  # Pitcher suppresses runs
    assert matchup.matchup_sample_strength == 1.0  # High sample reliability
    assert matchup.primary_pitch_vulnerability == "slider"


def test_compute_arsenal_matchup_hitter_advantage():
    # Hitter lineup crushes fastballs (+3.0 RV/100) and pitcher is fastball-heavy (60%)
    pitcher = PitcherArsenalProfile(
        pitcher_name="Fastball Pitcher",
        pitch_usage={"four_seam": 0.60, "slider": 0.20, "changeup": 0.20},
        pitch_whiff_rate={"four_seam": 0.15, "slider": 0.25, "changeup": 0.18},
        total_pitches_sample=800,
    )
    lineup = LineupPitchMatchupProfile(
        team="LAD",
        run_values_per_100={"four_seam": 3.0, "slider": 0.5, "changeup": 0.0},
        sample_pitches_seen=1200,
    )

    matchup = compute_arsenal_matchup(pitcher, lineup)
    assert matchup.expected_run_value_per_100 > 0.5  # Offense favored
