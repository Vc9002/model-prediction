"""Tests for real MLB pregame features built from normalized Statcast pitches
(src/model_prediction/rebuild/mlb_features.py) — replaces the rolling-score
placeholder used in scripts/pipeline_mlb_e2e.py. See
outputs/rebuild/takeover_status.md Checkpoint 5.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pandas as pd
import polars as pl
import pytest

from model_prediction.rebuild.mlb_features import (
    build_game_feature_row,
    bullpen_rolling_features,
    dedupe_scoreboard,
    identify_starters,
    lookup_pitcher_id,
    normalize_statcast_pitches,
    park_factor,
    pitcher_clean_rate_features,
    pitcher_rolling_features,
    point_in_time_probable_starters,
    resolve_horizon_starter_names,
)


def _pitch_row(
    game_pk: int, game_date: str, pitcher: int, at_bat_number: int, pitch_number: int,
    *, inning_topbot: str = "Top", home_team: str = "HOME", away_team: str = "AWAY",
    description: str = "called_strike", events: str | None = None,
    release_speed: float = 93.0, pitcher_days_since_prev_game: int = 4,
    inning: int = 1, bat_score: int = 0, post_bat_score: int = 0,
) -> dict:
    return {
        "game_pk": game_pk, "game_date": game_date, "pitcher": pitcher,
        "batter": 1, "home_team": home_team, "away_team": away_team,
        "inning": inning, "inning_topbot": inning_topbot,
        "at_bat_number": at_bat_number, "pitch_number": pitch_number,
        "pitch_type": "FF", "release_speed": release_speed, "release_spin_rate": 2200,
        "description": description, "events": events, "zone": 5,
        "p_throws": "R", "stand": "R",
        "pitcher_days_since_prev_game": pitcher_days_since_prev_game,
        "n_thruorder_pitcher": 1,
        "bat_score": bat_score, "post_bat_score": post_bat_score,
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


class TestPitcherCleanRateFeatures:
    """CLAUDE.md Part 1 SS10's "Pitcher clean-rate group" -- real beta-
    binomial-shrunk rates computed directly from Statcast's real
    bat_score/post_bat_score run-scoring fields, previously not wired
    anywhere (pitcher_clean_rate_shrink() in missingness.py had zero real
    callers, grep-verified)."""

    def test_no_prior_history_reports_unavailable_not_a_guess(self):
        pitches = normalize_statcast_pitches(pl.DataFrame([_pitch_row(1, "2026-08-05", 100, 1, 1)]))
        feats = pitcher_clean_rate_features(pitches, 999, before_game_date="2026-08-01")
        assert feats["availability"] == 0.0
        assert feats["clean_appearance_rate"] == 0.0
        assert feats["clean_appearance_n"] == 0.0

    def test_future_starts_never_leak_into_a_past_decision(self):
        rows = [
            # Before the decision date: a clean start.
            _pitch_row(10, "2026-07-20", 100, 1, 1, inning=1, bat_score=0, post_bat_score=0),
            # After the decision date: pitcher allows a run -- must not
            # influence a feature computed for before_game_date=2026-08-01.
            _pitch_row(20, "2026-08-10", 100, 1, 1, inning=1, bat_score=0, post_bat_score=1),
        ]
        pitches = normalize_statcast_pitches(pl.DataFrame(rows))
        feats = pitcher_clean_rate_features(pitches, 100, before_game_date="2026-08-01")

        assert feats["availability"] == 1.0
        assert feats["clean_appearance_rate"] > 0.5, (
            "a future start where the pitcher allowed a run leaked into a feature "
            "computed for an earlier decision date"
        )
        assert feats["clean_appearance_n"] == 1.0

    def test_rates_computed_from_real_run_scoring_data(self):
        rows = [
            # Start 1 (game_pk=10, 2026-07-20): fully clean -- no runs in
            # inning 1 or inning 2.
            _pitch_row(10, "2026-07-20", 100, 1, 1, inning=1, bat_score=0, post_bat_score=0),
            _pitch_row(10, "2026-07-20", 100, 1, 2, inning=1, bat_score=0, post_bat_score=0),
            _pitch_row(10, "2026-07-20", 100, 2, 1, inning=2, bat_score=0, post_bat_score=0),
            _pitch_row(10, "2026-07-20", 100, 2, 2, inning=2, bat_score=0, post_bat_score=0),
            # Start 2 (game_pk=11, 2026-07-25): clean first inning, but
            # allows 1 run in inning 2 -- not a clean appearance, and
            # inning 2 is not a scoreless inning.
            _pitch_row(11, "2026-07-25", 100, 1, 1, inning=1, bat_score=0, post_bat_score=0),
            _pitch_row(11, "2026-07-25", 100, 1, 2, inning=1, bat_score=0, post_bat_score=0),
            _pitch_row(11, "2026-07-25", 100, 2, 1, inning=2, bat_score=0, post_bat_score=1),
        ]
        pitches = normalize_statcast_pitches(pl.DataFrame(rows))
        feats = pitcher_clean_rate_features(pitches, 100, before_game_date="2026-08-01")

        assert feats["availability"] == 1.0
        # Raw: 2/2 starts had a clean inning 1.
        assert feats["first_inning_clean_n"] == 2.0
        # Raw: 3/4 real (game, inning) pairs were scoreless -- (10,1),
        # (10,2), (11,1) clean; (11,2) is not.
        assert feats["scoreless_inning_n"] == 4.0
        # Raw: 1/2 starts were a fully clean appearance -- game_pk=10
        # clean, game_pk=11 allowed a run in inning 2.
        assert feats["clean_appearance_n"] == 2.0

        # Beta-binomial posterior means with the real default league prior
        # (alpha=5.0, beta=5.0) -- hand-computed from the real counts
        # above, not just "some number between 0 and 1".
        assert feats["first_inning_clean_rate"] == pytest.approx((5.0 + 2) / (5.0 + 5.0 + 2), abs=1e-9)
        assert feats["scoreless_inning_rate"] == pytest.approx((5.0 + 3) / (5.0 + 5.0 + 4), abs=1e-9)
        assert feats["clean_appearance_rate"] == pytest.approx((5.0 + 1) / (5.0 + 5.0 + 2), abs=1e-9)

    def test_data_without_bat_score_reports_unavailable_not_a_crash(self):
        # normalize_statcast_pitches() only keeps bat_score/post_bat_score
        # when the source data actually has them -- an older or synthetic
        # dataset missing them must fail closed to availability=0, not
        # raise on a missing column.
        pitches = pl.DataFrame([
            {"game_pk": 1, "game_date": "2026-07-20", "pitcher": 100, "inning": 1,
             "inning_topbot": "Top", "home_team": "HOME", "away_team": "AWAY"},
        ]).with_columns(pl.col("game_date").str.slice(0, 10).alias("game_date_str"))
        feats = pitcher_clean_rate_features(pitches, 100, before_game_date="2026-08-01")
        assert feats["availability"] == 0.0


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


def _probable_record(event_id: str, observed_at_utc: str, home_starter: str, away_starter: str) -> dict:
    return {
        "event_id": event_id, "observed_at_utc": observed_at_utc,
        "home_starter": home_starter, "away_starter": away_starter,
    }


class TestPointInTimeProbableStarters:
    """FOUNDATION_COMPLETION.md Phase 3: wires the shared point_in_time_join()
    utility into a real caller for the first time. Real gap fixed: a naive
    `{rec["event_id"]: rec for rec in records}` keeps whichever record
    happens to be last in the file for an event, not the newest observation
    strictly before that game's real decision_time_utc."""

    def test_uses_the_newest_observation_strictly_before_decision_time(self):
        decision_times = {"401": datetime.fromisoformat("2026-08-06T22:10:00+00:00")}
        records = [
            _probable_record("401", "2026-08-05T10:00:00+00:00", "Pitcher A", "Pitcher X"),
            _probable_record("401", "2026-08-06T18:00:00+00:00", "Pitcher B", "Pitcher Y"),  # real revision
        ]

        result = point_in_time_probable_starters(decision_times, records)

        assert result["401"] == {"home_starter": "Pitcher B", "away_starter": "Pitcher Y"}

    def test_a_revision_published_after_decision_time_does_not_leak_in(self):
        # The real bug this closes: "last record in the file" would have
        # picked this late revision even though it was observed *after* the
        # late horizon's real decision cutoff.
        decision_times = {"401": datetime.fromisoformat("2026-08-06T22:10:00+00:00")}
        records = [
            _probable_record("401", "2026-08-05T10:00:00+00:00", "Pitcher A", "Pitcher X"),
            _probable_record("401", "2026-08-06T23:00:00+00:00", "Pitcher B", "Pitcher Y"),  # after cutoff
        ]

        result = point_in_time_probable_starters(decision_times, records)

        assert result["401"] == {"home_starter": "Pitcher A", "away_starter": "Pitcher X"}

    def test_event_with_no_qualifying_observation_is_absent_not_guessed(self):
        decision_times = {"401": datetime.fromisoformat("2026-08-06T22:10:00+00:00")}
        records = [
            _probable_record("401", "2026-08-06T23:00:00+00:00", "Pitcher A", "Pitcher X"),  # only future
        ]

        result = point_in_time_probable_starters(decision_times, records)

        assert "401" not in result

    def test_only_events_in_decision_times_are_considered(self):
        decision_times = {"401": datetime.fromisoformat("2026-08-06T22:10:00+00:00")}
        records = [
            _probable_record("401", "2026-08-05T10:00:00+00:00", "Pitcher A", "Pitcher X"),
            _probable_record("999", "2026-08-05T10:00:00+00:00", "Other Home", "Other Away"),
        ]

        result = point_in_time_probable_starters(decision_times, records)

        assert set(result.keys()) == {"401"}

    def test_empty_inputs_return_empty(self):
        assert point_in_time_probable_starters({}, [_probable_record("401", "2026-08-05T10:00:00+00:00", "A", "X")]) == {}
        assert point_in_time_probable_starters(
            {"401": datetime.fromisoformat("2026-08-06T22:10:00+00:00")}, [],
        ) == {}


def _pit_probable(event_id, observed_at_utc, home_starter, away_starter, *, pit_eligible=True):
    return {
        "event_id": event_id, "observed_at_utc": observed_at_utc,
        "home_starter": home_starter, "away_starter": away_starter,
        "pit_eligible": pit_eligible,
    }


def _espn_game(event_id="401", event_start_utc="2026-08-06T22:10:00+00:00"):
    return {
        "event_id": event_id, "event_start_utc": event_start_utc,
        "home_team": "Seattle Mariners", "away_team": "Detroit Tigers",
        "home_score": 4, "away_score": 2, "venue": "T-Mobile Park",
    }


class TestResolveHorizonStarterNames:
    """Task 1 (historical starter train-serving parity): historical
    training must resolve starters the same point-in-time-safe way live
    inference does -- never from the completed game's own actual pitcher.
    The exact regression case specified for this fix: a starter revision
    observed after the late horizon's decision time must not leak in."""

    def test_late_horizon_uses_the_probable_known_at_decision_time_not_a_later_revision(self):
        # event_start_utc=22:10Z, so late decision_time = 21:10Z (T-60m).
        game = _espn_game()
        records = [
            _pit_probable("401", "2026-08-06T16:00:00+00:00", "Pitcher A", "Away Pitcher"),
            # Real revision, but observed at 21:40Z -- *after* the late
            # decision cutoff of 21:10Z. Must not leak into a "late" row.
            _pit_probable("401", "2026-08-06T21:40:00+00:00", "Pitcher B", "Away Pitcher"),
        ]

        home, _away, missing_reason = resolve_horizon_starter_names(game, "late", records)

        assert home == "Pitcher A"
        assert missing_reason is None

    def test_early_horizon_at_the_same_game_only_sees_the_earlier_probable_too(self):
        # Sanity check that the fix is horizon-aware, not just a fixed
        # cutoff -- the "early" decision time (T-36h) is well before both
        # real observations here, so neither is usable yet.
        game = _espn_game()
        records = [
            _pit_probable("401", "2026-08-06T16:00:00+00:00", "Pitcher A", "Away Pitcher"),
        ]

        home, away, missing_reason = resolve_horizon_starter_names(game, "early", records)

        assert (home, away) == (None, None)
        assert missing_reason == "no_valid_probable_at_horizon"

    def test_retroactively_scraped_record_is_never_used_even_if_timestamp_would_pass(self):
        # pit_eligible=False means this was scraped after the fact
        # (provenance="retroactive_or_unverifiable_non_pit") -- its
        # observed_at_utc doesn't reflect a real pregame observation and
        # must never be trusted as a genuine probable, even though its
        # timestamp alone would satisfy the point-in-time filter.
        game = _espn_game()
        records = [
            _pit_probable("401", "2026-08-06T16:00:00+00:00", "Pitcher A", "Away Pitcher", pit_eligible=False),
        ]

        home, away, missing_reason = resolve_horizon_starter_names(game, "late", records)

        assert (home, away) == (None, None)
        assert missing_reason == "no_valid_probable_at_horizon"

    def test_no_records_at_all_fails_closed_with_a_real_reason_not_none(self):
        home, away, missing_reason = resolve_horizon_starter_names(_espn_game(), "late", [])
        assert (home, away) == (None, None)
        assert missing_reason == "no_valid_probable_at_horizon"

    def test_invalid_horizon_raises(self):
        with pytest.raises(ValueError, match="horizon"):
            resolve_horizon_starter_names(_espn_game(), "nonsense", [])


class TestBuildGameFeatureRowStarterParity:
    """End-to-end regression for Task 1: build_game_feature_row() must
    never fall back to identify_starters()'s actual-pitcher-of-record for
    the game being featurized, even when a real point-in-time-valid
    probable exists that names someone else (e.g. a real late starter
    swap). Proven here by giving the ACTUAL Statcast-inferred starter and
    the real point-in-time PROBABLE starter distinct, real prior-history
    signals (a different avg_velocity each) and asserting the row carries
    the probable's signal, not the actual starter's."""

    def _pitches_and_starters(self):
        # This completed game (game_pk=100): pitcher 2 actually threw
        # SEA's first pitch -- the real, actual starter of record.
        this_game = [
            _pitch_row(100, "2026-08-06", pitcher=2, at_bat_number=1, pitch_number=1,
                       home_team="SEA", away_team="DET", release_speed=80.0),
        ]
        # Real prior starts (before 2026-08-06) for each pitcher, with
        # deliberately distinct velocities so the test can tell which
        # pitcher's history actually fed the row.
        prior = [
            _pitch_row(50, "2026-08-01", pitcher=1, at_bat_number=1, pitch_number=1,
                       home_team="SEA", away_team="DET", release_speed=99.0),  # Pitcher A
            _pitch_row(51, "2026-07-31", pitcher=2, at_bat_number=1, pitch_number=1,
                       home_team="SEA", away_team="DET", release_speed=80.0),  # Pitcher B (actual starter)
        ]
        pitches = normalize_statcast_pitches(pl.DataFrame(this_game + prior))
        return pitches, identify_starters(pitches)

    def test_uses_the_real_point_in_time_probable_not_the_actual_completed_game_starter(self):
        game = _espn_game()
        # Real point-in-time probable at the late decision time: Pitcher A
        # -- Pitcher B (the real actual starter, per Statcast) only shows
        # up in a revision observed after the late cutoff and must not be
        # used for a "late" row.
        records = [
            _pit_probable("401", "2026-08-06T16:00:00+00:00", "Pitcher A", "Away Pitcher"),
            _pit_probable("401", "2026-08-06T21:40:00+00:00", "Pitcher B", "Away Pitcher"),
        ]
        pitches, starters = self._pitches_and_starters()

        with patch(
            "model_prediction.rebuild.mlb_features.lookup_pitcher_id",
            side_effect=lambda name: {"Pitcher A": 1, "Pitcher B": 2, "Away Pitcher": 3}.get(name),
        ):
            row = build_game_feature_row(game, pitches, starters, "data/rebuild", "late", records)

        assert row is not None
        assert row["starters_known"] == 1.0
        assert row["starter_missing_reason"] == ""
        # Pitcher A's real prior avg_velocity (99.0) -- not Pitcher B's
        # (80.0), even though Pitcher B is who actually started this game.
        assert row["home_sp_avg_velocity"] == pytest.approx(99.0)

    def test_no_valid_probable_zeroes_starter_features_instead_of_using_the_actual_starter(self):
        game = _espn_game()
        pitches, starters = self._pitches_and_starters()

        # No probable-starter archive at all for this event -- must not
        # silently fall back to Pitcher B (the real actual starter).
        row = build_game_feature_row(game, pitches, starters, "data/rebuild", "late", [])

        assert row is not None
        assert row["starters_known"] == 0.0
        assert row["starter_missing_reason"] == "no_valid_probable_at_horizon"
        assert row["home_sp_availability"] == 0.0
        assert row["home_sp_avg_velocity"] == 0.0

    def test_name_that_cannot_be_resolved_to_a_statcast_id_also_zeroes_not_falls_back(self):
        game = _espn_game()
        records = [
            _pit_probable("401", "2026-08-06T16:00:00+00:00", "Unresolvable Name", "Away Pitcher"),
        ]
        pitches, starters = self._pitches_and_starters()

        with patch("model_prediction.rebuild.mlb_features.lookup_pitcher_id", return_value=None):
            row = build_game_feature_row(game, pitches, starters, "data/rebuild", "late", records)

        assert row is not None
        assert row["starters_known"] == 0.0
        assert row["starter_missing_reason"] == "starter_name_not_resolved_to_statcast_id"
        assert row["home_sp_avg_velocity"] == 0.0
