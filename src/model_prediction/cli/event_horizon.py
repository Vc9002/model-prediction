"""Cheap "is there anything left to forecast today" check for the daily forecast tasks (quick 261122-spd).

Each per-sport forecast task discovers its games from one ESPN scoreboard (league, game date) and skips every
game that has already started, but only after it has loaded the sport's full history, artifacts, injuries and
efficiency tables. On a day with no game left, that work finishes with an empty result. This module answers the
same question first, from the same scoreboard, so the task can be skipped when it provably would have found
nothing.

Fail-open by construction: a skip happens only on a successful fetch of a well-formed scoreboard in which every
event has a parseable start time at or before the decision time. Anything else (a raised error, a payload that
is not a scoreboard, an event with no or an unparseable ``date``) means "unknown" and the task runs as before.
The decision time passed in must not be later than the time the tasks use for themselves, so a game that is
still upcoming for a task can never look started here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..config import is_sport_daily_enabled
from ..domain import parse_utc

logger = logging.getLogger("model_prediction.cli")

# League keys (config.models / ESPN scoreboard name) whose forecast tasks discover games from
# ``ESPNClient.scoreboard(league, args.date)`` and drop started games themselves. NCAAB already returns early
# on an empty scoreboard, and MLB totals/NRFI, esports and the rest use other discovery paths, so none of
# those are listed.
SCOREBOARD_LEAGUES: tuple[str, ...] = ("MLB", "NBA", "WNBA", "NFL", "NCAAF")


@dataclass(frozen=True)
class EventHorizon:
    """What one league's scoreboard holds relative to the decision time."""

    league: str
    total: int
    upcoming: int
    started_event_ids: tuple[str, ...]

    @property
    def nothing_to_forecast(self) -> bool:
        return self.upcoming == 0


def scoreboard_horizon(payload: Any, league: str, now: datetime) -> EventHorizon | None:
    """Classify a scoreboard payload; None when it is not a well-formed scoreboard."""
    if not isinstance(payload, Mapping):
        return None
    events = payload.get("events")
    if not isinstance(events, list):
        return None
    upcoming = 0
    started: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            upcoming += 1  # unreadable event: let the task decide
            continue
        try:
            start = parse_utc(str(event["date"]))
        except (KeyError, TypeError, ValueError):
            upcoming += 1  # no usable start time: never assume it has started
            continue
        if start > now:
            upcoming += 1
        else:
            started.append(str(event.get("id", "")))
    return EventHorizon(league, len(events), upcoming, tuple(started))


def probe_event_horizon(
    config: dict[str, Any],
    game_date: str,
    now: datetime,
    fetch_scoreboard: Callable[[str, str], Any],
    leagues: tuple[str, ...] = SCOREBOARD_LEAGUES,
) -> dict[str, EventHorizon | None]:
    """One scoreboard fetch per daily-enabled league; a failed league maps to None (run its tasks)."""
    wanted = [
        league
        for league in leagues
        if is_sport_daily_enabled(config, league) and not _configured_leagues(config, league)
    ]
    result: dict[str, EventHorizon | None] = dict.fromkeys(wanted)

    def _one(league: str) -> tuple[str, EventHorizon | None]:
        try:
            return league, scoreboard_horizon(fetch_scoreboard(league, game_date), league, now)
        except Exception:  # any failure means "unknown", never "empty"
            logger.warning("event-horizon probe failed for %s on %s", league, game_date, exc_info=True)
            return league, None

    if wanted:
        with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            result.update(pool.map(_one, wanted))
    return result


def _configured_leagues(config: Mapping[str, Any], league: str) -> Any:
    """A sport that fans out over several ESPN leagues is not covered by the single-scoreboard probe."""
    model_config = (config.get("models") or {}).get(league, {})
    return model_config.get("leagues") if isinstance(model_config, Mapping) else None


def horizon_skip_result(
    horizons: Mapping[str, EventHorizon | None], league: str, sport: str
) -> dict[str, Any] | None:
    """The result a skipped task records, or None when the task must run.

    Shaped like an empty forecast: ``scheduled_games`` is the scoreboard size (0 reads as
    ``no_scheduled_events`` in ledger_coverage) and games that already started are listed as skipped for
    ``event_started`` (all of them reads as ``all_events_started``), so the daily report says why nothing was
    priced. No ``declined_slots``, ``candidates`` or ``priced_contracts``: nothing was evaluated, so
    ``_restore_unreplaced`` reopens whatever the pre-forecast clear retired, exactly as after an empty run.
    """
    horizon = horizons.get(league)
    if horizon is None or not horizon.nothing_to_forecast:
        return None
    return {
        "sport": sport,
        "status": "no_scheduled_events",
        "reason": "no_event_after_decision_time",
        "logged": 0,
        "scheduled_games": horizon.total,
        "market_candidates": 0,
        "candidates": [],
        "priced_contracts": [],
        "unmatched": [],
        "skipped": [
            {"event_id": event_id, "reason": "event_started"} for event_id in horizon.started_event_ids
        ],
    }
