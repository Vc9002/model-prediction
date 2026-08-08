"""Strict point-in-time join utility.

Never join by game date alone when an observation timestamp exists.
Every join enforces: observation.observed_at_utc <= decision.decision_time_utc.
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl


def point_in_time_join(
    decisions: pl.DataFrame,
    observations: pl.DataFrame,
    *,
    entity_keys: list[str],
    decision_time_col: str = "decision_time_utc",
    observation_time_col: str = "observed_at_utc",
    max_age: timedelta | None = None,
) -> pl.DataFrame:
    """Join observations to decisions using strict point-in-time rules.

    For each decision row, finds the newest observation row where:
      1. observation.{observation_time_col} <= decision.{decision_time_col}
      2. All entity_keys match between the two rows
      3. If max_age is set, (decision_time - observation_time) <= max_age

    Returns a DataFrame with one row per decision. Rows with no matching
    observation have null values for observation columns.

    Raises AssertionError if any joined observation has a timestamp
    after the decision time (indicating a data pipeline bug).
    """
    if decisions.height == 0:
        return decisions

    if observations.height == 0:
        return decisions

    # Rename observation columns to avoid clashes, prefix with "obs_"
    obs_time_renamed = f"obs_{observation_time_col}"
    obs_renamed = observations.rename({
        col: f"obs_{col}" for col in observations.columns
        if col not in entity_keys
    })

    # join_asof requires both frames sorted by their respective `on` column
    # (within each `by` group). entity_keys go in `by` (exact match, like a
    # regular join's `on`) — they are not the asof column. Real bug fixed
    # here (see outputs/rebuild/takeover_status.md): the previous version
    # passed entity_keys as `on=`, which join_asof treats as the *asof*
    # column, not an exact-match key, while also passing left_on/right_on —
    # an invalid combination. It also sorted by the observation time
    # column's pre-rename name after already renaming it, which raised
    # ColumnNotFoundError on every real call — this function has never
    # successfully run once, and had zero test coverage. It's also dead
    # code: nothing in this repo actually calls it (verified via grep) —
    # every real feature builder in mlb_features.py implements its own
    # point-in-time filtering directly instead.
    decisions_sorted = decisions.sort(decision_time_col)
    obs_sorted = obs_renamed.sort(obs_time_renamed)

    result = decisions_sorted.join_asof(
        obs_sorted,
        left_on=decision_time_col,
        right_on=obs_time_renamed,
        by=entity_keys,
        strategy="backward",
        # Both frames are already sorted immediately above; polars can't
        # verify sortedness within `by` groups itself and would otherwise
        # warn on every call.
        check_sortedness=False,
    )

    # Hard invariant: no observation timestamp after decision time
    if result.height > 0:
        obs_time = result.get_column(obs_time_renamed)
        dec_time = result.get_column(decision_time_col)
        violations = (obs_time > dec_time).sum()
        if violations and violations > 0:
            raise AssertionError(
                f"PIT violation: {violations} rows have observation time "
                f"after decision time"
            )

    # Max age filter
    if max_age is not None and result.height > 0:
        obs_time = result.get_column(obs_time_renamed)
        dec_time = result.get_column(decision_time_col)
        age = dec_time - obs_time
        mask = age <= max_age
        # Null out observations that are too old
        obs_cols = [c for c in result.columns if c.startswith("obs_")]
        for col in obs_cols:
            result = result.with_columns(
                pl.when(mask).then(result[col]).otherwise(None).alias(col)
            )

    return result
