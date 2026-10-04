"""Strict completed scoreboard normalization for isolated research scoring."""

from __future__ import annotations

import math
from typing import Any

from .research_forward import aware_time
from .research_generation import digest

SCHEMA = "espn-scoreboard-result-v1"


def score(value: Any) -> int:
    if value is None or isinstance(value, bool) or value == "":
        raise ValueError("missing_score")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number != int(number):
        raise ValueError("invalid_score")
    return int(number)


def completed_espn_events(
    board: dict[str, Any], sport: str, observed_at_utc: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows, unavailable = [], []
    observed = aware_time(observed_at_utc)
    seen = set()
    for event in board["events"]:
        try:
            if not event.get("id") or event["id"] in seen:
                raise ValueError("missing_or_duplicate_event_identity")
            seen.add(event["id"])
            competition = event["competitions"][0]
            status = competition["status"]["type"]
            if status.get("completed") is not True or status.get("state") != "post":
                raise ValueError("not_completed")
            if aware_time(event["date"]) >= observed:
                raise ValueError("result_before_event_start")
            teams = competition["competitors"]
            by_side = {t["homeAway"]: t for t in teams}
            if len(teams) != 2 or set(by_side) != {"home", "away"}:
                raise ValueError("ambiguous_participants")
            home, away = by_side["home"], by_side["away"]
            if not home["team"]["id"] or not away["team"]["id"] or home["team"]["id"] == away["team"]["id"]:
                raise ValueError("invalid_participant_ids")
            row = {
                "source_schema": SCHEMA,
                "event_id": str(event["id"]),
                "event_start_utc": event["date"],
                "game_start_utc": event["date"],
                "status": "completed",
                "league": sport,
                "observed_at_utc": observed_at_utc,
                "source_event_hash": digest(event),
                "completed_periods": competition["status"].get("period"),
            }
            for side, team in by_side.items():
                row[side + "_score"] = score(team.get("score"))
                row[side + "_team_id"] = str(team["team"]["id"])
                row[side + "_team"] = team["team"]["displayName"]
                row[side] = {"team_id": str(team["team"]["id"]), "team_name": team["team"]["displayName"]}
                first = [inning for inning in team.get("linescores", []) if inning.get("period") == 1]
                try:
                    row["first_inning_runs_" + side] = (
                        score(first[0].get("value")) if sport == "MLB" and len(first) == 1 else None
                    )
                except (ValueError, TypeError):
                    row["first_inning_runs_" + side] = None
            rows.append(row)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            unavailable.append({"event_id": event.get("id"), "reason": str(exc)})
    return rows, unavailable
