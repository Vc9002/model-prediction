"""One auditable WNBA point-in-time eligibility gate."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl


def eligible_prior_team_games(
    games: pl.DataFrame,
    team_box: pl.DataFrame,
    *,
    team_id: str,
    decision_time_utc: datetime,
) -> pl.DataFrame:
    if decision_time_utc.tzinfo is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    decision = decision_time_utc.astimezone(UTC)
    event_cutoff = decision
    required_games = {
        "event_id",
        "event_start_utc",
        "sports_event_date",
        "observed_at_utc",
        "raw_snapshot_hash",
        "completed",
        "pit_eligible",
    }
    required_box = {
        "event_id",
        "team_id",
        "observed_at_utc",
        "raw_snapshot_hash",
        "pit_eligible",
    }
    if not required_games.issubset(games.columns) or not required_box.issubset(team_box.columns):
        raise ValueError("WNBA PIT input is missing required provenance columns")
    prior_games = (
        games.with_columns(
            pl.col("observed_at_utc").str.to_datetime(time_zone="UTC", strict=True).alias("_observed"),
            pl.col("event_start_utc").str.to_datetime(time_zone="UTC", strict=True).alias("_event_start"),
        )
        .filter(pl.col("_observed") <= decision)
        .sort("_observed")
        .group_by("event_id", maintain_order=True)
        .last()
        # Latest-as-of state must be selected before checking completed or
        # PIT eligibility. Otherwise an older FINAL survives a later
        # correction to postponed/not-completed.
        .filter(pl.col("completed") & pl.col("pit_eligible") & (pl.col("_event_start") < event_cutoff))
        .select(
            "event_id",
            "event_start_utc",
            "sports_event_date",
            pl.col("raw_snapshot_hash").alias("game_raw_snapshot_hash"),
        )
    )
    eligible_boxes = (
        team_box.with_columns(
            pl.col("observed_at_utc").str.to_datetime(time_zone="UTC", strict=True).alias("_observed")
        )
        .filter(pl.col("_observed") <= decision)
        .sort("_observed")
        .group_by(["event_id", "team_id"], maintain_order=True)
        .last()
        .filter((pl.col("team_id") == team_id) & pl.col("pit_eligible"))
        .drop("_observed")
    )
    return eligible_boxes.join(prior_games, on="event_id", how="inner", suffix="_game").sort(
        "event_start_utc"
    )
