"""Strict point-in-time gates for NFL release data."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl


def _decision_iso(decision_time_utc: datetime) -> str:
    if decision_time_utc.tzinfo is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    return decision_time_utc.astimezone(UTC).isoformat()


def _latest_eligible(
    frame: pl.DataFrame,
    *,
    decision: str,
    keys: list[str],
) -> pl.DataFrame:
    required = {*keys, "observed_at_utc", "effective_at_utc", "pit_eligible"}
    if not required.issubset(frame.columns):
        raise ValueError(
            f"NFL PIT input is missing required columns: {sorted(required - set(frame.columns))}"
        )
    return (
        frame.filter(
            pl.col("pit_eligible")
            & (pl.col("observed_at_utc") <= decision)
            & (pl.col("effective_at_utc") <= decision)
        )
        .sort("observed_at_utc")
        .unique(subset=keys, keep="last", maintain_order=True)
    )


def eligible_prior_team_plays(
    games: pl.DataFrame,
    plays: pl.DataFrame,
    *,
    team_id: str,
    decision_time_utc: datetime,
    exclude_event_id: str | None = None,
) -> pl.DataFrame:
    """Return only complete, observed plays from games prior to a decision."""
    decision = _decision_iso(decision_time_utc)
    required_games = {"event_id", "event_start_utc", "completed"}
    required_plays = {"event_id", "play_id", "posteam_id", "defteam_id", "game_complete"}
    if not required_games.issubset(games.columns) or not required_plays.issubset(plays.columns):
        raise ValueError("NFL PIT game/play input is missing required structural columns")

    latest_games = _latest_eligible(games, decision=decision, keys=["event_id"]).filter(
        pl.col("completed") & (pl.col("event_start_utc") < decision)
    )
    if exclude_event_id is not None:
        latest_games = latest_games.filter(pl.col("event_id") != exclude_event_id)
    latest_plays = _latest_eligible(
        plays,
        decision=decision,
        keys=["event_id", "play_id"],
    ).filter(
        pl.col("game_complete") & ((pl.col("posteam_id") == team_id) | (pl.col("defteam_id") == team_id))
    )
    return latest_plays.join(
        latest_games.select(["event_id", "event_start_utc"]),
        on="event_id",
        how="inner",
    ).sort(["event_start_utc", "play_id"])


def eligible_weekly_roster(
    rosters: pl.DataFrame,
    *,
    team_id: str,
    season: int,
    week: int,
    decision_time_utc: datetime,
) -> pl.DataFrame:
    """Return the latest captured roster vintage available by decision time."""
    decision = _decision_iso(decision_time_utc)
    required = {"season", "week", "team_id", "player_id"}
    if not required.issubset(rosters.columns):
        raise ValueError("NFL roster PIT input is missing required structural columns")
    selected = rosters.filter(
        (pl.col("season") == season) & (pl.col("week") == week) & (pl.col("team_id") == team_id)
    )
    return _latest_eligible(selected, decision=decision, keys=["player_id"]).sort("player_id")
