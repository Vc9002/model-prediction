"""Point-in-time projected-minutes x player-impact availability features.

This deliberately refuses to reduce an injury report to counts.  A usable
feature requires a versioned pregame minutes/impact prior for every rotation
player and an official report snapshot that existed at decision time.
"""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain import parse_utc

FEATURE_NAMES = {
    "availability_points_gap",
    "home_available_minutes_share",
    "away_available_minutes_share",
    "availability_uncertainty",
    "availability_report_age_hours",
}
STATUS_PROBABILITY_VERSION = "wnba-status-prior-v1"
STATUS_ACTIVE_PROBABILITIES = {
    "Available": 1.0,
    "Probable": 0.90,
    "Questionable": 0.50,
    "Doubtful": 0.25,
    "Out": 0.0,
}
EXPECTED_TEAM_MINUTES = 200.0
MINIMUM_TEAM_MINUTES = 190.0
MAXIMUM_TEAM_MINUTES = 210.0


def _identity(value: str) -> str:
    if "," in value:
        last, first = value.split(",", 1)
        value = f"{first} {last}"
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in normalized if not unicodedata.combining(character))
    return " ".join(ascii_value.replace(",", " ").replace("-", " ").casefold().split())


def _latest_snapshot(
    data_root: Path,
    observed_at: datetime,
    game_date: str,
) -> dict[str, Any]:
    snapshot_root = data_root / "availability" / "wnba" / "snapshots"
    eligible: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in snapshot_root.glob("*/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            observed = parse_utc(str(payload["observed_at_utc"]))
            report_at = parse_utc(str(payload["report_at_utc"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if observed <= observed_at and report_at <= observed_at:
            report_dates = {str(entry.get("game_date")) for entry in payload.get("entries", [])}
            if game_date in report_dates:
                eligible.append((report_at, path, payload))
    if not eligible:
        raise ValueError(
            "NO_CALL_AVAILABILITY_UNAVAILABLE: no point-in-time official WNBA snapshot for game date"
        )
    return max(eligible, key=lambda item: (item[0], str(item[1])))[2]


def _load_priors(
    data_root: Path,
    game_date: str,
    observed_at: datetime,
    maximum_age_hours: float = 168.0,
) -> dict[str, Any]:
    root = data_root / "player_priors" / "wnba"
    eligible: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            as_of = parse_utc(str(payload["observed_at_utc"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            payload.get("schema_version") == "1"
            and payload.get("sport") == "wnba"
            and as_of <= observed_at
            and str(payload.get("valid_from")) <= game_date <= str(payload.get("valid_through"))
        ):
            age_hours = (observed_at - as_of).total_seconds() / 3600
            if age_hours > maximum_age_hours:
                continue  # Prior too old
            eligible.append((as_of, path, payload))
    if not eligible:
        raise ValueError("NO_CALL_AVAILABILITY_PRIORS_UNAVAILABLE: no pregame WNBA minutes/impact priors")
    return max(eligible, key=lambda item: (item[0], str(item[1])))[2]


def _latest_espn_snapshot(data_root: Path, event_id: str, observed_at: datetime) -> dict[str, Any]:
    root = data_root / "availability/wnba/espn_event_snapshots"
    eligible: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in root.glob(f"*/{event_id}-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            observed = parse_utc(str(payload["observed_at_utc"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if observed <= observed_at:
            eligible.append((observed, path, payload))
    if not eligible:
        raise ValueError("NO_CALL_AVAILABILITY_ESPN_UNAVAILABLE: no point-in-time ESPN event status snapshot")
    return max(eligible, key=lambda item: (item[0], str(item[1])))[2]


def _team_rows(priors: Mapping[str, Any], team: str) -> list[Mapping[str, Any]]:
    rows = [
        row for row in priors.get("players", []) if _identity(str(row.get("team", ""))) == _identity(team)
    ]
    total_minutes = sum(float(row.get("projected_minutes", 0)) for row in rows)
    if not MINIMUM_TEAM_MINUTES <= total_minutes <= MAXIMUM_TEAM_MINUTES:
        raise ValueError(
            "NO_CALL_AVAILABILITY_PRIORS_INCOMPLETE: "
            f"{team} projected rotation minutes={total_minutes:.1f}, expected {MINIMUM_TEAM_MINUTES:.0f}-{MAXIMUM_TEAM_MINUTES:.0f}"
        )
    return rows


def _status_by_player(snapshot: Mapping[str, Any], team: str, game_date: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for entry in snapshot.get("entries", []):
        if _identity(str(entry.get("team", ""))) != _identity(team):
            continue
        if str(entry.get("game_date")) != game_date:
            continue
        status = str(entry.get("current_status", ""))
        if status not in STATUS_ACTIVE_PROBABILITIES:
            raise ValueError(f"NO_CALL_AVAILABILITY_STATUS_UNKNOWN: {status or '<missing>'}")
        statuses[_identity(str(entry.get("player_name", "")))] = status
    return statuses


def merge_availability_sources(
    official_snapshot: Mapping[str, Any],
    espn_snapshot: Mapping[str, Any] | None,
    *,
    game_date: str,
    conflict_policy: str = "fail_closed",
) -> dict[str, Any]:
    """Merge explicit ESPN statuses into the official report; fail closed on conflicts.

    An omission is not an explicit Available status, so a timestamped ESPN Out
    fills it. Two explicit, different statuses are a source conflict and force
    the downstream forecast to fail closed.  ``most_conservative`` exists
    only for labeled research sensitivity and selects the lower active
    probability while preserving the conflict in metadata — it must be
    explicitly requested; the default is fail_closed.
    """
    if conflict_policy not in {"fail_closed", "most_conservative"}:
        raise ValueError(f"unknown availability conflict policy: {conflict_policy}")
    merged = dict(official_snapshot)
    entries = [dict(entry) for entry in official_snapshot.get("entries", [])]
    source_conflicts: list[dict[str, Any]] = []
    by_player = {
        (_identity(str(entry.get("team", ""))), _identity(str(entry.get("player_name", "")))): entry
        for entry in entries
        if str(entry.get("game_date")) == game_date
    }
    if espn_snapshot is None:
        merged["entries"] = entries
        return merged
    for espn_entry in espn_snapshot.get("entries", []):
        return_date = espn_entry.get("return_date")
        if return_date and str(return_date) < game_date:
            continue
        key = (
            _identity(str(espn_entry.get("team", ""))),
            _identity(str(espn_entry.get("player_name", ""))),
        )
        existing = by_player.get(key)
        espn_status = str(espn_entry.get("current_status", ""))
        if existing is not None:
            official_status = str(existing.get("current_status", ""))
            if official_status != espn_status:
                conflict = {
                    "team": existing.get("team"),
                    "player_name": existing.get("player_name"),
                    "official_status": official_status,
                    "espn_status": espn_status,
                }
                if conflict_policy == "fail_closed":
                    raise ValueError(
                        "NO_CALL_AVAILABILITY_SOURCE_CONFLICT: "
                        f"{existing.get('player_name')} official={official_status} espn={espn_status}"
                    )
                if official_status not in STATUS_ACTIVE_PROBABILITIES:
                    raise ValueError(f"NO_CALL_AVAILABILITY_STATUS_UNKNOWN: {official_status}")
                if espn_status not in STATUS_ACTIVE_PROBABILITIES:
                    raise ValueError(f"NO_CALL_AVAILABILITY_STATUS_UNKNOWN: {espn_status}")
                source_conflicts.append(conflict)
                existing["current_status"] = min(
                    (official_status, espn_status),
                    key=STATUS_ACTIVE_PROBABILITIES.__getitem__,
                )
                existing["conflict_resolution"] = "most_conservative_research_only"
            existing["secondary_source"] = "espn_event_injuries"
            existing["secondary_status_at_utc"] = espn_entry.get("status_at_utc")
            continue
        added = {
            "game_date": game_date,
            "game_time_et": "",
            "matchup": "",
            "team": espn_entry.get("team"),
            "player_name": espn_entry.get("player_name"),
            "current_status": espn_status,
            "reason": espn_entry.get("reason", ""),
            "source": "espn_event_injuries",
            "status_at_utc": espn_entry.get("status_at_utc"),
        }
        entries.append(added)
        by_player[key] = added
    merged["entries"] = entries
    merged["sources_used"] = ["official_wnba_injury_report", "espn_event_injuries"]
    merged["source_conflicts"] = source_conflicts
    return merged


def _team_availability(
    team: str,
    rows: list[Mapping[str, Any]],
    statuses: Mapping[str, str],
) -> dict[str, float]:
    prior_by_player = {_identity(str(row.get("player_name", ""))): row for row in rows}
    missing = sorted(name for name in statuses if name not in prior_by_player)
    if missing:
        raise ValueError(
            "NO_CALL_AVAILABILITY_PLAYER_UNMAPPED: " + f"{team} missing priors for {', '.join(missing)}"
        )

    expected_available_minutes = 0.0
    expected_loss_points = 0.0
    uncertainty_variance = 0.0
    for name, row in prior_by_player.items():
        try:
            minutes = float(row["projected_minutes"])
            impact = float(row["impact_points_per_100"])
            replacement = float(row["replacement_impact_points_per_100"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"NO_CALL_AVAILABILITY_PRIORS_INVALID: {team} {name}") from error
        if not 0 <= minutes <= 40 or not all(math.isfinite(value) for value in (impact, replacement)):
            raise ValueError(f"NO_CALL_AVAILABILITY_PRIORS_INVALID: {team} {name}")
        probability = STATUS_ACTIVE_PROBABILITIES[statuses.get(name, "Available")]
        expected_available_minutes += probability * minutes
        # A 40-minute WNBA game has roughly 80 possessions.  Convert the
        # player's per-100-possession impact above her named replacement into
        # expected scoreboard points lost.  The 80-possession assumption is a
        # versioned initial prior, not a learned coefficient.
        value_if_absent = minutes / 40.0 * (impact - replacement) * 0.80
        expected_loss_points += (1.0 - probability) * value_if_absent
        uncertainty_variance += probability * (1.0 - probability) * value_if_absent**2
    return {
        "available_minutes_share": expected_available_minutes / EXPECTED_TEAM_MINUTES,
        "expected_loss_points": expected_loss_points,
        "uncertainty_variance": uncertainty_variance,
    }


def matchup_player_availability(
    *,
    data_root: str | Path,
    home_team: str,
    away_team: str,
    game_date: str,
    observed_at: datetime,
    event_start: datetime,
    event_id: str | None = None,
    maximum_report_age_hours: float = 12.0,
    maximum_prior_age_hours: float = 168.0,
) -> dict[str, Any]:
    """Build home-oriented WNBA availability features or fail closed."""
    observed = parse_utc(observed_at.isoformat())
    start = parse_utc(event_start.isoformat())
    if observed >= start:
        raise ValueError("NO_CALL_AVAILABILITY_INVALID_TIME: observation is not pregame")
    root = Path(data_root)
    snapshot = _latest_snapshot(root, observed, game_date)
    if event_id is not None:
        espn_snapshot: dict[str, Any] | None = None
        with suppress(ValueError, KeyError, TypeError, OSError):
            # Missing/malformed ESPN data is a routine, silent fall-back to
            # official-only -- ESPN often hasn't posted an event snapshot
            # yet. A genuine two-source *conflict* is not: merge_availability_sources
            # (default conflict_policy="fail_closed") must be called outside
            # this suppress, or its NO_CALL_AVAILABILITY_SOURCE_CONFLICT
            # ValueError gets silently swallowed here too, silently
            # discarding real disagreement (e.g. ESPN says a star is Out,
            # official still says Available) instead of forcing the
            # fail-closed NO_CALL this function's docstring promises.
            espn_snapshot = _latest_espn_snapshot(root, event_id, observed)
        if espn_snapshot is not None:
            snapshot = merge_availability_sources(snapshot, espn_snapshot, game_date=game_date)
    priors = _load_priors(root, game_date, observed, maximum_age_hours=maximum_prior_age_hours)
    result = matchup_player_availability_from_payloads(
        snapshot=snapshot,
        priors=priors,
        home_team=home_team,
        away_team=away_team,
        game_date=game_date,
        observed_at=observed,
        event_start=start,
        maximum_report_age_hours=maximum_report_age_hours,
    )
    conflicts = list(snapshot.get("source_conflicts", []))
    result["availability_source_conflict_count"] = len(conflicts)
    result["availability_source_conflicts"] = conflicts
    return result


def matchup_player_availability_from_payloads(
    *,
    snapshot: Mapping[str, Any],
    priors: Mapping[str, Any],
    home_team: str,
    away_team: str,
    game_date: str,
    observed_at: datetime,
    event_start: datetime,
    maximum_report_age_hours: float = 12.0,
) -> dict[str, Any]:
    """Pure calculator used by forward inference and labeled retrospective tests."""
    observed = parse_utc(observed_at.isoformat())
    start = parse_utc(event_start.isoformat())
    if observed >= start:
        raise ValueError("NO_CALL_AVAILABILITY_INVALID_TIME: observation is not pregame")
    report_at = parse_utc(str(snapshot["report_at_utc"]))
    age_hours = (observed - report_at).total_seconds() / 3600
    if age_hours < 0 or age_hours > maximum_report_age_hours:
        raise ValueError(
            f"NO_CALL_AVAILABILITY_STALE: official report age={age_hours:.2f}h, max={maximum_report_age_hours:.2f}h"
        )

    teams_listed = {_identity(str(team)) for team in snapshot.get("teams_listed", [])}
    absent = [team for team in (away_team, home_team) if _identity(team) not in teams_listed]
    if absent:
        raise ValueError(
            "NO_CALL_AVAILABILITY_REPORT_INCOMPLETE: teams absent from report: " + ", ".join(absent)
        )
    submission_status = {
        _identity(str(team)): str(status) for team, status in snapshot.get("team_report_status", {}).items()
    }
    incomplete = [
        team
        for team in (away_team, home_team)
        if submission_status.get(_identity(team)) not in {"submitted", "submitted_no_entries"}
    ]
    if incomplete:
        raise ValueError(
            "NO_CALL_AVAILABILITY_REPORT_INCOMPLETE: reports not submitted for " + ", ".join(incomplete)
        )
    home_rows = _team_rows(priors, home_team)
    away_rows = _team_rows(priors, away_team)
    home = _team_availability(home_team, home_rows, _status_by_player(snapshot, home_team, game_date))
    away = _team_availability(away_team, away_rows, _status_by_player(snapshot, away_team, game_date))
    return {
        # Positive means the away team loses more value, favoring the home team.
        "availability_points_gap": round(away["expected_loss_points"] - home["expected_loss_points"], 10),
        "home_available_minutes_share": round(home["available_minutes_share"], 10),
        "away_available_minutes_share": round(away["available_minutes_share"], 10),
        "availability_uncertainty": round(
            math.sqrt(home["uncertainty_variance"] + away["uncertainty_variance"]), 10
        ),
        "availability_report_age_hours": round(age_hours, 10),
    }
