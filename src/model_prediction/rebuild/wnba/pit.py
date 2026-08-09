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
    decision = decision_time_utc.astimezone(UTC).isoformat()
    required_games = {"event_id", "event_start_utc", "observed_at_utc", "completed", "pit_eligible"}
    required_box = {"event_id", "team_id", "observed_at_utc", "pit_eligible"}
    if not required_games.issubset(games.columns) or not required_box.issubset(team_box.columns):
        raise ValueError("WNBA PIT input is missing required provenance columns")
    prior_games = (
        games.filter(
            pl.col("completed")
            & pl.col("pit_eligible")
            & (pl.col("event_start_utc") < decision)
            & (pl.col("observed_at_utc") <= decision)
        )
        .sort("observed_at_utc")
        .group_by("event_id", maintain_order=True)
        .last()
        .select(["event_id", "event_start_utc"])
    )
    eligible_boxes = (
        team_box.filter(
            (pl.col("team_id") == team_id)
            & pl.col("pit_eligible")
            & (pl.col("observed_at_utc") <= decision)
        )
        .sort("observed_at_utc")
        .group_by(["event_id", "team_id"], maintain_order=True)
        .last()
    )
    return (
        eligible_boxes
        .join(prior_games, on="event_id", how="inner", suffix="_game")
        .sort("event_start_utc")
    )
