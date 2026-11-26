"""Dashboard backtests module (extracted from dashboard_server.py, DD-5).

Part of the dashboard_server.py -> dashboard/ package split. See MASTER.md
DD-5 and dashboard/__init__.py for the re-export shim that keeps the old
`dashboard_server` import path and test-suite symbol references working.
"""

from __future__ import annotations

import json
import re
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
from model_prediction.polymarket_matching import lines_match as _lines_match
from model_prediction.polymarket_matching import match_spread_side
from model_prediction.polymarket_matching import participant_matches as _participant_matches
from model_prediction.polymarket_matching import team_matches as _team_matches  # noqa: F401 -- re-export

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


def _resolve_snapshot_path(sport_name: str, day_str: str) -> Path:
    """Resolve the location of polymarket_snapshots.jsonl, prioritizing live runtime storage."""
    try:
        from model_prediction.runtime_paths import RuntimePaths

        runtime_root = RuntimePaths.resolve().runtime_root
        candidate_sports = [sport_name]
        if sport_name in ("nbl", "euroleague"):
            candidate_sports.append("basketball")
        elif sport_name in ("cfb", "college_football"):
            candidate_sports.append("ncaaf")
        elif sport_name in ("r6", "rainbow_six"):
            candidate_sports.append("esports")

        for s in candidate_sports:
            runtime_path = runtime_root / "odds" / s / day_str / "polymarket_snapshots.jsonl"
            if runtime_path.exists():
                return runtime_path
            data_path = DATA / "odds" / s / day_str / "polymarket_snapshots.jsonl"
            if data_path.exists():
                return data_path
    except (OSError, RuntimeError):
        pass
    return DATA / "odds" / sport_name / day_str / "polymarket_snapshots.jsonl"


def odds_summary(sport: str | None = None) -> dict:
    """Per-sport summary of stored Polymarket odds snapshots for today."""
    today = _today()
    sports = [sport] if sport else list(SPORTS)
    result: dict = {}
    for s in sports:
        path = _resolve_snapshot_path(s, today)
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

    path = _resolve_snapshot_path(sport, day)
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


_RE_VS_SPLIT = re.compile(r"\s+(?:vs\.?|@)\s+", re.IGNORECASE)


def _event_participant_indexes(row: dict, participant: str, title: str) -> set[int]:
    sides = _RE_VS_SPLIT.split(title)
    if len(sides) < 2:
        sides = [title]
    return {index for index, side in enumerate(sides) if _participant_matches(row, participant, side)}


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
    if not away or not home:
        return False
    away_indexes = _event_participant_indexes(row, away, title)
    home_indexes = _event_participant_indexes(row, home, title)
    return any(away_index != home_index for away_index in away_indexes for home_index in home_indexes)


def _spread_side_for_row(row: dict, snapshot: dict) -> str | None:
    """Which side ("long"/"short") of a spread snapshot this row's team+line
    picks, or None if it doesn't match at all (including belonging to a
    different game -- see _row_matches_snapshot_event).

    The long/short decision is polymarket_matching.match_spread_side, the
    same code the execution gate's _resolve_spread_side runs. Found
    2026-08-04: this matching never
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
    return match_spread_side(row, row_line, market_team, market_line).side


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
from collections import OrderedDict

# Parsed polymarket_snapshots.jsonl files, newest-used last. The cache was
# unbounded until 2026-11-13: 774 files / 543 MB on disk parse to ~4.4x that
# in Python objects, and decorating picks across the season's dates pinned
# ~2.5 GB in the dashboard process (Windows low-virtual-memory events named
# it the top consumer at 3.4-3.6 GB). Bounded by on-disk bytes so one 43 MB
# NCAAF Saturday cannot evict everything else at once but the whole tree can
# never be resident; evicted files simply re-parse on the next touch.
_SNAPSHOT_FILE_CACHE: OrderedDict[Path, tuple[float, int, list[dict]]] = OrderedDict()
_SNAPSHOT_FILE_CACHE_LOCK = threading.Lock()
_SNAPSHOT_CACHE_MAX_BYTES = 96 * 1024 * 1024  # on-disk bytes; ~400 MB parsed
_SNAPSHOT_CACHE_BYTES = 0


def _snapshot_cache_put(path: Path, mtime: float, size: int, records: list[dict]) -> None:
    """Insert under the byte cap, evicting least-recently-used files first."""
    global _SNAPSHOT_CACHE_BYTES
    with _SNAPSHOT_FILE_CACHE_LOCK:
        old = _SNAPSHOT_FILE_CACHE.pop(path, None)
        if old is not None:
            _SNAPSHOT_CACHE_BYTES -= old[1]
        _SNAPSHOT_FILE_CACHE[path] = (mtime, size, records)
        _SNAPSHOT_CACHE_BYTES += size
        while _SNAPSHOT_CACHE_BYTES > _SNAPSHOT_CACHE_MAX_BYTES and len(_SNAPSHOT_FILE_CACHE) > 1:
            _, (_, evicted_size, _) = _SNAPSHOT_FILE_CACHE.popitem(last=False)
            _SNAPSHOT_CACHE_BYTES -= evicted_size


# Negative-existence cache: paths confirmed absent on first check are stored here
# so bulk decoration (4000+ picks) avoids repeated expensive Windows stat() calls.
_MISSING_PATHS: set[Path] = set()


def _load_snapshot_file(path: Path) -> list[dict]:
    """Read and parse a polymarket_snapshots.jsonl file with mtime caching.

    Avoids re-opening and parsing large JSON Lines files thousands of times
    during bulk ledger decoration.
    """
    if path in _MISSING_PATHS:
        return []
    if not path.exists():
        _MISSING_PATHS.add(path)
        return []
    try:
        stat = path.stat()
        mtime, size = stat.st_mtime, stat.st_size
    except OSError:
        return []

    with _SNAPSHOT_FILE_CACHE_LOCK:
        cached = _SNAPSHOT_FILE_CACHE.get(path)
        if cached is not None and cached[0] == mtime:
            _SNAPSHOT_FILE_CACHE.move_to_end(path)
            return cached[2]

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

    _snapshot_cache_put(path, mtime, size, records)
    return records


_RE_RECORDED_SLUG = re.compile(r"\bmarket_slug=([a-z0-9_\-]+)")
_RE_RECORDED_SIDE = re.compile(r"\btoken_side=(long|short)\b")


def _recorded_market(row: dict) -> tuple[str, str] | None:
    """The Polymarket market slug and side the forecast priced this row against, or None.

    Both must be recorded: other sports already write ``market_slug=`` alone, and those rows keep
    the name-matching path unchanged."""
    rationale = str(row.get("rationale") or "")
    slug = _RE_RECORDED_SLUG.search(rationale)
    side = _RE_RECORDED_SIDE.search(rationale)
    if slug is None or side is None:
        return None
    return slug.group(1), side.group(1)


def _pinned_snapshot(
    snapshots: list[dict], slug: str, event_start: datetime, market_type: str
) -> dict | None:
    """Newest valid pregame snapshot of the recorded market (a max-scan: the file is ingest-ordered)."""
    newest: dict | None = None
    for snapshot in snapshots:
        if snapshot.get("market_slug") != slug or snapshot.get("timestamp_valid") is False:
            continue
        observed = snapshot.get("_observed_dt")
        if observed is not None and observed >= event_start:
            continue
        if snapshot.get("market_type") != market_type:
            continue
        if newest is None or str(snapshot.get("observed_at_utc") or "") >= str(
            newest.get("observed_at_utc") or ""
        ):
            newest = snapshot
    return newest


def _moneyline_side_by_name(row: dict, snapshot: dict) -> str | None:
    selected_team = _row_selected_team(row) or ""
    matches_long = _participant_matches(
        row, selected_team, str((snapshot.get("long") or {}).get("description") or "")
    )
    matches_short = _participant_matches(
        row, selected_team, str((snapshot.get("short") or {}).get("description") or "")
    )
    if matches_long == matches_short:
        return None
    return "long" if matches_long else "short"


def _pinned_side(row: dict, snapshot: dict, side: str, market_type: str) -> str | None:
    """``side`` when the recorded market still agrees with the row, "refuse" when it contradicts
    it, or None when its line no longer matches (the caller then falls back to name matching).

    Name matching is only a cross-check here: a decisive name answer that disagrees with the
    recorded side refuses the row; an indecisive one (an abbreviation no alias knows) does not."""
    row_event = str(row.get("event_id") or "")
    snapshot_event = str(snapshot.get("event_id") or "")
    if row_event and snapshot_event and row_event != snapshot_event:
        return "refuse"
    if market_type == "spread":
        row_line = _row_line(row)
        try:
            market_line = float(snapshot.get("line"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if row_line is None or not _lines_match(row_line, market_line if side == "long" else -market_line):
            return None
        role = match_spread_side(row, row_line, snapshot.get("team"), market_line).market_team
        selection = str(row.get("selection") or "").casefold()
        if role in ("away", "home") and (role == selection) != (side == "long"):
            return "refuse"
        return side
    if market_type == "total":
        row_line = _row_line(row)
        try:
            market_line = float(snapshot.get("line"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if not _lines_match(row_line, market_line):
            return None
        description = str((snapshot.get(side) or {}).get("description") or "").casefold().strip()
        if description != str(row.get("selection") or "").casefold().strip():
            return "refuse"
        return side
    name_side = _moneyline_side_by_name(row, snapshot)
    return "refuse" if name_side is not None and name_side != side else side


def _quote_from_snapshot(snapshot: dict, side_name: str, event_start: datetime) -> dict | None:
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
        # The exchange's own start, which the live buyer cross-checks against the row's tip time.
        "event_start_utc": snapshot.get("event_start_utc"),
        "price_role": "pregame_close",
        "seconds_before_start": max(0, int((event_start - observed_at).total_seconds())),
    }


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

    A row that recorded the market and side it was priced against (the NCAAB forecast writes
    ``market_slug=... token_side=...``, 2026-11-25) is quoted on exactly that market and side; name
    matching below is the fallback when that market was not captured or its line no longer agrees.
    """
    market_type = row.get("market_type")
    if market_type not in ("moneyline", "spread", "total", "nrfi"):
        return None
    sport = str(row.get("league") or row.get("sport") or "").lower()
    if sport not in SPORTS and sport != "esports":
        return None
    try:
        event_start = datetime.fromisoformat(str(row.get("event_start_utc") or ""))
    except ValueError:
        return None
    day = event_start.astimezone(EASTERN).date().isoformat()
    odds_sport = (
        "esports"
        if sport in ("cs2", "lol", "dota2", "valorant", "r6", "rainbow_six", "cod", "ow", "rl")
        else ("ncaaf" if sport in ("cfb", "college_football") else sport)
    )
    path = _resolve_snapshot_path(odds_sport, day)
    if path in _MISSING_PATHS or not path.exists():
        if path not in _MISSING_PATHS:
            _MISSING_PATHS.add(path)
        path = _resolve_snapshot_path(sport, day)
    snapshots = _load_snapshot_file(path)
    if not snapshots:
        return None
    recorded = _recorded_market(row) if market_type in ("moneyline", "spread", "total") else None
    if recorded is not None:
        pinned = _pinned_snapshot(snapshots, recorded[0], event_start, market_type)
        pinned_side = _pinned_side(row, pinned, recorded[1], market_type) if pinned is not None else None
        if pinned_side == "refuse":
            return None
        if pinned is not None and pinned_side is not None:
            return _quote_from_snapshot(pinned, pinned_side, event_start)
    latest: dict[str, dict] = {}
    for snapshot in snapshots:
        if snapshot.get("timestamp_valid") is False:
            continue
        snapshot_observed = snapshot.get("_observed_dt")
        if snapshot_observed is not None and snapshot_observed >= event_start:
            continue
        snapshot_market_type = snapshot.get("market_type")
        if market_type == "moneyline":
            if snapshot_market_type not in {"moneyline", "team_win"}:
                continue
        elif snapshot_market_type != market_type:
            continue
        slug = str(snapshot.get("market_slug") or "")
        if not slug or any(
            marker in slug.casefold()
            for marker in ("-f5-", "-f3-", "-f7-", "-1st-", "-h1-", "-h2-", "-fh-", "-sh-")
        ):
            continue
        if market_type == "moneyline":
            away = str(row.get("away_team") or "")
            home = str(row.get("home_team") or "")
            if snapshot_market_type == "team_win":
                selected_team = _row_selected_team(row)
                market_team = str(snapshot.get("team") or "")
                if (
                    sport != "soccer"
                    or not selected_team
                    or not market_team
                    or not _row_matches_snapshot_event(row, snapshot)
                    or not _participant_matches(row, selected_team, market_team)
                ):
                    continue
            else:
                long_description = str((snapshot.get("long") or {}).get("description") or "")
                short_description = str((snapshot.get("short") or {}).get("description") or "")
                if not (
                    (
                        _participant_matches(row, away, long_description)
                        and _participant_matches(row, home, short_description)
                    )
                    or (
                        _participant_matches(row, home, long_description)
                        and _participant_matches(row, away, short_description)
                    )
                ):
                    continue
        elif market_type == "spread":
            # Multiple alternate-line markets exist per event -- this
            # must match the row's exact team+line, not just the game,
            # or every alternate line would collide in `latest` and trip
            # the doubleheader guard below.
            if _spread_side_for_row(row, snapshot) is None:
                continue
        elif market_type == "total":
            if _total_side_for_row(row, snapshot) is None:
                continue
        else:  # NRFI/YRFI are opposite sides of one Yes/No run contract.
            if not _row_matches_snapshot_event(row, snapshot):
                continue
            selection = str(row.get("selection") or "").casefold().strip()
            expected_description = {"nrfi": "no", "yrfi": "yes"}.get(selection)
            if expected_description is None:
                continue
            long_desc = str((snapshot.get("long") or {}).get("description") or "").casefold().strip()
            short_desc = str((snapshot.get("short") or {}).get("description") or "").casefold().strip()
            if (long_desc == expected_description) == (short_desc == expected_description):
                continue
        latest[slug] = snapshot
    if not latest:
        return None
    if len(latest) > 1:
        return None  # doubleheader: two contracts matched; never guess which game
    snapshot = max(latest.values(), key=lambda item: str(item.get("observed_at_utc") or ""))
    side_name: str | None = None
    if market_type == "moneyline":
        if snapshot.get("market_type") == "team_win":
            side_name = "long"
        else:
            selected_team = (
                str(row.get("home_team") or "")
                if str(row.get("selection") or "").casefold() == "home"
                else str(row.get("away_team") or "")
            )
            matches_long = _participant_matches(
                row,
                selected_team,
                str((snapshot.get("long") or {}).get("description") or ""),
            )
            matches_short = _participant_matches(
                row,
                selected_team,
                str((snapshot.get("short") or {}).get("description") or ""),
            )
            if matches_long == matches_short:
                return None  # ambiguous side (same-city names) — never default to long
            side_name = "long" if matches_long else "short"
    elif market_type == "spread":
        side_name = _spread_side_for_row(row, snapshot)
    elif market_type == "total":
        side_name = _total_side_for_row(row, snapshot)
    else:  # nrfi
        selection = str(row.get("selection") or "").casefold().strip()
        expected_description = {"nrfi": "no", "yrfi": "yes"}.get(selection)
        if expected_description is None:
            return None
        long_desc = str((snapshot.get("long") or {}).get("description") or "").casefold().strip()
        short_desc = str((snapshot.get("short") or {}).get("description") or "").casefold().strip()
        if (long_desc == expected_description) == (short_desc == expected_description):
            return None
        side_name = "long" if long_desc == expected_description else "short"
    if side_name is None:
        return None
    return _quote_from_snapshot(snapshot, side_name, event_start)
