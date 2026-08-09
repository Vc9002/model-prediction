"""Structural and provenance audit for normalized WNBA seasons."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Any

import polars as pl

from .store import WNBANormalizedStore


def _duplicate_count(frame: pl.DataFrame, keys: list[str]) -> int:
    if frame.is_empty() or not set(keys).issubset(frame.columns):
        return 0
    return frame.group_by(keys).len().filter(pl.col("len") > 1).height


def _latest(frame: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    """Select latest valid UTC observation without hiding invalid rows."""

    if frame.is_empty() or not set(keys + ["observed_at_utc"]).issubset(frame.columns):
        return pl.DataFrame()
    return (
        frame.with_columns(
            pl.col("observed_at_utc")
            .str.to_datetime(time_zone="UTC", strict=False)
            .alias("_observed")
        )
        .filter(pl.col("_observed").is_not_null())
        .sort("_observed")
        .group_by(keys, maintain_order=True)
        .last()
        .drop("_observed")
    )


def _timestamp_violations(frame: pl.DataFrame) -> int:
    if frame.is_empty():
        return 0
    required = {"observed_at_utc", "retrieved_at_utc"}
    if not required.issubset(frame.columns):
        return frame.height
    checked = frame.with_columns(
        pl.col("observed_at_utc").str.to_datetime(time_zone="UTC", strict=False).alias("_observed"),
        pl.col("retrieved_at_utc")
        .str.to_datetime(time_zone="UTC", strict=False)
        .alias("_retrieved"),
    )
    return checked.filter(
        pl.col("_observed").is_null()
        | pl.col("_retrieved").is_null()
        | (pl.col("_observed") > pl.col("_retrieved"))
    ).height


def audit_wnba_season(
    store: WNBANormalizedStore,
    season: int,
    *,
    expected_event_ids: AbstractSet[str] | None = None,
) -> dict[str, Any]:
    """Audit raw observations and compare coverage to an independent expectation.

    ``expected_event_ids`` must come from a separately acquired schedule or
    release inventory. Deriving the expected count from the normalized games
    table would make a truncated load look complete.
    """

    observations = {
        table: store.read_observations(table, season)
        for table in ("games", "team_box", "player_box", "rosters")
    }
    games = _latest(observations["games"], store.BUSINESS_KEYS["games"])
    team_box = _latest(observations["team_box"], store.BUSINESS_KEYS["team_box"])
    player_box = _latest(observations["player_box"], store.BUSINESS_KEYS["player_box"])
    rosters = _latest(observations["rosters"], store.BUSINESS_KEYS["rosters"])

    present_ids = set(games["event_id"].to_list()) if not games.is_empty() else set()
    expected_ids = set(expected_event_ids) if expected_event_ids is not None else None
    completed = games.filter(pl.col("completed")) if not games.is_empty() else games
    completed_ids = set(completed["event_id"].to_list()) if not completed.is_empty() else set()
    team_box_ids = set(team_box["event_id"].to_list()) if not team_box.is_empty() else set()
    player_box_ids = set(player_box["event_id"].to_list()) if not player_box.is_empty() else set()

    missing_final_scores = 0
    missing_teams = 0
    missing_canonical_team_ids = 0
    missing_canonical_player_ids = 0
    if not completed.is_empty():
        missing_final_scores = completed.filter(
            pl.col("home_score").is_null() | pl.col("away_score").is_null()
        ).height
    if not games.is_empty():
        missing_teams = games.filter(
            (pl.col("home_team_id") == "") | (pl.col("away_team_id") == "")
        ).height
        canonical_columns = {"home_team_canonical_id", "away_team_canonical_id"}
        if not canonical_columns.issubset(games.columns):
            missing_canonical_team_ids += games.height
        else:
            missing_canonical_team_ids += games.filter(
                pl.col("home_team_canonical_id").is_null()
                | pl.col("away_team_canonical_id").is_null()
                | (pl.col("home_team_canonical_id") == "")
                | (pl.col("away_team_canonical_id") == "")
            ).height
    for frame in (team_box, player_box, rosters):
        if not frame.is_empty():
            if "team_canonical_id" not in frame.columns:
                missing_canonical_team_ids += frame.height
            else:
                missing_canonical_team_ids += frame.filter(
                    pl.col("team_canonical_id").is_null()
                    | (pl.col("team_canonical_id") == "")
                ).height
    for frame in (player_box, rosters):
        if not frame.is_empty():
            if "player_canonical_id" not in frame.columns:
                missing_canonical_player_ids += frame.height
            else:
                missing_canonical_player_ids += frame.filter(
                    pl.col("player_canonical_id").is_null()
                    | (pl.col("player_canonical_id") == "")
                ).height

    duplicate_observations = {
        table: _duplicate_count(frame, store.observation_keys(table))
        for table, frame in observations.items()
    }
    report: dict[str, Any] = {
        "sport": "wnba",
        "season": season,
        "games_expected": len(expected_ids) if expected_ids is not None else None,
        "expected_games_basis": (
            "independent_event_ids" if expected_ids is not None else "UNAVAILABLE"
        ),
        "games_present": len(present_ids),
        "expected_events_missing": (
            len(expected_ids - present_ids) if expected_ids is not None else None
        ),
        "unexpected_events_present": (
            len(present_ids - expected_ids) if expected_ids is not None else None
        ),
        "duplicate_events": duplicate_observations["games"],
        "duplicate_observations": duplicate_observations,
        "missing_final_scores": missing_final_scores,
        "missing_teams": missing_teams,
        "missing_canonical_team_ids": missing_canonical_team_ids,
        "missing_canonical_player_ids": missing_canonical_player_ids,
        "missing_team_boxscores": len(completed_ids - team_box_ids),
        "missing_player_boxscores": len(completed_ids - player_box_ids),
        "team_box_rows": team_box.height,
        "player_box_rows": player_box.height,
        "roster_rows": rosters.height,
        "missing_player_ids": (
            player_box.filter(pl.col("player_id") == "").height
            if not player_box.is_empty()
            else 0
        ),
        "timestamp_violations": sum(_timestamp_violations(frame) for frame in observations.values()),
        "timestamp_valid_rows": sum(
            frame.filter(pl.col("pit_eligible")).height
            for frame in (team_box, player_box, rosters)
            if not frame.is_empty() and "pit_eligible" in frame.columns
        ),
    }
    hard_fail = any(
        report[key] > 0
        for key in (
            "duplicate_events",
            "missing_final_scores",
            "missing_teams",
            "missing_canonical_team_ids",
            "missing_canonical_player_ids",
            "timestamp_violations",
        )
    ) or any(value > 0 for value in duplicate_observations.values())
    incomplete_expectation = expected_ids is None or bool(report["expected_events_missing"])
    degraded = incomplete_expectation or any(
        report[key] > 0
        for key in ("missing_team_boxscores", "missing_player_boxscores", "missing_player_ids")
    )
    if hard_fail:
        report["status"] = "ERROR"
    elif not present_ids:
        report["status"] = "UNAVAILABLE"
    else:
        report["status"] = "DEGRADED" if degraded else "HEALTHY"
    report["qualification_note"] = (
        "Capture-time-only historical releases are not retrospective PIT evidence; "
        "model qualification remains blocked until replay-safe vintages exist."
    )
    return report
