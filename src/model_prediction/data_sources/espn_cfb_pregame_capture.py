"""Forward, point-in-time capture of ESPN NCAAF pregame lines and injuries for upcoming FBS games.

Why this exists (quick-261115-183): every NCAAF information-source test in the 2026-11-15 totals work was
capped at an upper bound or came back INSUFFICIENT_DATA because the history carries no observation time
(ESPN's archived open/close values are untimestamped, the injury feed lists 3 stale entries league-wide, no
QB or depth information exists anywhere in the pipeline). A source that cannot prove it was observable before
kickoff cannot enter a model. This module starts the clock: each call appends, per upcoming FBS-involved
game, what ESPN published at that moment with an ``observed_at_utc`` taken when each response RETURNED.

What one record holds (one JSON line per event per call, append-only, hashed):
  - scoreboard: the headline line (provider, spread, total, favourite flags) from the groups=80 scoreboard;
  - core_odds: every provider item of the core odds endpoint with open / current / close point lines, the
    favourite flags, and prices stored ONLY as implied probabilities (never American odds); live items are
    flagged, not dropped;
  - injuries: the league-wide ESPN injury entries for the two teams (the same feed
    ``espn_cfb_injuries.py`` snapshots; it is tiny and stale today, which is itself the finding);
  - qb_depth: always ``endpoint_unavailable``. Probed once on 2026-11-15: the core
    ``seasons/{year}/teams/{id}/depthcharts`` endpoint answers 400 "Depth charts not supported for
    football|college-football" and the site ``teams/{id}/depthchart`` endpoint returns ``{}``. The record keeps
    the field so a later source can fill it without a schema change.

Storage: ``<runtime data root>/ncaaf_pregame_capture/espn_pregame_snapshots.jsonl``. A repo-local ``data/``
argument is rerouted to the runtime root (``rerouted_data_root``) so the file never splits across two
directories; a standalone call (``data_root=None``) resolves through ``RuntimePaths`` and fails closed
without an external runtime root. Readers must filter on ``observed_at_utc`` against the event start.

Fail-soft by contract: ``capture_cfb_pregame_snapshots`` never raises; every failure is a status dict.
Read-only GETs with a generic User-Agent, 0.25 s pacing, and hard request / time / event budgets. Nothing
here changes an existing capture, gate, ledger or model output.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..domain import parse_utc
from ..runtime_paths import RuntimePaths, rerouted_data_root
from .provider_capture import browser_http_get as _default_http_get
from .provider_capture import iso_utc as _iso
from .provider_capture import record_hash as _record_hash

_logger = logging.getLogger(__name__)

CAPTURE_DIRNAME = "ncaaf_pregame_capture"
CAPTURE_FILENAME = "espn_pregame_snapshots.jsonl"
SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
    "?dates={date}&groups=80&limit=500"
)
CORE_ODDS_URL = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/"
    "events/{eid}/competitions/{eid}/odds"
)
INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/injuries"
QB_DEPTH_STATUS = "endpoint_unavailable"
SCHEMA_VERSION = "1"

HttpGet = Callable[[str], tuple[int, bytes]]
Clock = Callable[[], datetime]


# --------------------------------------------------------------------------- parsing helpers
def american_to_probability(value: Any) -> float | None:
    """Implied probability of an American price ('-110', 120, '+154'); None for anything unusable.

    The price itself is never stored: only this probability is written to disk.
    """
    try:
        number = float(str(value).strip().replace("+", ""))
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or abs(number) < 100:
        return None
    return -number / (-number + 100.0) if number < 0 else 100.0 / (number + 100.0)


_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _line(value: Any) -> float | None:
    """A point line from a number or a display string such as '+4', '-2.5', '53.5', 'o48.5'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    match = _NUM.search(str(value))
    return float(match.group(0)) if match else None


def _american_block(block: Any, key: str) -> Any:
    node = block.get(key) if isinstance(block, dict) else None
    return node.get("american") if isinstance(node, dict) else None


def _point_spread(side: dict[str, Any], which: str) -> float | None:
    block = side.get(which)
    spread = block.get("pointSpread") if isinstance(block, dict) else None
    return _line(spread.get("american")) if isinstance(spread, dict) else None


def _home_spread(spread: float | None, home_fav: Any, away_fav: Any) -> float | None:
    """Signed home line from ESPN's favourite flags (the raw ``spread`` is the favourite's number)."""
    if spread is None:
        return None
    if home_fav is True and away_fav is not True:
        return -abs(spread)
    if away_fav is True and home_fav is not True:
        return abs(spread)
    return 0.0 if spread == 0 else None


def parse_scoreboard_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Identity, kickoff and headline line of one scoreboard event, or None when it has no usable identity."""
    try:
        competition = event["competitions"][0]
        home = next(c for c in competition["competitors"] if c["homeAway"] == "home")
        away = next(c for c in competition["competitors"] if c["homeAway"] == "away")
        event_id = str(event["id"])
        start = str(event["date"])
        status = str((event.get("status") or {}).get("type", {}).get("name") or "")
    except (KeyError, IndexError, StopIteration, TypeError):
        return None
    line: dict[str, Any] | None = None
    odds = competition.get("odds") or []
    if odds and isinstance(odds[0], dict):
        item = odds[0]
        provider = item.get("provider") or {}
        h_fav = (item.get("homeTeamOdds") or {}).get("favorite")
        a_fav = (item.get("awayTeamOdds") or {}).get("favorite")
        spread = _line(item.get("spread"))
        line = {
            "provider_id": None if provider.get("id") is None else str(provider.get("id")),
            "provider_name": provider.get("name"),
            "details": item.get("details"),
            "spread": spread,
            "over_under": _line(item.get("overUnder")),
            "home_favorite": h_fav if isinstance(h_fav, bool) else None,
            "away_favorite": a_fav if isinstance(a_fav, bool) else None,
            "home_spread_line": _home_spread(spread, h_fav, a_fav),
        }
    return {
        "event_id": event_id,
        "event_start_utc": start,
        "status": status,
        "neutral_site": bool(competition.get("neutralSite")),
        "home_team": (home.get("team") or {}).get("displayName"),
        "away_team": (away.get("team") or {}).get("displayName"),
        "home_id": str((home.get("team") or {}).get("id")),
        "away_id": str((away.get("team") or {}).get("id")),
        "line": line,
    }


def parse_core_odds(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """One compact dict per provider item: point lines at open / current / close, flags, implied probabilities."""
    out: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        provider = item.get("provider") or {}
        name = str(provider.get("name") or "")
        home = item.get("homeTeamOdds") or {}
        away = item.get("awayTeamOdds") or {}
        spread = _line(item.get("spread"))
        o_blk, c_blk, x_blk = (item.get(k) or {} for k in ("open", "current", "close"))
        out.append(
            {
                "provider_id": None if provider.get("id") is None else str(provider.get("id")),
                "provider_name": name,
                "is_live": "live" in name.lower() or bool(item.get("live")),
                "details": item.get("details"),
                "spread": spread,
                "over_under": _line(item.get("overUnder")),
                "home_favorite": home.get("favorite") if isinstance(home.get("favorite"), bool) else None,
                "away_favorite": away.get("favorite") if isinstance(away.get("favorite"), bool) else None,
                "home_spread_line": _home_spread(spread, home.get("favorite"), away.get("favorite")),
                "home_spread_open": _point_spread(home, "open"),
                "home_spread_current": _point_spread(home, "current"),
                "home_spread_close": _point_spread(home, "close"),
                "total_open": _line(_american_block(o_blk, "total")),
                "total_current": _line(_american_block(c_blk, "total")),
                "total_close": _line(_american_block(x_blk, "total")),
                "over_prob_current": american_to_probability(
                    item.get("overOdds")
                    if item.get("overOdds") is not None
                    else _american_block(c_blk, "over")
                ),
                "under_prob_current": american_to_probability(
                    item.get("underOdds")
                    if item.get("underOdds") is not None
                    else _american_block(c_blk, "under")
                ),
                "over_prob_open": american_to_probability(_american_block(o_blk, "over")),
                "under_prob_open": american_to_probability(_american_block(o_blk, "under")),
                "home_cover_prob_current": american_to_probability(home.get("spreadOdds")),
                "away_cover_prob_current": american_to_probability(away.get("spreadOdds")),
            }
        )
    return out


def parse_injuries(payload: dict[str, Any], team_ids: tuple[str, str]) -> dict[str, list[dict[str, Any]]]:
    """League-wide ESPN injury entries filtered to the two teams of one game, keyed by team id."""
    wanted = {str(t) for t in team_ids}
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in wanted}
    for team in payload.get("injuries") or []:
        tid = str((team or {}).get("id"))
        if tid not in wanted:
            continue
        for entry in team.get("injuries") or []:
            athlete = entry.get("athlete") or {}
            position = athlete.get("position") or {}
            out[tid].append(
                {
                    "athlete_id": None if athlete.get("id") is None else str(athlete.get("id")),
                    "name": athlete.get("displayName"),
                    "position": position.get("abbreviation") if isinstance(position, dict) else None,
                    "status": entry.get("status"),
                    "reported_date": entry.get("date"),
                }
            )
    return out


# --------------------------------------------------------------------------- transport
def capture_path(data_root: str | Path | None) -> Path:
    """The one capture file. ``None`` resolves the runtime root (fail-closed); a repo-local ``data/`` is rerouted."""
    if data_root is None:
        root = RuntimePaths.resolve(require_external_runtime=True).data_root
    else:
        root = rerouted_data_root(data_root)
    return root / CAPTURE_DIRNAME / CAPTURE_FILENAME


def capture_cfb_pregame_snapshots(
    data_root: str | Path | None,
    game_date: str | None = None,
    *,
    days: int = 8,
    now: Clock | None = None,
    http_get: HttpGet | None = None,
    sleep: Callable[[float], None] = time.sleep,
    pace_s: float = 0.25,
    max_events: int = 120,
    max_requests: int = 200,
    max_seconds: float = 240.0,
) -> dict[str, Any]:
    """Append one record per upcoming FBS-involved game starting within ``days`` days of ``game_date``.

    ``now`` is a clock callable (default: real UTC time) read again after EVERY response so each block's
    ``observed_at_utc`` is the return time, never an earlier timestamp. ``http_get(url) -> (status, bytes)`` is
    injectable for tests. Never raises: any failure is returned as ``{"status": "error", ...}``.
    """
    try:
        return _capture(
            data_root,
            game_date,
            days=days,
            clock=now or (lambda: datetime.now(UTC)),
            http_get=http_get or _default_http_get,
            sleep=sleep,
            pace_s=pace_s,
            max_events=max_events,
            max_requests=max_requests,
            max_seconds=max_seconds,
        )
    except Exception as exc:
        _logger.warning("NCAAF pregame capture failed", exc_info=True)
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "records_written": 0,
            "qb_depth": QB_DEPTH_STATUS,
        }


def _capture(
    data_root: str | Path | None,
    game_date: str | None,
    *,
    days: int,
    clock: Clock,
    http_get: HttpGet,
    sleep: Callable[[float], None],
    pace_s: float,
    max_events: int,
    max_requests: int,
    max_seconds: float,
) -> dict[str, Any]:
    path = capture_path(data_root)
    started = clock()
    first_date = date.fromisoformat(game_date) if game_date else started.astimezone(UTC).date()
    errors: list[dict[str, str]] = []
    requests = 0
    truncated: str | None = None

    def fetch(url: str, kind: str) -> tuple[dict[str, Any], datetime, str] | None:
        nonlocal requests, truncated
        if requests >= max_requests:
            truncated = truncated or "max_requests"
            return None
        if (clock() - started).total_seconds() > max_seconds:
            truncated = truncated or "max_seconds"
            return None
        if requests:
            sleep(pace_s)
        requests += 1
        try:
            status, raw = http_get(url)
            observed = clock()  # taken after the response returned
            if status != 200:
                errors.append({"kind": kind, "error": f"HTTP {status}"})
                return None
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError("payload is not a JSON object")
        except Exception as exc:  # noqa: BLE001 -- one bad request must not stop the rest
            errors.append({"kind": kind, "error": f"{type(exc).__name__}: {exc}"[:160]})
            return None
        return payload, observed, hashlib.sha256(raw).hexdigest()

    events: dict[str, dict[str, Any]] = {}
    sb_meta: dict[str, tuple[datetime, str]] = {}
    for offset in range(max(1, days)):
        day = (first_date + timedelta(days=offset)).strftime("%Y%m%d")
        got = fetch(SCOREBOARD_URL.format(date=day), "scoreboard")
        if got is None:
            continue
        payload, observed, sha = got
        for raw_event in payload.get("events") or []:
            parsed = parse_scoreboard_event(raw_event) if isinstance(raw_event, dict) else None
            if parsed is not None and parsed["event_id"] not in events:
                events[parsed["event_id"]] = parsed
                sb_meta[parsed["event_id"]] = (observed, sha)

    skipped_started = not_scheduled = dropped_after_kickoff = 0
    upcoming: list[dict[str, Any]] = []
    for ev in events.values():
        try:
            kickoff = parse_utc(ev["event_start_utc"])
        except (TypeError, ValueError):
            errors.append(
                {"kind": "event", "error": f"bad start {ev['event_start_utc']!r} for {ev['event_id']}"}
            )
            continue
        ev["_kickoff"] = kickoff
        if ev["status"] != "STATUS_SCHEDULED":
            not_scheduled += 1
        elif kickoff <= clock():
            skipped_started += 1
        else:
            upcoming.append(ev)
    upcoming.sort(key=lambda e: (e["_kickoff"], e["event_id"]))
    events_over_cap = max(0, len(upcoming) - max_events)
    upcoming = upcoming[:max_events]

    injuries = fetch(INJURIES_URL, "injuries") if upcoming else None
    records_written = 0
    for ev in upcoming:
        kickoff = ev["_kickoff"]
        core = fetch(CORE_ODDS_URL.format(eid=ev["event_id"]), "core_odds")
        sb_observed, sb_sha = sb_meta[ev["event_id"]]
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "source": "espn_cfb_pregame",
            "sport": "ncaaf",
            "event_id": ev["event_id"],
            "event_start_utc": ev["event_start_utc"],
            "home_team": ev["home_team"],
            "away_team": ev["away_team"],
            "home_id": ev["home_id"],
            "away_id": ev["away_id"],
            "neutral_site": ev["neutral_site"],
            "scoreboard": {
                "url_kind": "site_scoreboard_groups_80",
                "observed_at_utc": _iso(sb_observed),
                "content_sha256": sb_sha,
                "line": ev["line"],
            },
            "core_odds": None,
            "injuries": None,
            "qb_depth": QB_DEPTH_STATUS,
        }
        observed_times = [sb_observed]
        if core is not None:
            payload, observed, sha = core
            observed_times.append(observed)
            record["core_odds"] = {
                "url_kind": "core_odds",
                "observed_at_utc": _iso(observed),
                "content_sha256": sha,
                "providers": parse_core_odds(payload),
            }
        if injuries is not None:
            payload, observed, sha = injuries
            observed_times.append(observed)
            record["injuries"] = {
                "url_kind": "site_league_injuries",
                "observed_at_utc": _iso(observed),
                "content_sha256": sha,
                "by_team": parse_injuries(payload, (ev["home_id"], ev["away_id"])),
                "league_entries_total": sum(
                    len((t or {}).get("injuries") or []) for t in payload.get("injuries") or []
                ),
            }
        latest = max(observed_times)
        if latest >= kickoff or clock() >= kickoff:
            dropped_after_kickoff += (
                1  # nothing observed at or after kickoff may be mistaken for a pregame view
            )
            continue
        record["observed_at_utc"] = _iso(latest)
        record["record_hash"] = _record_hash(record)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        except OSError as exc:
            errors.append({"kind": "write", "error": f"{type(exc).__name__}: {exc}"[:160]})
            continue
        records_written += 1

    ended = clock()
    partial = bool(errors) or truncated is not None or events_over_cap > 0
    return {
        "status": "partial" if partial else "ok",
        "observed_at_utc": _iso(ended),
        "path": str(path),
        "records_written": records_written,
        "events_seen": len(events),
        "events_upcoming": len(upcoming) + events_over_cap,
        "events_over_cap": events_over_cap,
        "skipped_started": skipped_started,
        "skipped_not_scheduled": not_scheduled,
        "dropped_after_kickoff": dropped_after_kickoff,
        "requests": requests,
        "truncated": truncated,
        "qb_depth": QB_DEPTH_STATUS,
        "errors": errors[:20],
    }
