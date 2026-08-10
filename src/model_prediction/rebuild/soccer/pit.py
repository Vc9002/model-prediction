"""Fail-closed point-in-time eligibility for soccer observations."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from .rights import assert_research_shadow_allowed


def eligible_matches_as_of(
    matches: pl.DataFrame,
    decision_time_utc: datetime,
    *,
    completed_only: bool = False,
) -> pl.DataFrame:
    # Rights/provenance validation intentionally happens before row filtering;
    # a malformed future row cannot disappear and make the dataset look safe.
    assert_research_shadow_allowed(matches)
    if decision_time_utc.tzinfo is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    required = {
        "source",
        "source_match_id",
        "observed_at_utc",
        "available_at_utc",
        "event_start_utc",
        "timestamp_valid",
        "pit_eligible",
        "completed",
    }
    missing = sorted(required - set(matches.columns))
    if missing:
        raise ValueError(f"soccer PIT input is missing required columns: {missing}")
    decision = decision_time_utc.astimezone(UTC).isoformat()
    eligible = matches.filter(
        pl.col("timestamp_valid")
        & pl.col("pit_eligible")
        & (pl.col("observed_at_utc") <= decision)
        & (pl.col("available_at_utc") <= decision)
    )
    if completed_only:
        eligible = eligible.filter(pl.col("completed") & (pl.col("event_start_utc") < decision))
    return (
        eligible.sort("observed_at_utc")
        .unique(subset=["source", "source_match_id"], keep="last", maintain_order=True)
        .sort(["event_start_utc", "source_match_id"])
    )


def prior_team_matches_as_of(
    matches: pl.DataFrame,
    *,
    team_id: str,
    decision_time_utc: datetime,
) -> pl.DataFrame:
    eligible = eligible_matches_as_of(matches, decision_time_utc, completed_only=True)
    return eligible.filter((pl.col("home_team_id") == team_id) | (pl.col("away_team_id") == team_id))
