"""Point-in-time MLB probable-starter availability, shadow feature.

Cross-references the ESPN-reported probable starter (from
``data_sources/espn_probables.py``'s point-in-time archive) against MLB
Stats API's dated IL-transaction history (``data_sources/mlb_injuries.py``)
to catch cases where an announced starter is actually unavailable.

Unlike WNBA's official injury-report PDFs -- a genuinely prospective-only
document; there is no way to ask what an old PDF said about a future date --
MLB Stats API transactions are retroactively queryable: a capture fetched at
any time, covering a date range that includes ``game_date``, can reconstruct
availability as of ``game_date``, because the point-in-time discipline lives
in each transaction's own ``reported_date`` field, not in when the snapshot
file happened to be written. See ``data_sources/mlb_injuries.py``'s module
docstring for why ``reported_date`` (the API's ``date`` field) -- and never
``effective_date`` (the API's ``effectiveDate``, sometimes retroactively
backdated) -- is the only field ever used to decide what was knowable as of
a given decision time.

v1 scope: probable-starter unavailability only (a binary flag per side), no
lineup-regular/position-player tracking, no probability-adjustment wiring
(shadow: computed and logged only, never adjusts a live forecast).
"""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from ..data_sources.espn_probables import point_in_time_probable_starters
from ..data_sources.mlb_injuries import (
    AVAILABLE_TRANSACTION_MARKERS,
    INJURY_OR_ADMIN_LIST_STATUSES,
    STATUS_ACTIVE_PROBABILITIES,
    UNAVAILABLE_TRANSACTION_MARKERS,
    team_id_for_name,
)
from ..domain import parse_utc

FEATURE_NAMES = {
    "probable_starter_unavailable_home",
    "probable_starter_unavailable_away",
    "availability_report_age_hours",
}


def _identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in normalized if not unicodedata.combining(character))
    return " ".join(ascii_value.replace("-", " ").casefold().split())


def _latest_transactions_snapshot(
    root: Path, team_id: int, cutoff_date: str
) -> dict[str, Any]:
    """Most recent captured transactions snapshot covering ``team_id``
    through ``cutoff_date``.

    Retroactively queryable by construction (see module docstring): a
    capture fetched well after ``cutoff_date`` is still valid, since
    correctness comes from filtering each entry's own ``reported_date`` in
    ``_starter_status``, not from when the snapshot file was written.
    """
    snapshot_root = root / "availability" / "mlb" / "snapshots"
    eligible: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in snapshot_root.glob("*/txn-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            observed = parse_utc(str(payload["observed_at_utc"]))
            end_date = str(payload["end_date"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if team_id in payload.get("team_ids", []) and end_date >= cutoff_date:
            eligible.append((observed, path, payload))
    if not eligible:
        raise ValueError(
            f"NO_CALL_MLB_AVAILABILITY_UNAVAILABLE: no captured transactions data for team {team_id} through {cutoff_date}"
        )
    return max(eligible, key=lambda item: (item[0], str(item[1])))[2]


def _starter_status(
    payload: dict[str, Any], team_id: int, starter_name: str, observed: datetime
) -> str:
    """Status of ``starter_name`` as of ``observed``, from the latest
    IL-relevant transaction reported strictly before ``observed``'s own
    calendar day.

    MLB Stats API's transaction ``date`` field (stored as ``reported_date``)
    has no time-of-day component -- only a calendar date. A transaction
    reported on the SAME calendar day as the decision could have happened
    before or after the decision within that day, and there is no way to
    tell which from the data alone. Same-day transactions are therefore
    excluded entirely (strict ``<``, not ``<=``) rather than assumed safe --
    the conservative, fail-closed choice, matching this project's standing
    rule that ambiguous timing is treated as unknowable, not as knowable.
    This also means a decision horizon set to a day before the game
    (rather than game day itself) correctly gets a tighter cutoff, not the
    game date's cutoff -- a real precision improvement over comparing
    against game_date, not just a same-day patch.

    Real MLB Stats API ``type_desc`` is almost never discriminative for IL
    moves (both a placement and an activation typically read "Status
    Change"); the real detail is free text in ``description``, matched here
    against ``UNAVAILABLE_TRANSACTION_MARKERS``/``AVAILABLE_TRANSACTION_MARKERS``.
    A player's real transaction history also includes plenty of moves that
    say nothing about IL status (trades, options, recalls, waiver claims) --
    those are silently ignored rather than treated as an unrecognized/failed
    status, since only the IL-relevant subset is ordered to find the latest.
    No IL-relevant history found (or none before the cutoff) is treated as
    Active, not a fail-closed case -- the overwhelming majority of probable
    starters have a clean record, and requiring an explicit "Active"
    transaction for every start would make this feature fire NO_CALL for
    nearly every game.
    """
    cutoff_date = observed.date().isoformat()
    relevant: list[tuple[str, str]] = []
    for entry in payload.get("entries", []):
        if entry.get("team_id") != team_id:
            continue
        if _identity(str(entry.get("player_name", ""))) != _identity(starter_name):
            continue
        reported = entry.get("reported_date")
        if not reported or str(reported) >= cutoff_date:
            continue
        description = str(entry.get("description") or "").casefold()
        if any(marker in description for marker in AVAILABLE_TRANSACTION_MARKERS):
            relevant.append((str(reported), "Active"))
        elif any(marker in description for marker in UNAVAILABLE_TRANSACTION_MARKERS):
            relevant.append((str(reported), str(entry.get("description") or "Unavailable")))
    if not relevant:
        return "Active"
    return max(relevant, key=lambda item: item[0])[1]


def _latest_roster_snapshot(
    root: Path, team_id: int, observed_at: datetime, maximum_age_hours: float
) -> dict[str, Any] | None:
    """Most recent LIVE roster snapshot for ``team_id`` captured no more
    than ``maximum_age_hours`` before ``observed_at``.

    Unlike transactions, a roster snapshot reports only *current* status
    (see mlb_injuries.py's module docstring) -- it can never be safely used
    for a decision time in the past relative to when it was captured (that
    would leak knowledge the decision could not have had), so a capture
    from AFTER ``observed_at`` is never eligible, only strictly-before and
    within the freshness window. Returns None (triggering the transactions-
    based fallback in ``_side_status``) when nothing qualifies.
    """
    snapshot_root = root / "availability" / "mlb" / "snapshots"
    best: tuple[datetime, dict[str, Any]] | None = None
    for path in snapshot_root.glob("*/roster-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            captured = parse_utc(str(payload["observed_at_utc"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if team_id not in payload.get("team_ids", []):
            continue
        age_hours = (observed_at - captured).total_seconds() / 3600
        if not 0 <= age_hours <= maximum_age_hours:
            continue
        if best is None or captured > best[0]:
            best = (captured, payload)
    return best[1] if best else None


def _side_status(
    root: Path,
    team_id: int,
    starter_name: str,
    game_date: str,
    observed: datetime,
    maximum_report_age_hours: float,
) -> tuple[str, datetime, str]:
    """Resolve one side's starter status.

    Prefers a fresh live roster read when one exists: it is a direct,
    current-status signal, immune to the transactions-based path's
    inherent blind spot (a placement transaction older than the
    transactions capture's own lookback window, e.g. a 60-Day IL player
    with no intervening rehab-assignment transaction to catch it in a
    60-day rolling capture). Falls back to the transactions-based
    reconstruction -- the only option for a historical/backtest decision
    time, since a live-only roster read can never cover the past -- when no
    sufficiently fresh roster snapshot exists.

    Returns (status, source_captured_at, source_label).
    """
    roster = _latest_roster_snapshot(root, team_id, observed, maximum_report_age_hours)
    if roster is not None:
        roster_status = None
        for entry in roster.get("entries", []):
            if entry.get("team_id") == team_id and _identity(str(entry.get("player_name", ""))) == _identity(
                starter_name
            ):
                roster_status = str(entry.get("current_status", ""))
                break
        if roster_status is not None:
            if roster_status not in STATUS_ACTIVE_PROBABILITIES:
                raise ValueError(
                    "NO_CALL_MLB_AVAILABILITY_STATUS_UNKNOWN: unrecognized roster status "
                    f"{roster_status!r} for {starter_name}"
                )
            status = "Active" if STATUS_ACTIVE_PROBABILITIES[roster_status] >= 1.0 else roster_status
            return status, parse_utc(str(roster["observed_at_utc"])), "roster"
    snapshot = _latest_transactions_snapshot(root, team_id, game_date)
    status = _starter_status(snapshot, team_id, starter_name, observed)
    return status, parse_utc(str(snapshot["observed_at_utc"])), "transactions"


def matchup_player_availability(
    *,
    data_root: str | Path,
    home_team: str,
    away_team: str,
    game_date: str,
    observed_at: datetime,
    event_start: datetime,
    event_id: str,
    maximum_report_age_hours: float = 24.0,
) -> dict[str, Any]:
    """Build probable-starter availability flags or fail closed."""
    observed = parse_utc(observed_at.isoformat())
    start = parse_utc(event_start.isoformat())
    if observed >= start:
        raise ValueError("NO_CALL_MLB_AVAILABILITY_INVALID_TIME: observation is not pregame")

    try:
        starters = point_in_time_probable_starters(event_id, observed_at)
    except ValueError as error:
        raise ValueError(f"NO_CALL_MLB_AVAILABILITY_UNAVAILABLE: {error}") from error

    root = Path(data_root)
    home_id = team_id_for_name(home_team)
    away_id = team_id_for_name(away_team)

    home_status, home_captured, home_source = _side_status(
        root, home_id, starters["home_starter"], game_date, observed, maximum_report_age_hours
    )
    away_status, away_captured, away_source = _side_status(
        root, away_id, starters["away_starter"], game_date, observed, maximum_report_age_hours
    )

    def _age_hours(captured: datetime, source: str) -> float:
        if source == "roster":
            # _latest_roster_snapshot already enforces captured <= observed
            # and the freshness bound; this is just that same gap restated.
            return (observed - captured).total_seconds() / 3600
        # Transactions: a capture fetched after `observed` (retroactive
        # reconstruction, e.g. a backtest) reflects complete knowledge
        # through its own end_date and is treated as fully fresh (0h) --
        # staleness is only a meaningful risk for a live capture that ran
        # before `observed` and may have missed a very recent,
        # not-yet-ingested transaction.
        return max(0.0, (observed - captured).total_seconds() / 3600)

    age_hours = max(_age_hours(home_captured, home_source), _age_hours(away_captured, away_source))
    if age_hours > maximum_report_age_hours:
        raise ValueError(
            f"NO_CALL_MLB_AVAILABILITY_STALE: capture age={age_hours:.2f}h, "
            f"max={maximum_report_age_hours:.2f}h"
        )

    return {
        "probable_starter_unavailable_home": 0.0 if home_status == "Active" else 1.0,
        "probable_starter_unavailable_away": 0.0 if away_status == "Active" else 1.0,
        "availability_report_age_hours": round(age_hours, 10),
        "home_probable_starter": starters["home_starter"],
        "away_probable_starter": starters["away_starter"],
        "home_probable_starter_status": home_status,
        "away_probable_starter_status": away_status,
        "home_probable_starter_source": home_source,
        "away_probable_starter_source": away_source,
    }


# ── Pitching-staff (bullpen) availability ───────────────────────────────
#
# Roadmap item (docs/MODEL_IMPROVEMENTS.md section 8, rank 3: "Bullpen
# availability and quality... closer/setup absence"). MLB Stats API roster
# data identifies position TYPE (Pitcher vs. everyone else), not bullpen
# ROLE (closer/setup/long), so this is coarser than a true closer-specific
# signal -- but it's a real, point-in-time-correct pitching-staff-health
# read this project has never had, reusing the same live roster snapshot
# infrastructure (and its freshness/future-capture safeguards) as
# probable-starter availability above. Live-only by construction, same as
# the roster-preferred path above: a fresh roster snapshot cannot cover a
# historical/backtest decision time, only a near-live one -- there is no
# transactions-based fallback here, since "how many pitchers are on the
# active roster right now" isn't reconstructable from the IL-transaction
# log alone (a team's active roster size/composition also changes via
# trades, options, and call-ups that a pure IL-transaction filter wouldn't
# capture correctly).

PITCHING_STAFF_FEATURE_NAMES = {
    "pitching_staff_unavailable_share_home",
    "pitching_staff_unavailable_share_away",
    "pitching_staff_unavailable_share_gap",
}


def team_pitching_staff_availability(
    *,
    data_root: str | Path,
    team: str,
    observed_at: datetime,
    maximum_report_age_hours: float = 24.0,
) -> dict[str, Any]:
    """Share of ``team``'s current pitching staff unavailable due to injury
    or an administrative list, from a fresh live 40-man roster snapshot.

    "Current pitching staff" excludes pitchers optioned to the minors
    (``Reassigned to Minors``) from both numerator and denominator -- that
    status is routine 40-man-vs-26-man roster depth management, present for
    every team on any given day regardless of health, not a meaningful
    availability signal. Including it would swamp the real signal (actual
    injuries, typically a handful of players) with structural noise
    (routinely a third or more of a 40-man roster on any given day).
    """
    team_id = team_id_for_name(team)
    root = Path(data_root)
    roster = _latest_roster_snapshot(root, team_id, observed_at, maximum_report_age_hours)
    if roster is None:
        raise ValueError(
            f"NO_CALL_MLB_PITCHING_STAFF_UNAVAILABLE: no fresh roster snapshot for {team}"
        )
    all_pitchers = [
        entry
        for entry in roster.get("entries", [])
        if entry.get("team_id") == team_id and entry.get("position_type") == "Pitcher"
    ]
    staff = [entry for entry in all_pitchers if str(entry.get("current_status") or "") != "Reassigned to Minors"]
    if not staff:
        raise ValueError(f"NO_CALL_MLB_PITCHING_STAFF_UNAVAILABLE: no pitchers found on roster for {team}")
    unknown = [
        entry for entry in staff if str(entry.get("current_status") or "") not in STATUS_ACTIVE_PROBABILITIES
    ]
    if unknown:
        raise ValueError(
            "NO_CALL_MLB_AVAILABILITY_STATUS_UNKNOWN: unrecognized roster status "
            f"{unknown[0].get('current_status')!r} for {unknown[0].get('player_name')}"
        )
    unavailable = [entry for entry in staff if str(entry["current_status"]) in INJURY_OR_ADMIN_LIST_STATUSES]
    return {
        "pitching_staff_total": len(staff),
        "pitching_staff_unavailable": len(unavailable),
        "pitching_staff_unavailable_share": round(len(unavailable) / len(staff), 10),
        "report_captured_at_utc": str(roster["observed_at_utc"]),
    }


def matchup_pitching_staff_availability(
    *,
    data_root: str | Path,
    home_team: str,
    away_team: str,
    observed_at: datetime,
    event_start: datetime,
    maximum_report_age_hours: float = 24.0,
) -> dict[str, Any]:
    """Home-oriented pitching-staff availability gap, or fail closed.

    Positive gap means the away team's pitching staff has a larger
    unavailable share -- favors home -- same sign convention as
    features/player_availability.py's availability_points_gap.
    """
    observed = parse_utc(observed_at.isoformat())
    start = parse_utc(event_start.isoformat())
    if observed >= start:
        raise ValueError("NO_CALL_MLB_PITCHING_STAFF_INVALID_TIME: observation is not pregame")
    home = team_pitching_staff_availability(
        data_root=data_root,
        team=home_team,
        observed_at=observed_at,
        maximum_report_age_hours=maximum_report_age_hours,
    )
    away = team_pitching_staff_availability(
        data_root=data_root,
        team=away_team,
        observed_at=observed_at,
        maximum_report_age_hours=maximum_report_age_hours,
    )
    return {
        "pitching_staff_unavailable_share_home": home["pitching_staff_unavailable_share"],
        "pitching_staff_unavailable_share_away": away["pitching_staff_unavailable_share"],
        "pitching_staff_unavailable_share_gap": round(
            away["pitching_staff_unavailable_share"] - home["pitching_staff_unavailable_share"], 10
        ),
        "home_pitching_staff_total": home["pitching_staff_total"],
        "away_pitching_staff_total": away["pitching_staff_total"],
    }
