"""Structural and provenance audit for normalized NFL seasons."""

from __future__ import annotations

from typing import Any

import polars as pl

from .store import NFLNormalizedStore


def _duplicates(frame: pl.DataFrame, keys: list[str]) -> int:
    if frame.is_empty() or not set(keys).issubset(frame.columns):
        return 0
    return frame.group_by(keys).len().filter(pl.col("len") > 1).height


def audit_nfl_season(store: NFLNormalizedStore, season: int) -> dict[str, Any]:
    games = store.read_season("games", season)
    plays = store.read_season("plays", season)
    rosters = store.read_season("weekly_rosters", season)
    completed_ids = (
        set(games.filter(pl.col("completed"))["event_id"].to_list()) if not games.is_empty() else set()
    )
    complete_pbp_ids = (
        set(plays.filter(pl.col("game_complete"))["event_id"].to_list()) if not plays.is_empty() else set()
    )
    timestamp_violations = sum(
        frame.filter(
            (pl.col("observed_at_utc") > pl.col("retrieved_at_utc"))
            | (pl.col("effective_at_utc") > pl.col("observed_at_utc"))
        ).height
        for frame in (games, plays, rosters)
        if not frame.is_empty()
    )
    report: dict[str, Any] = {
        "sport": "nfl",
        "season": season,
        "games": games.select("event_id").n_unique() if not games.is_empty() else 0,
        "plays": plays.height,
        "weekly_roster_rows": rosters.height,
        "duplicate_games": _duplicates(games, ["event_id", "observed_at_utc"]),
        "duplicate_plays": _duplicates(plays, ["event_id", "play_id", "observed_at_utc"]),
        "missing_complete_pbp_games": len(completed_ids - complete_pbp_ids),
        "timestamp_violations": timestamp_violations,
    }
    hard_fail = any(report[key] for key in ("duplicate_games", "duplicate_plays", "timestamp_violations"))
    degraded = (
        report["games"] == 0 or report["missing_complete_pbp_games"] > 0 or report["weekly_roster_rows"] == 0
    )
    report["status"] = "ERROR" if hard_fail else ("DEGRADED" if degraded else "HEALTHY")
    report["qualification_note"] = (
        "Mutable nflverse releases captured now are not retrospective PIT evidence; "
        "only vintages observed by a decision time are eligible."
    )
    return report
