"""Tests for real MLB pregame features built from normalized Statcast pitches
(src/model_prediction/rebuild/mlb_features.py) — replaces the rolling-score
placeholder used in scripts/pipeline_mlb_e2e.py. See
outputs/rebuild/takeover_status.md Checkpoint 5.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import polars as pl

from model_prediction.rebuild.mlb_features import (
    bullpen_rolling_features,
    dedupe_scoreboard,
    identify_starters,
    lookup_pitcher_id,
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


class TestLookupPitcherId:
    """Real, verified case from a live shadow run (see
    outputs/rebuild/takeover_status.md Checkpoint 9): pybaseball's register
    has two real "Drew Anderson"s — one who last played in 2006, one active
    through 2026. The initial implementation treated any multi-row match as
    unresolvable ambiguity and returned None, which correctly avoided
    guessing wrong but meant a real, resolvable probable starter never got
    real features. Fixed to break ties by mlb_played_last (a real recency
    fact already in the same lookup result, not a guess) since a probable
    starter for a real upcoming game must be the currently active player.
    """

    def test_ambiguous_name_resolved_by_recency(self):
        fake_result = pd.DataFrame({
            "name_first": ["drew", "drew"], "name_last": ["anderson", "anderson"],
            "key_mlbam": [449776, 623454],
            "mlb_played_first": [2006.0, 2017.0], "mlb_played_last": [2006.0, 2026.0],
        })
        with patch("pybaseball.playerid_lookup", return_value=fake_result):
            assert lookup_pitcher_id("Drew Anderson") == 623454

    def test_unambiguous_name_still_works(self):
        fake_result = pd.DataFrame({
            "name_first": ["bryan"], "name_last": ["woo"], "key_mlbam": [693433],
            "mlb_played_first": [2023.0], "mlb_played_last": [2026.0],
        })
        with patch("pybaseball.playerid_lookup", return_value=fake_result):
            assert lookup_pitcher_id("Bryan Woo") == 693433

    def test_no_match_returns_none_not_a_guess(self):
        with patch("pybaseball.playerid_lookup", return_value=pd.DataFrame()):
            assert lookup_pitcher_id("Nobody Real") is None

    def test_true_tie_in_recency_returns_none(self):
        # Two different real players who both last played the same year --
        # recency can't break this tie, so it must still fail closed.
        fake_result = pd.DataFrame({
            "name_first": ["j", "j"], "name_last": ["smith", "smith"],
            "key_mlbam": [111, 222],
            "mlb_played_first": [2020.0, 2021.0], "mlb_played_last": [2026.0, 2026.0],
        })
        with patch("pybaseball.playerid_lookup", return_value=fake_result):
            assert lookup_pitcher_id("J Smith") is None


class TestDedupeScoreboard:
    """Real bug found running the Checkpoint 9 shadow script: 188 real
    STATUS_FINAL scoreboard rows were only 135 real unique games —
    NormalizedStore.write() appends on every call with no primary-key
    enforcement, so repeated collection duplicates identical-content rows.
    This silently inflated Checkpoint 6's training sample with
    non-independent duplicate rows of the same game. Regression: same
    event_id, different observed_at_utc, must collapse to one row.
    """

    def test_duplicate_event_id_collapses_to_one_row(self):
        df = pl.DataFrame({
            "event_id": ["401816384", "401816384", "401816385"],
            "observed_at_utc": [
                "2026-08-05T10:33:04+00:00", "2026-08-05T10:42:34+00:00", "2026-08-05T10:33:04+00:00",
            ],
            "home_score": [3, 3, 5],
            "status": ["STATUS_FINAL", "STATUS_FINAL", "STATUS_FINAL"],
        })

        deduped = dedupe_scoreboard(df)

        assert deduped.height == 2
        assert sorted(deduped["event_id"].to_list()) == ["401816384", "401816385"]

    def test_keeps_the_most_recently_observed_row(self):
        df = pl.DataFrame({
            "event_id": ["1", "1"],
            "observed_at_utc": ["2026-08-05T10:00:00+00:00", "2026-08-05T12:00:00+00:00"],
            "home_score": [0, 5],  # later observation has the real final score
        })

        deduped = dedupe_scoreboard(df)

        assert deduped.height == 1
        assert deduped["home_score"][0] == 5

    def test_empty_input_returns_empty(self):
        df = pl.DataFrame({"event_id": [], "observed_at_utc": []})
        assert dedupe_scoreboard(df).is_empty()
