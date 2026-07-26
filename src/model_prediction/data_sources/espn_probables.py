"""ESPN live probable pitchers for daily MLB forecasts.

Pulls starting pitcher ERAs from the ESPN scoreboard probables endpoint.
Free, no API key. Missing probable starters fail closed; a team-level runs
allowed proxy must never masquerade as a starting-pitcher feature.
"""

from __future__ import annotations

import json
from functools import lru_cache

import httpx

from ..config import PROJECT_ROOT

_DISK_CACHE_PATH = PROJECT_ROOT / "data" / "espn_probables_cache.jsonl"


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


@lru_cache(maxsize=512)
def _pull_espn_probables(date_str: str) -> dict[str, dict]:
    """Pull probable pitchers from ESPN scoreboard for a date.

    Returns dict of {event_id: {home_era, away_era, home_name, away_name}}.
    Checks the on-disk cache first (see _load_disk_cache); only a genuine
    cache miss reaches the network. The in-memory lru_cache above this still
    matters within a single process (many rows share one game date).
    """
    disk_cache = _load_disk_cache()
    if date_str in disk_cache:
        return disk_cache[date_str]

    # The public scoreboard accepts YYYYMMDD, while the forecasting pipeline
    # uses ISO dates. Passing YYYY-MM-DD returns HTTP 400 and previously caused
    # every game to fall through to an unrelated team runs-allowed proxy.
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
    except Exception:
        return {}

    result = {}
    for ev in data.get("events", []):
        eid = ev.get("id", "")
        comps = ev.get("competitions", [{}])[0]
        competitors = comps.get("competitors", [])
        if len(competitors) < 2:
            continue

        eras = {}
        for c in competitors:
            home_away = c.get("homeAway", "")
            probables = c.get("probables", [])
            sp = next((p for p in probables if p.get("name") == "probableStartingPitcher"), None)
            if sp:
                athlete = sp.get("athlete", {})
                stats = sp.get("statistics", [])
                era_stat = next((s for s in stats if s.get("name") == "ERA"), None)
                era = float(era_stat.get("displayValue", 0)) if era_stat else None
                name = athlete.get("fullName", athlete.get("displayName", "?"))
                eras[home_away] = {"name": name, "era": era}

        if "home" in eras and "away" in eras:
            result[eid] = {
                "home_era": eras["home"]["era"],
                "away_era": eras["away"]["era"],
                "home_starter": eras["home"]["name"],
                "away_starter": eras["away"]["name"],
            }

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
