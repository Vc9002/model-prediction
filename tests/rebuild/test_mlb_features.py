"""Tests for real MLB pregame features built from normalized Statcast pitches
(src/model_prediction/rebuild/mlb_features.py) — replaces the rolling-score
placeholder used in scripts/pipeline_mlb_e2e.py. See
outputs/rebuild/takeover_status.md Checkpoint 5.
"""

from __future__ import annotations

import polars as pl

from model_prediction.rebuild.mlb_features import (
    bullpen_rolling_features,
    identify_starters,
    normalize_statcast_pitches,
    park_factor,
    pitcher_rolling_features,
)


def _pitch_row(
    game_pk: int, game_date: str, pitcher: int, at_bat_number: int, pitch_number: int,
    *, inning_topbot: str = "Top", home_team: str = "HOME", away_team: str = "AWAY",
    description: str = "called_strike", events: str | None = None,
    release_speed: float = 93.0, pitcher_days_since_prev_game: int = 4,
) -> dict:
    return {
        "game_pk": game_pk, "game_date": game_date, "pitcher": pitcher,
        "batter": 1, "home_team": home_team, "away_team": away_team,
        "inning": 1, "inning_topbot": inning_topbot,
        "at_bat_number": at_bat_number, "pitch_number": pitch_number,
        "pitch_type": "FF", "release_speed": release_speed, "release_spin_rate": 2200,
        "description": description, "events": events, "zone": 5,
        "p_throws": "R", "stand": "R",
        "pitcher_days_since_prev_game": pitcher_days_since_prev_game,
        "n_thruorder_pitcher": 1,
    }


class TestIdentifyStarters:
    def test_starter_is_whoever_threw_the_first_pitch_for_their_team(self):
        # HOME pitches in the Top half; starter=100 throws AB1, reliever=200
        # comes in for AB2. The starter must be 100, not whoever pitched most.
        rows = [
            _pitch_row(1, "2026-08-01", 100, at_bat_number=1, pitch_number=1),
            _pitch_row(1, "2026-08-01", 100, at_bat_number=1, pitch_number=2),
            _pitch_row(1, "2026-08-01", 200, at_bat_number=2, pitch_number=1),
            _pitch_row(1, "2026-08-01", 200, at_bat_number=2, pitch_number=2),
            _pitch_row(1, "2026-08-01", 200, at_bat_number=2, pitch_number=3),
        ]
        pitches = normalize_statcast_pitches(pl.DataFrame(rows))
        starters = identify_starters(pitches)

        row = starters.filter((pl.col("game_pk") == 1) & (pl.col("pitching_team") == "HOME"))
        assert row["pitcher"][0] == 100


class TestPitcherRollingFeaturesPointInTime:
    def test_future_starts_never_leak_into_a_past_decision(self):
        # Pitcher 100 started on 07-25 (before) and 08-10 (after) the
        # decision date of 08-01. Only 07-25 may influence the features.
        rows = [
            _pitch_row(1, "2026-07-25", 100, 1, 1, release_speed=90.0),
            _pitch_row(2, "2026-08-10", 100, 1, 1, release_speed=99.0),
        ]
        pitches = normalize_statcast_pitches(pl.DataFrame(rows))

        feats = pitcher_rolling_features(pitches, 100, before_game_date="2026-08-01")

        assert feats["availability"] == 1.0
        assert feats["avg_velocity"] == 90.0, (
            "future start (08-10) leaked into a feature computed for a decision on 08-01"
        )

    def test_no_prior_history_reports_unavailable_not_a_guessed_average(self):
        pitches = normalize_statcast_pitches(pl.DataFrame([_pitch_row(1, "2026-08-05", 100, 1, 1)]))

        feats = pitcher_rolling_features(pitches, 999, before_game_date="2026-08-01")

        assert feats["availability"] == 0.0
        assert feats["avg_velocity"] == 0.0, (
            "a pitcher with zero prior starts must report availability=0, not a silently "
            "substituted league-average-looking number"
        )

    def test_k_pct_and_bb_pct_computed_from_real_plate_appearance_outcomes(self):
        rows = [
            _pitch_row(1, "2026-07-25", 100, 1, 1, description="called_strike"),
            _pitch_row(1, "2026-07-25", 100, 1, 2, description="swinging_strike", events="strikeout"),
            _pitch_row(1, "2026-07-25", 100, 2, 1, description="ball"),
            _pitch_row(1, "2026-07-25", 100, 2, 2, description="ball"),
            _pitch_row(1, "2026-07-25", 100, 2, 3, description="ball"),
            _pitch_row(1, "2026-07-25", 100, 2, 4, description="ball", events="walk"),
        ]
        pitches = normalize_statcast_pitches(pl.DataFrame(rows))

        feats = pitcher_rolling_features(pitches, 100, before_game_date="2026-08-01")

        assert feats["k_pct"] == 0.5   # 1 strikeout / 2 batters faced
        assert feats["bb_pct"] == 0.5  # 1 walk / 2 batters faced


class TestBullpenRollingFeatures:
    def test_starter_is_excluded_from_bullpen_workload(self):
        rows = [
            _pitch_row(1, "2026-07-30", 100, 1, 1, home_team="TOR", away_team="NYY"),  # starter
            _pitch_row(1, "2026-07-30", 200, 2, 1, home_team="TOR", away_team="NYY"),  # reliever
            _pitch_row(1, "2026-07-30", 200, 2, 2, home_team="TOR", away_team="NYY"),
        ]
        pitches = normalize_statcast_pitches(pl.DataFrame(rows))
        starters = identify_starters(pitches)

        feats = bullpen_rolling_features(pitches, "TOR", before_game_date="2026-08-01", starters=starters)

        assert feats["bullpen_pitches"] == 2.0, "the starter's own pitches must not count as bullpen workload"
        assert feats["bullpen_appearances"] == 1.0


class TestParkFactor:
    def test_known_park_returns_its_real_factor(self):
        assert park_factor("Coors Field") == 112.0

    def test_unknown_park_defaults_to_neutral_not_a_guess(self):
        assert park_factor("Some Random Independent-League Field") == 100.0
