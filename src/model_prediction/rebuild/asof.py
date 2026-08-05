"""Strict point-in-time join utility.

Never join by game date alone when an observation timestamp exists.
Every join enforces: observation.observed_at_utc <= decision.decision_time_utc.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

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
    obs_renamed = observations.rename({
        col: f"obs_{col}" for col in observations.columns
        if col not in entity_keys
    })

    # Build the join condition: match entity keys + PIT constraint
    join_on = entity_keys.copy()

    # Perform an asof join: for each decision, find the newest observation
    # whose timestamp is <= the decision timestamp
    result = decisions.join_asof(
        obs_renamed.sort(observation_time_col),
        on=join_on,
        left_on=decision_time_col,
        right_on=f"obs_{observation_time_col}",
        strategy="nearest",
    )

    # Hard invariant: no observation timestamp after decision time
    if result.height > 0:
        obs_time = result.get_column(f"obs_{observation_time_col}")
        dec_time = result.get_column(decision_time_col)
        violations = (obs_time > dec_time).sum()
        if violations and violations > 0:
            raise AssertionError(
                f"PIT violation: {violations} rows have observation time "
                f"after decision time"
            )

    # Max age filter
    if max_age is not None and result.height > 0:
        obs_time = result.get_column(f"obs_{observation_time_col}")
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
