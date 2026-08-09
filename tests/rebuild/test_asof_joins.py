"""Tests for the shared point-in-time join utility (asof.py).

Real bug found and fixed (see outputs/rebuild/takeover_status.md and
FOUNDATION_COMPLETION.md Phase 3): point_in_time_join had never worked —
it renamed the observation timestamp column, then sorted by its pre-rename
name (ColumnNotFoundError, verified live on the most basic real call), and
combined join_asof's `on` (an asof column) with `left_on`/`right_on` while
actually intending `on`'s argument (entity_keys) to be exact-match keys —
that's `by`, not `on`. It also defaulted away from "backward" wherever it
did work in theory. It had zero test coverage and, verified via grep, is
dead code — no real feature builder in this repo calls it; each one
implements its own point-in-time filtering directly instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

from model_prediction.rebuild.asof import point_in_time_join


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class TestBasicBackwardSemantics:
    def test_future_observation_is_excluded(self):
        decisions = pl.DataFrame({
            "entity": ["A"], "decision_time_utc": [_dt("2026-01-10")],
        })
        observations = pl.DataFrame({
            "entity": ["A"], "observed_at_utc": [_dt("2026-01-15")], "value": [999],
        })

        result = point_in_time_join(decisions, observations, entity_keys=["entity"])

        assert result["obs_value"][0] is None, "a future-only observation must not be selected"

    def test_newest_prior_observation_is_selected(self):
        decisions = pl.DataFrame({
            "entity": ["A"], "decision_time_utc": [_dt("2026-01-20")],
        })
        observations = pl.DataFrame({
            "entity": ["A", "A", "A"],
            "observed_at_utc": [_dt("2026-01-05"), _dt("2026-01-15"), _dt("2026-01-25")],
            "value": [1, 2, 3],
        })

        result = point_in_time_join(decisions, observations, entity_keys=["entity"])

        assert result["obs_value"][0] == 2, "must pick the newest observation strictly before the decision"

    def test_no_prior_observation_returns_null_not_a_future_leak(self):
        decisions = pl.DataFrame({
            "entity": ["A"], "decision_time_utc": [_dt("2026-01-01")],
        })
        observations = pl.DataFrame({
            "entity": ["A"], "observed_at_utc": [_dt("2026-01-15")], "value": [999],
        })

        result = point_in_time_join(decisions, observations, entity_keys=["entity"])

        assert result["obs_value"][0] is None
        assert result["obs_observed_at_utc"][0] is None


class TestEntityKeyIsolation:
    def test_observations_do_not_leak_across_entities(self):
        # Same-day-doubleheader-style case: two entities, decisions and
        # observations must never cross entity boundaries.
        decisions = pl.DataFrame({
            "entity": ["A", "B"],
            "decision_time_utc": [_dt("2026-01-10"), _dt("2026-01-10")],
        })
        observations = pl.DataFrame({
            "entity": ["A", "B"],
            "observed_at_utc": [_dt("2026-01-05"), _dt("2026-01-09")],
            "value": [111, 222],
        })

        result = point_in_time_join(decisions, observations, entity_keys=["entity"]).sort("entity")

        assert result["obs_value"].to_list() == [111, 222]

    def test_multi_column_entity_keys_work(self):
        # e.g. (team, season) or (game_pk, side) style composite keys.
        decisions = pl.DataFrame({
            "team": ["A", "A"], "season": [2025, 2026],
            "decision_time_utc": [_dt("2026-01-10"), _dt("2026-01-10")],
        })
        observations = pl.DataFrame({
            "team": ["A", "A"], "season": [2025, 2026],
            "observed_at_utc": [_dt("2026-01-01"), _dt("2026-01-01")],
            "value": [2025, 2026],
        })

        result = point_in_time_join(
            decisions, observations, entity_keys=["team", "season"],
        ).sort("season")

        assert result["obs_value"].to_list() == [2025, 2026], (
            "a 2025-season decision must not match the 2026-season observation despite the same team"
        )


class TestMaxAge:
    def test_stale_observation_beyond_max_age_becomes_unavailable(self):
        decisions = pl.DataFrame({
            "entity": ["A"], "decision_time_utc": [_dt("2026-01-20")],
        })
        observations = pl.DataFrame({
            "entity": ["A"], "observed_at_utc": [_dt("2026-01-01")], "value": [1],
        })

        result = point_in_time_join(
            decisions, observations, entity_keys=["entity"], max_age=timedelta(days=5),
        )

        assert result["obs_value"][0] is None, "a 19-day-old observation must fail a 5-day max_age"

    def test_fresh_observation_within_max_age_is_kept(self):
        decisions = pl.DataFrame({
            "entity": ["A"], "decision_time_utc": [_dt("2026-01-20")],
        })
        observations = pl.DataFrame({
            "entity": ["A"], "observed_at_utc": [_dt("2026-01-18")], "value": [1],
        })

        result = point_in_time_join(
            decisions, observations, entity_keys=["entity"], max_age=timedelta(days=5),
        )

        assert result["obs_value"][0] == 1


class TestEmptyInputs:
    def test_empty_decisions_returns_empty(self):
        decisions = pl.DataFrame({"entity": [], "decision_time_utc": []}, schema={"entity": pl.Utf8, "decision_time_utc": pl.Datetime})
        observations = pl.DataFrame({
            "entity": ["A"], "observed_at_utc": [_dt("2026-01-01")], "value": [1],
        })
        result = point_in_time_join(decisions, observations, entity_keys=["entity"])
        assert result.height == 0

    def test_empty_observations_returns_decisions_unmatched(self):
        decisions = pl.DataFrame({
            "entity": ["A"], "decision_time_utc": [_dt("2026-01-10")],
        })
        observations = pl.DataFrame(
            {"entity": [], "observed_at_utc": [], "value": []},
            schema={"entity": pl.Utf8, "observed_at_utc": pl.Datetime, "value": pl.Int64},
        )
        result = point_in_time_join(decisions, observations, entity_keys=["entity"])
        assert result.height == 1


class TestHardInvariant:
    def test_runs_without_raising_on_real_shaped_data(self):
        # Regression: this call raised ColumnNotFoundError unconditionally
        # before the fix -- the function had never completed a single real
        # call. No assertion beyond "does not raise" is needed for that
        # specific regression; correctness is covered by the tests above.
        decisions = pl.DataFrame({
            "entity": ["A", "B"],
            "decision_time_utc": [_dt("2026-01-10"), _dt("2026-01-12")],
        })
        observations = pl.DataFrame({
            "entity": ["A", "A", "B"],
            "observed_at_utc": [_dt("2026-01-01"), _dt("2026-01-09"), _dt("2026-01-11")],
            "value": [1, 2, 3],
        })
        point_in_time_join(decisions, observations, entity_keys=["entity"])
