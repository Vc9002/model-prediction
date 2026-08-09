"""Structural and provenance audit for normalized WNBA seasons."""

from __future__ import annotations

from typing import Any

import polars as pl

from .store import WNBANormalizedStore


def _duplicate_count(frame: pl.DataFrame, keys: list[str]) -> int:
    if frame.is_empty() or not set(keys).issubset(frame.columns):
        return 0
    return frame.group_by(keys).len().filter(pl.col("len") > 1).height


def audit_wnba_season(store: WNBANormalizedStore, season: int) -> dict[str, Any]:
    games = store.read_season("games", season)
    team_box = store.read_season("team_box", season)
    player_box = store.read_season("player_box", season)
    rosters = store.read_season("rosters", season)

    expected_games = games.select("event_id").n_unique() if not games.is_empty() else 0
    completed = games.filter(pl.col("completed")) if not games.is_empty() else games
    completed_ids = set(completed["event_id"].to_list()) if not completed.is_empty() else set()
    team_box_ids = set(team_box["event_id"].to_list()) if not team_box.is_empty() else set()
    player_box_ids = set(player_box["event_id"].to_list()) if not player_box.is_empty() else set()

    missing_final_scores = 0
    missing_teams = 0
    if not completed.is_empty():
        missing_final_scores = completed.filter(
            pl.col("home_score").is_null() | pl.col("away_score").is_null()
        ).height
    if not games.is_empty():
        missing_teams = games.filter(
            (pl.col("home_team_id") == "") | (pl.col("away_team_id") == "")
        ).height

    timestamp_violations = 0
    for frame in (games, team_box, player_box, rosters):
        if not frame.is_empty():
            timestamp_violations += frame.filter(
                pl.col("observed_at_utc") > pl.col("retrieved_at_utc")
            ).height

    report: dict[str, Any] = {
        "sport": "wnba",
        "season": season,
        "games_expected": expected_games,
        "games_present": expected_games,
        "duplicate_events": _duplicate_count(games, ["event_id", "observed_at_utc"]),
        "missing_final_scores": missing_final_scores,
        "missing_teams": missing_teams,
        "missing_team_boxscores": len(completed_ids - team_box_ids),
        "missing_player_boxscores": len(completed_ids - player_box_ids),
        "team_box_rows": team_box.height,
        "player_box_rows": player_box.height,
        "roster_rows": rosters.height,
        "missing_player_ids": (
            player_box.filter(pl.col("player_id") == "").height if not player_box.is_empty() else 0
        ),
        "timestamp_violations": timestamp_violations,
        "timestamp_valid_rows": sum(
            frame.filter(pl.col("pit_eligible")).height
            for frame in (team_box, player_box, rosters)
            if not frame.is_empty()
        ),
    }
    hard_fail = any(
        report[key] > 0
        for key in ("duplicate_events", "missing_final_scores", "missing_teams", "timestamp_violations")
    )
    degraded = any(
        report[key] > 0
        for key in ("missing_team_boxscores", "missing_player_boxscores", "missing_player_ids")
    )
    report["status"] = "ERROR" if hard_fail else ("DEGRADED" if degraded else "HEALTHY")
    report["qualification_note"] = (
        "Capture-time-only historical releases are not retrospective PIT evidence; "
        "model qualification remains blocked until replay-safe vintages exist."
    )
    return report
