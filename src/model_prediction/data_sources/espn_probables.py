"""ESPN live probable pitchers for daily MLB forecasts.

Pulls starting pitcher ERAs from the ESPN scoreboard probables endpoint.
Free, no API key. Missing probable starters fail closed; a team-level runs
allowed proxy must never masquerade as a starting-pitcher feature.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache

import httpx

from ..config import PROJECT_ROOT
from ..domain import iso_utc, parse_utc, utc_now

_DISK_CACHE_PATH = PROJECT_ROOT / "data" / "espn_probables_cache.jsonl"
_PIT_ARCHIVE_PATH = (
    PROJECT_ROOT / "data" / "point_in_time" / "mlb_probable_starters.jsonl"
)


def _load_disk_cache() -> dict[str, dict]:
    """One JSON line per date, keyed on ESPN's calendar date — the same
    scoreboard response for a past date never changes, so once fetched it's
    valid forever. Without this, re-running a walk-forward backtest (one
    ESPN call per unique historical game date) costs 2+ hours of live HTTP
    calls every single time; with it, only genuinely new dates hit ESPN.
    """
    if not _DISK_CACHE_PATH.exists():
        return {}
    cache: dict[str, dict] = {}
    with _DISK_CACHE_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            cache[entry["date"]] = entry["probables"]
    return cache


def _append_disk_cache(date_str: str, probables: dict[str, dict]) -> None:
    _DISK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DISK_CACHE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"date": date_str, "probables": probables}) + "\n")


def _append_pit_archive(rows: list[dict]) -> None:
    if not rows:
        return
    _PIT_ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _PIT_ARCHIVE_PATH.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _fetch_espn_probables(
    date_str: str,
    *,
    observed_at: datetime,
) -> dict[str, dict]:
    """Fetch one scoreboard and append explicit observation-time lineage."""
    normalized_date = date_str.replace("-", "")
    if len(normalized_date) != 8 or not normalized_date.isdigit():
        return {}
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
        f"?dates={normalized_date}"
    )
    try:
        resp = httpx.get(url, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
    except (httpx.HTTPError, TypeError, ValueError):
        return {}

    observed_at_utc = iso_utc(observed_at)
    result: dict[str, dict] = {}
    archive_rows: list[dict] = []
    for ev in data.get("events", []):
        eid = str(ev.get("id", ""))
        comps = ev.get("competitions", [{}])[0]
        competitors = comps.get("competitors", [])
        if not eid or len(competitors) < 2:
            continue

        eras = {}
        for competitor in competitors:
            home_away = competitor.get("homeAway", "")
            probables = competitor.get("probables", [])
            starter = next(
                (
                    probable
                    for probable in probables
                    if probable.get("name") == "probableStartingPitcher"
                ),
                None,
            )
            if starter:
                athlete = starter.get("athlete", {})
                stats = starter.get("statistics", [])
                era_stat = next(
                    (stat for stat in stats if stat.get("name") == "ERA"),
                    None,
                )
                try:
                    era = (
                        float(era_stat.get("displayValue"))
                        if era_stat is not None
                        else None
                    )
                except (TypeError, ValueError):
                    era = None
                name = athlete.get("fullName", athlete.get("displayName", "?"))
                eras[home_away] = {"name": name, "era": era}

        if "home" not in eras or "away" not in eras:
            continue
        entry = {
            "home_era": eras["home"]["era"],
            "away_era": eras["away"]["era"],
            "home_starter": eras["home"]["name"],
            "away_starter": eras["away"]["name"],
        }
        result[eid] = entry
        event_start_utc = str(ev.get("date") or "")
        try:
            pit_eligible = parse_utc(observed_at_utc) < parse_utc(event_start_utc)
        except ValueError:
            pit_eligible = False
        archive_rows.append(
            {
                "schema_version": "1",
                "source": "espn_scoreboard_probables",
                "source_version": "site-v2",
                "game_date": date_str,
                "event_id": eid,
                "event_start_utc": event_start_utc,
                "observed_at_utc": observed_at_utc,
                **entry,
                "pit_eligible": pit_eligible,
                "provenance": (
                    "prospective_pregame"
                    if pit_eligible
                    else "retroactive_or_unverifiable_non_pit"
                ),
            }
        )
    _append_pit_archive(archive_rows)
    return result


def capture_probable_starter_snapshot(
    date_str: str,
    *,
    observed_at: datetime | None = None,
) -> dict[str, dict]:
    """Capture an append-only probable-starter observation for daily use."""
    if observed_at is None:
        # Reuse the live forecast cache so daily capture and MLB feature
        # construction share one ESPN request and one archive observation.
        return _pull_espn_probables(date_str)
    return _fetch_espn_probables(date_str, observed_at=observed_at)


@lru_cache(maxsize=512)
def _pull_espn_probables(date_str: str) -> dict[str, dict]:
    """Pull probable pitchers from ESPN scoreboard for a date.

    Returns dict of {event_id: {home_era, away_era, home_name, away_name}}.
    The old cache remains usable only as legacy/non-PIT research data.
    Every live network response is separately archived with observation and
    event timestamps so later validation can distinguish real pregame evidence.
    """
    disk_cache = _load_disk_cache()
    if date_str in disk_cache:
        return disk_cache[date_str]

    normalized_date = date_str.replace("-", "")
    if len(normalized_date) != 8 or not normalized_date.isdigit():
        return {}
    result = _fetch_espn_probables(date_str, observed_at=utc_now())

    # Only persist STRICTLY PAST dates. This function also serves live
    # forecasting (default date_str = today), and ESPN announces probables
    # progressively through game day — caching today's (possibly still
    # incomplete) result forever would freeze it wrong for the rest of the
    # day. A past date's scoreboard is genuinely final and safe to persist,
    # even when ESPN returned zero probables for it (e.g. an off day).
    # Compare normalized_date (always compact YYYYMMDD, computed above)
    # against today in the same compact format -- comparing the raw,
    # possibly-ISO date_str against a compact string is a lexicographic trap:
    # "-" sorts below every digit, so any ISO "YYYY-MM-DD" input (a supported,
    # tested calling convention for this function) would always compare as
    # "less than" a compact today-string regardless of the actual date,
    # silently caching today's incomplete result forever.
    from ..domain import eastern_today
    if normalized_date < eastern_today().strftime("%Y%m%d"):
        _append_disk_cache(date_str, result)
    return result


def point_in_time_pitcher_era_gap(
    event_id: str,
    decision_at: datetime,
) -> float:
    """Latest archived two-starter observation available before first pitch."""
    if not _PIT_ARCHIVE_PATH.exists():
        raise ValueError(f"NO_CALL_STARTERS_NO_PIT_ARCHIVE: {event_id}")
    decision = parse_utc(decision_at.isoformat())
    eligible: list[dict] = []
    with _PIT_ARCHIVE_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                observed = parse_utc(str(row["observed_at_utc"]))
                event_start = parse_utc(str(row["event_start_utc"]))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if (
                str(row.get("event_id")) == str(event_id)
                and row.get("pit_eligible") is True
                and observed <= decision
                and observed < event_start
                and row.get("home_era") is not None
                and row.get("away_era") is not None
            ):
                eligible.append(row)
    if not eligible:
        raise ValueError(f"NO_CALL_STARTERS_NO_PIT_ARCHIVE: {event_id}")
    latest = max(eligible, key=lambda row: str(row["observed_at_utc"]))
    return round(float(latest["home_era"]) - float(latest["away_era"]), 4)


def espn_pitcher_era_gap(event_id: str, home_team: str, away_team: str,
                         date_str: str = "") -> float:
    """Live probable pitcher ERA gap from ESPN.

    Returns home_starter_ERA - away_starter_ERA.
    Negative = home starter is better (lower ERA).
    Raises ValueError when both probable starters and ERAs are unavailable.
    This is intentionally fail-closed: team runs allowed is not starter ERA.
    """
    if not date_str:
        from ..domain import eastern_today
        date_str = eastern_today().strftime("%Y%m%d")

    probables = _pull_espn_probables(date_str)
    entry = probables.get(event_id)
    if entry and entry.get("home_era") is not None and entry.get("away_era") is not None:
        return round(entry["home_era"] - entry["away_era"], 4)

    raise ValueError(
        "NO_CALL_STARTERS_UNAVAILABLE: "
        f"no two-sided probable-starter ERA for event {event_id} on {date_str} "
        f"({away_team} at {home_team})"
    )
