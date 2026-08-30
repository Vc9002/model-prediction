"""Dashboard backtests module (extracted from dashboard_server.py, DD-5).

Part of the dashboard_server.py -> dashboard/ package split. See MASTER.md
DD-5 and dashboard/__init__.py for the re-export shim that keeps the old
`dashboard_server` import path and test-suite symbol references working.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore[assignment]


from model_prediction.dashboard.common import (
    DATA,
    EASTERN,
    OUTPUTS,
    SPORTS,
    _number,
    _read_json,
    _today,
)

# ── SECTION: Backtests & Odds ───────────────────────────────────────


def backtests() -> list[dict]:
    items = []
    if OUTPUTS.exists():
        for path in sorted(OUTPUTS.glob("*.json")):
            payload = _read_json(path) or {}
            items.append(
                {
                    "file": path.name,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()[:16],
                    "status": payload.get("status"),
                    "sport": payload.get("sport") or ",".join(payload.get("sports_scope", [])[:4]),
                    "keys": sorted(payload)[:12],
                    "size_kb": round(path.stat().st_size / 1024, 1),
                }
            )
    return items


def odds_summary(sport: str | None = None) -> dict:
    """Per-sport summary of stored Polymarket odds snapshots for today."""
    today = _today()
    sports = [sport] if sport else list(SPORTS)
    result: dict = {}
    for s in sports:
        path = DATA / "odds" / s / today / "polymarket_snapshots.jsonl"
        if not path.exists():
            result[s] = {"snapshots": 0, "status": "no_data"}
            continue
        snaps = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    snaps.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        moneyline = [s for s in snaps if s.get("market_type") == "moneyline"]
        spread = [s for s in snaps if s.get("market_type") == "spread"]
        total = [s for s in snaps if s.get("market_type") == "total"]
        with_bbo = [
            s
            for s in snaps
            if (s.get("long") or {}).get("ask") is not None and (s.get("short") or {}).get("ask") is not None
        ]
        result[s] = {
            "snapshots": len(snaps),
            "moneyline": len(moneyline),
            "spread": len(spread),
            "total": len(total),
            "with_executable_bbo": len(with_bbo),
            "date": today,
        }
    return result if sport is None else result.get(sport, {})


def market_snapshots(sport: str, day: str) -> dict:
    # Local import: orders.py depends on this module's _pick_quote, so a
    # module-level import here would be circular.
    from model_prediction.dashboard.orders import _human_market_name

    path = DATA / "odds" / sport / day / "polymarket_snapshots.jsonl"
    latest: dict[str, dict] = {}
    first_ask: dict[str, float] = {}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue
                slug = snap.get("market_slug")
                if not slug:
                    continue
                # Historical dashboard prices are pregame-only. Once an event
                # starts, later BBOs must never replace the last valid quote.
                # Legacy snapshots captured before these fields existed lack
                # them entirely — fall back to the pre-filter behavior for
                # those rather than dropping their price history outright.
                if snap.get("timestamp_valid") is False:
                    continue
                observed_raw, event_start_raw = snap.get("observed_at_utc"), snap.get("event_start_utc")
                if observed_raw and event_start_raw:
                    try:
                        observed_at = datetime.fromisoformat(str(observed_raw))
                        event_start = datetime.fromisoformat(str(event_start_raw))
                        if observed_at >= event_start:
                            continue
                    except ValueError:
                        pass
                ask = (snap.get("long") or {}).get("ask")
                if slug not in first_ask and ask is not None:
                    first_ask[slug] = ask
                latest[slug] = snap
    rows = []
    for slug, snap in sorted(latest.items()):
        long_side = snap.get("long") or {}
        ask, bid = long_side.get("ask"), long_side.get("bid")
        rows.append(
            {
                "market_slug": slug,
                # Market tables can contain hundreds of contracts. Keep this
                # endpoint local and deterministic instead of doing one public
                # metadata lookup per total/team-total slug.
                "market_name": _human_market_name(str(slug), allow_lookup=False),
                "market_type": snap.get("market_type"),
                "line": snap.get("line"),
                "league": snap.get("league"),
                "event_start_utc": snap.get("event_start_utc"),
                "description": long_side.get("description"),
                "bid": bid,
                "ask": ask,
                "spread": round(ask - bid, 4) if ask is not None and bid is not None else None,
                "ask_size": long_side.get("ask_size"),
                "bid_size": long_side.get("bid_size"),
                "move": round(ask - first_ask[slug], 4) if ask is not None and slug in first_ask else None,
                "observed_at_utc": snap.get("observed_at_utc"),
            }
        )
    return {"sport": sport, "date": day, "markets": rows, "count": len(rows)}


def _team_matches(team_name: str, side_description: str) -> bool:
    team = " ".join(team_name.casefold().split())
    description = " ".join(side_description.casefold().split())
    if not team or not description:
        return False
    if team == description:
        return True
    shorter, longer = (description, team) if len(description) <= len(team) else (team, description)
    return f" {shorter} " in f" {longer} "


def _lines_match(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 1e-6


def _row_selected_team(row: dict) -> str | None:
    selection = str(row.get("selection") or "").casefold()
    if selection == "home":
        return str(row.get("home_team") or "") or None
    if selection == "away":
        return str(row.get("away_team") or "") or None
    return None


def _row_line(row: dict) -> float | None:
    try:
        return float(row.get("line"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _row_matches_snapshot_event(row: dict, snapshot: dict) -> bool:
    """Confirm a spread/total snapshot belongs to this row's actual game.

    Unlike moneyline (long/short descriptions ARE the two team names, so
    matching both against the row's away/home teams already confirms the
    right game), a spread snapshot's "team" is only ONE team (the long
    side), and a total snapshot has no team field at all -- only a line.
    Real bug found and fixed 2026-08-04 while writing this matching (never
    shipped): without this check, _spread_side_for_row's line-negation
    branch matches ANY OTHER game's spread whose line happens to be the
    exact negation of this row's line (e.g. every one of a day's ~15 "-1.5"
    MLB spread markets satisfies "not is_market_team and line == -1.5" for
    a row picking the away side of an unrelated game) -- same risk for
    total's line-only match. `event_title` carries both team names for
    every market type, so requiring both to match closes this."""
    title = str(snapshot.get("event_title") or "")
    away = str(row.get("away_team") or "")
    home = str(row.get("home_team") or "")
    return bool(away) and bool(home) and _team_matches(away, title) and _team_matches(home, title)


def _spread_side_for_row(row: dict, snapshot: dict) -> str | None:
    """Which side ("long"/"short") of a spread snapshot this row's team+line
    picks, or None if it doesn't match at all (including belonging to a
    different game -- see _row_matches_snapshot_event).

    Duplicated from polymarket_execute.py's _resolve_spread_side (same "zero
    imports from that package" rule _team_matches's docstring already
    follows) -- a spread market's own line/team always describe its LONG
    side; SHORT is always the exact negation. A row's line is selection-
    relative, so it only matches the market's long side when the picked
    team IS the market's own team; for the opponent, the row's line must
    equal the negation instead. Found 2026-08-04: this matching never
    existed in dashboard_server.py at all -- _pick_quote returned None for
    every spread row unconditionally, so a real, sized MLB spread pick could
    never be previewed or ordered even when the exact market genuinely
    existed live on Polymarket."""
    if not _row_matches_snapshot_event(row, snapshot):
        return None
    selected_team = _row_selected_team(row)
    row_line = _row_line(row)
    market_team = snapshot.get("team")
    market_line = snapshot.get("line")
    if not selected_team or row_line is None or market_team is None or market_line is None:
        return None
    is_market_team = _team_matches(selected_team, str(market_team))
    if is_market_team and _lines_match(row_line, float(market_line)):
        return "long"
    if not is_market_team and _lines_match(row_line, -float(market_line)):
        return "short"
    return None


def _total_side_for_row(row: dict, snapshot: dict) -> str | None:
    """Which side ("long"/"short") of a total snapshot this row's
    over/under+line picks, or None if it doesn't match. Same "found
    2026-08-04, never existed" note as _spread_side_for_row -- and the same
    cross-game collision risk is even sharper here: a total snapshot has NO
    team field at all, only a line, so without _row_matches_snapshot_event
    ANY other game's total market with the same line (e.g. exactly 8.5)
    would match a row that has nothing to do with that game."""
    if not _row_matches_snapshot_event(row, snapshot):
        return None
    selection = str(row.get("selection") or "").casefold().strip()
    if selection not in ("over", "under"):
        return None
    row_line = _row_line(row)
    market_line = snapshot.get("line")
    if row_line is None or market_line is None or not _lines_match(row_line, float(market_line)):
        return None
    long_desc = str((snapshot.get("long") or {}).get("description") or "").casefold().strip()
    short_desc = str((snapshot.get("short") or {}).get("description") or "").casefold().strip()
    matches_long = long_desc == selection
    matches_short = short_desc == selection
    if matches_long == matches_short:
        return None  # ambiguous or neither side literally says "over"/"under"
    return "long" if matches_long else "short"


import threading

_SNAPSHOT_FILE_CACHE: dict[Path, tuple[float, list[dict]]] = {}
_SNAPSHOT_FILE_CACHE_LOCK = threading.Lock()


def _load_snapshot_file(path: Path) -> list[dict]:
    """Read and parse a polymarket_snapshots.jsonl file with mtime caching.

    Avoids re-opening and parsing large JSON Lines files thousands of times
    during bulk ledger decoration.
    """
    if not path.exists():
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []

    with _SNAPSHOT_FILE_CACHE_LOCK:
        cached = _SNAPSHOT_FILE_CACHE.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    records: list[dict] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    snapshot = json.loads(line)
                    obs = snapshot.get("observed_at_utc")
                    if obs:
                        try:
                            snapshot["_observed_dt"] = datetime.fromisoformat(str(obs))
                        except ValueError:
                            snapshot["_observed_dt"] = None
                    else:
                        snapshot["_observed_dt"] = None
                    records.append(snapshot)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    with _SNAPSHOT_FILE_CACHE_LOCK:
        _SNAPSHOT_FILE_CACHE[path] = (mtime, records)
    return records


def _pick_quote(row: dict) -> dict | None:
    """Last valid pregame executable side quote for a moneyline, spread, or
    total pick.

    Moneyline-only until 2026-08-04 -- MLB spread/total became real, sized
    Main-ledger picks (LEDGER_ROUTING.md) without this function ever being
    extended, so `quote is None` unconditionally for every spread/total row
    made every one of them permanently unexecutable in the dashboard ("no
    exact executable Polymarket US market mapping"), even when the live
    Polymarket market genuinely existed (verified live: a real -1.5 spread
    and a real 8.5 total both existed for a real logged Main pick that was
    showing this exact error). polymarket_execute.py's deeper, real
    execution-time re-verification (_verify_live_side_and_timing, P0-1
    2026-08-03) already supported spread/total/btts -- it was simply
    unreachable from the dashboard order flow because this earlier,
    ticket-building stage always refused first.
    """
    market_type = row.get("market_type")
    if market_type not in ("moneyline", "spread", "total"):
        return None
    sport = str(row.get("league") or "").lower()
    if sport not in SPORTS:
        return None
    try:
        event_start = datetime.fromisoformat(str(row.get("event_start_utc") or ""))
    except ValueError:
        return None
    day = event_start.astimezone(EASTERN).date().isoformat()
    path = DATA / "odds" / sport / day / "polymarket_snapshots.jsonl"
    snapshots = _load_snapshot_file(path)
    if not snapshots:
        return None
    latest: dict[str, dict] = {}
    for snapshot in snapshots:
        if snapshot.get("timestamp_valid") is False:
            continue
        snapshot_observed = snapshot.get("_observed_dt")
        if snapshot_observed is not None and snapshot_observed >= event_start:
            continue
        if snapshot.get("market_type") != market_type:
            continue
        slug = str(snapshot.get("market_slug") or "")
        if not slug or any(
            marker in slug.casefold() for marker in ("-f5-", "-f3-", "-f7-", "-1st-", "-h1-", "-h2-")
        ):
            continue
        if market_type == "moneyline":
            long_description = str((snapshot.get("long") or {}).get("description") or "")
            short_description = str((snapshot.get("short") or {}).get("description") or "")
            away = str(row.get("away_team") or "")
            home = str(row.get("home_team") or "")
            if not (
                (_team_matches(away, long_description) and _team_matches(home, short_description))
                or (_team_matches(home, long_description) and _team_matches(away, short_description))
            ):
                continue
        elif market_type == "spread":
            # Multiple alternate-line markets exist per event -- this
            # must match the row's exact team+line, not just the game,
            # or every alternate line would collide in `latest` and trip
            # the doubleheader guard below.
            if _spread_side_for_row(row, snapshot) is None:
                continue
        else:  # total
            if _total_side_for_row(row, snapshot) is None:
                continue
        latest[slug] = snapshot
    if not latest:
        return None
    if len(latest) > 1:
        return None  # doubleheader: two contracts matched; never guess which game
    snapshot = max(latest.values(), key=lambda item: str(item.get("observed_at_utc") or ""))
    side_name: str | None = None
    if market_type == "moneyline":
        selected_team = (
            str(row.get("home_team") or "")
            if str(row.get("selection") or "").casefold() == "home"
            else str(row.get("away_team") or "")
        )
        matches_long = _team_matches(
            selected_team, str((snapshot.get("long") or {}).get("description") or "")
        )
        matches_short = _team_matches(
            selected_team, str((snapshot.get("short") or {}).get("description") or "")
        )
        if matches_long == matches_short:
            return None  # ambiguous side (same-city names) — never default to long
        side_name = "long" if matches_long else "short"
    elif market_type == "spread":
        side_name = _spread_side_for_row(row, snapshot)
    else:  # total
        side_name = _total_side_for_row(row, snapshot)
    if side_name is None:
        return None
    side = snapshot.get(side_name) or {}
    ask = _number(side.get("ask"), -1)
    if not 0 < ask < 1:
        return None
    observed = str(snapshot.get("observed_at_utc") or "")
    try:
        observed_at = datetime.fromisoformat(observed)
        age_seconds = max(0, int((datetime.now(UTC) - observed_at).total_seconds()))
    except ValueError:
        age_seconds = 10**9
    return {
        "market_slug": snapshot.get("market_slug"),
        "side": side_name,
        "description": side.get("description"),
        "bid": side.get("bid"),
        "ask": ask,
        "ask_size": side.get("ask_size"),
        "observed_at_utc": observed,
        "age_seconds": age_seconds,
        "fresh": age_seconds <= 300,
        "market_state": snapshot.get("market_state"),
        "price_role": "pregame_close",
        "seconds_before_start": max(0, int((event_start - observed_at).total_seconds())),
    }
