"""Canonical WNBA league-date semantics.

WNBA schedule slates are keyed to the league's US Eastern operating date,
not to the UTC calendar date.  A late evening game therefore remains on the
same WNBA slate even when its UTC timestamp is after midnight.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

WNBA_SPORTS_TIMEZONE_NAME = "America/New_York"
WNBA_SPORTS_TIMEZONE = ZoneInfo(WNBA_SPORTS_TIMEZONE_NAME)


def sports_event_date(event_start_utc: str | datetime) -> str:
    """Return the authoritative WNBA sports date for an event start."""

    parsed = (
        event_start_utc
        if isinstance(event_start_utc, datetime)
        else datetime.fromisoformat(event_start_utc)
    )
    if parsed.tzinfo is None:
        raise ValueError("WNBA event start must be timezone-aware")
    return parsed.astimezone(UTC).astimezone(WNBA_SPORTS_TIMEZONE).date().isoformat()
