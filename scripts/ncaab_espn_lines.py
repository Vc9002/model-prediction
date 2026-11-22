"""Historical NCAAB betting lines from ESPN's public odds endpoint (quick-261115-1m8, Tier 3).

Read-only research tooling. It builds (1) a per-date archive of ESPN men's college basketball
Division I scoreboards (event ids, teams, final scores, neutral-site flag, a few box-score counts)
and (2) a closing/opening line archive for every completed event 2022-23 .. 2026-27 to date, so a
market-anchored residual test (scripts/ncaab_residual.py) has lines to anchor on. The 2021-22
scoreboard is fetched too, for rating-history burn-in only (no odds are fetched for it).

WHAT THE DATA IS (read this before using it)
    ESPN's `/competitions/{id}/odds` returns one item per provider with the line as last
    archived. There is no per-quote observed_at. These are archived closing/final pregame
    lines (plus an opening line where the provider carries one): a closing-line BENCHMARK, not
    decision-time quotes. Live / in-play items (names containing "Live") carry the in-game line
    and are excluded from every selected value. No moneyline and no price of any kind is stored
    or printed: only point-spread and total lines.

PINNED LINE-SELECTION RULE (frozen before any outcome is joined; see PINNED_RULE below)
    The rule text below is hashed; the hash is stamped on every stored odds record and the first
    fetch time is kept in the progress file. fetch, verify and analyze refuse to run if any
    stored record was built under a different rule text. This script NEVER joins outcomes
    (scores, winners, covers): the scoreboard file carries final scores, but this script only
    uses them to decide which events are completed, never against a line.

Commands (single worker, read-only GET, generic User-Agent, no project or user details):
    scoreboard  resumable per-date scoreboard archive (2021-11-01 .. 2026-11-14, season windows)
    fetch       resumable, append-only fetch of every completed event's odds into espn_lines.jsonl
    verify      re-fetch a random sample of 25 stored events and compare to stored values
    analyze     outcome-free coverage audit; writes the audit json + md under outputs/ncaab/

Run with OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_espn_lines.py <command>.
All three fetching commands are resumable: re-run the same command after any interruption.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

_logger = logging.getLogger("ncaab_espn_lines")

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = REPO / "data" / "research" / "ncaab_market"  # data/research/ is gitignored operational output
SCOREBOARD_PATH = DATA_DIR / "scoreboard.jsonl"
LINES_PATH = DATA_DIR / "espn_lines.jsonl"
PROGRESS_PATH = DATA_DIR / "espn_lines_progress.json"  # fetch + verify sections, merged
AUDIT_JSON = REPO / "outputs" / "ncaab" / "ncaab_espn_lines_audit.json"
AUDIT_MD = REPO / "outputs" / "ncaab" / "ncaab_espn_lines_audit.md"

AS_OF_UTC = "2026-11-15T03:00:00Z"
LAST_SCOREBOARD_DATE = date(2026, 11, 14)
# (season_year in ESPN's convention = the spring calendar year, window of dates to scan)
SEASON_WINDOWS: dict[int, tuple[date, date]] = {
    2022: (date(2021, 11, 1), date(2022, 4, 10)),
    2023: (date(2022, 11, 1), date(2023, 4, 10)),
    2024: (date(2023, 11, 1), date(2024, 4, 10)),
    2025: (date(2024, 11, 1), date(2025, 4, 10)),
    2026: (date(2025, 11, 1), date(2026, 4, 10)),
    2027: (date(2026, 11, 1), LAST_SCOREBOARD_DATE),
}
ODDS_SEASONS = (2023, 2024, 2025, 2026, 2027)
ODDS_ORDER = (2027, 2026, 2025, 2024, 2023)  # newest first: live-relevant and holdout seasons land first
SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    "?dates={d}&groups=50&limit=500"
)
ODDS_URL = (
    "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/"
    "events/{eid}/competitions/{eid}/odds"
)
USER_AGENT = "Mozilla/5.0"
MIN_INTERVAL_S = 0.35  # hard floor between request starts
DEFAULT_INTERVAL_S = 0.40
MAX_RETRIES = 7
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 90.0
HTTP_TIMEOUT_S = 30.0
VERIFY_SAMPLE = 25
VERIFY_SEED = 20261115
DISAGREE_POINTS = 2.0
SPREAD_ABS_MAX = 70.0  # a point spread beyond this is a price or garbage, never a line
TOTAL_MIN, TOTAL_MAX = 90.0, 250.0  # basketball totals; the NCAAF 15-120 band would drop them all
CLOSE_VS_CURRENT_TOL = 3.0  # a close spread this far from the details-derived line is rejected

# The rule. Edit nothing here after the first `fetch`: the hash is pinned on disk.
PINNED_RULE = {
    "version": "ncaab-espn-lines-rule-1",
    "population": (
        "completed ESPN men's college basketball Division I scoreboard events (groups=50) with season "
        "year 2023..2027 (2022-23 through 2026-27 to date); every such event is fetched, events with no "
        "odds are kept as no-line records"
    ),
    "line_semantics": (
        "archived closing/final pregame lines; no per-quote observed_at; a closing-line benchmark, "
        "not a decision-time quote. The opening line is the provider's open block where present"
    ),
    "excluded_items": (
        "any provider item whose name contains 'live' (case-insensitive) or that is flagged live; "
        "these are in-play lines"
    ),
    "home_spread_line_orientation": (
        "negative = home favoured by that many points. Magnitude from the last number in the item's "
        "`details` text; side from homeTeamOdds.favorite / awayTeamOdds.favorite (exactly one true). "
        "'EVEN'/'PK'/'PICK' details = 0. If details or flags are unusable, the home team's own "
        "point-spread block string is used (direct home-signed value). The raw `spread` field's sign is "
        "NEVER assumed and a provider with neither resolution is excluded from the median."
    ),
    "per_provider_value": (
        "spread: homeTeamOdds.close point spread when the item carries a close block, else the "
        "details/flags-derived current line. total: close.total when present, else overUnder, else "
        "current.total. open spread: homeTeamOdds.open point spread; open total: open.total."
    ),
    "plausibility_drops": (
        "a spread-field value with |x| > 70 or a total-field value outside [90, 250] is treated as "
        "missing (a price or garbage leaking into a line field; ESPN BET 2022-2023 close blocks carry "
        "prices) and is dropped BEFORE storage, so no price enters the records, and counted; a close spread "
        "is rejected (the details/flags current line is used instead) when it differs from that line by "
        "more than 3.0 points; disagreement between the home point-spread block and the details/flags "
        "line is counted, never silently resolved"
    ),
    "primary_line": (
        "per event and market, spread and total separately: the median (statistics.median, "
        "unrounded) across eligible non-live providers of their per-provider value; the opening line "
        "is the same median over providers' open values"
    ),
    "also_emitted": (
        "ESPN BET (provider name exactly 'ESPN BET') close value where present; eligible provider "
        "count; min/max/range across providers as a dispersion diagnostic"
    ),
    "no_line_reasons": "http_404, empty_items, only_live_items, no_spread_resolved, fetch_failed",
    "outcomes": "never joined in this script",
}


def _rule_hash() -> str:
    return hashlib.sha256(json.dumps(PINNED_RULE, sort_keys=True).encode()).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def assert_records_match_rule(stored: dict[str, dict[str, Any]]) -> None:
    """Every stored record carries the rule hash it was built under; refuse a drifted rule."""
    digest = _rule_hash()
    bad = sorted(eid for eid, rec in stored.items() if rec.get("rule_sha256") != digest)
    if bad:
        raise SystemExit(
            f"FATAL: {len(bad)} stored records were built under a different rule, e.g. {bad[:3]}"
        )


def read_progress() -> dict[str, Any]:
    if not PROGRESS_PATH.exists():
        return {}
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def merge_progress(section: str, payload: dict[str, Any]) -> None:
    current = read_progress()
    current[section] = payload
    current["rule_sha256"] = _rule_hash()
    tmp = PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(PROGRESS_PATH)


# --------------------------------------------------------------------------- parsing

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
_DETAILS_MAG = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*$")
_PICK_WORDS = {"EVEN", "PK", "PICK", "PICKEM", "PICK'EM", "PICK EM"}


def parse_num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUM.search(str(value))
    return float(match.group(0)) if match else None


def spread_ok(value: float | None) -> float | None:
    return value if value is not None and abs(value) <= SPREAD_ABS_MAX else None


def total_ok(value: float | None) -> float | None:
    return value if value is not None and TOTAL_MIN <= value <= TOTAL_MAX else None


def _block(team: dict[str, Any], name: str, drops: list[str]) -> str | None:
    """The line string of a point-spread block (display strings only). A price-like string is
    dropped before storage, so no American-odds price can enter the records."""
    point_spread = (team.get(name) or {}).get("pointSpread") or {}
    text = point_spread.get("alternateDisplayValue") or point_spread.get("american")
    if text is None:
        return None
    if spread_ok(parse_num(text)) is None:
        drops.append(f"ps_{name}_dropped")
        return None
    return str(text)


def _total_block(item: dict[str, Any], name: str, drops: list[str]) -> str | None:
    total = (item.get(name) or {}).get("total") or {}
    text = total.get("alternateDisplayValue") or total.get("american")
    if text is None:
        return None
    if total_ok(parse_num(text)) is None:
        drops.append(f"total_{name}_dropped")
        return None
    return str(text)


def _clean_spread(value: Any, label: str, drops: list[str]) -> Any:
    if value is None:
        return None
    if spread_ok(parse_num(value)) is None:
        drops.append(f"{label}_dropped")
        return None
    return value


def _clean_total(value: Any, label: str, drops: list[str]) -> Any:
    if value is None:
        return None
    if total_ok(parse_num(value)) is None:
        drops.append(f"{label}_dropped")
        return None
    return value


def derive_home_spread(
    details: str | None, home_fav: bool | None, away_fav: bool | None, home_ps_current: str | None
) -> tuple[float | None, str]:
    """Home spread, negative = home favoured. Never trusts the raw `spread` sign."""
    text = (details or "").strip()
    if text.upper() in _PICK_WORDS:
        return 0.0, "details_pick"
    match = _DETAILS_MAG.search(text)
    if match and abs(float(match.group(1))) <= SPREAD_ABS_MAX:
        magnitude = abs(float(match.group(1)))
        if magnitude == 0.0:
            return 0.0, "details_pick"
        if home_fav is True and away_fav is not True:
            return -magnitude, "details_flags"
        if away_fav is True and home_fav is not True:
            return magnitude, "details_flags"
    block_value = spread_ok(parse_num(home_ps_current))
    if block_value is not None:
        return block_value, "home_pointspread_block"
    return None, "unresolved"


def trim_item(item: dict[str, Any]) -> dict[str, Any]:
    """Keep only lines and orientation evidence; no moneyline or price field is read."""
    provider = item.get("provider") or {}
    name = str(provider.get("name") or "")
    home = item.get("homeTeamOdds") or {}
    away = item.get("awayTeamOdds") or {}
    home_fav = home.get("favorite") if isinstance(home.get("favorite"), bool) else None
    away_fav = away.get("favorite") if isinstance(away.get("favorite"), bool) else None
    drops: list[str] = []
    details = item.get("details")
    detail_match = _DETAILS_MAG.search(str(details or "").strip())
    if detail_match and abs(float(detail_match.group(1))) > SPREAD_ABS_MAX:
        drops.append("details_dropped")
        details = None
    home_ps_current = _block(home, "current", drops)
    home_ps_close = _block(home, "close", drops)
    spread_current, orientation = derive_home_spread(details, home_fav, away_fav, home_ps_current)
    is_live = "live" in name.lower() or bool(item.get("live")) or bool(provider.get("live"))
    if home_fav is True and away_fav is not True:
        fav_side: str | None = "home"
    elif away_fav is True and home_fav is not True:
        fav_side = "away"
    else:
        fav_side = None
    rec: dict[str, Any] = {
        "provider_id": str(provider.get("id")) if provider.get("id") is not None else None,
        "provider_name": name,
        "is_live": is_live,
        "details": details,
        "raw_spread": _clean_spread(item.get("spread"), "raw_spread", drops),
        "raw_over_under": _clean_total(item.get("overUnder"), "raw_over_under", drops),
        "initial_spread": _clean_spread(item.get("initialSpread"), "initial_spread", drops),
        "initial_over_under": _clean_total(item.get("initialOverUnder"), "initial_over_under", drops),
        "home_favorite": home_fav,
        "away_favorite": away_fav,
        "fav_side": fav_side,
        "home_ps_current": home_ps_current,
        "home_ps_open": _block(home, "open", drops),
        "home_ps_close": home_ps_close,
        "away_ps_current": _block(away, "current", drops),
        "total_current": _total_block(item, "current", drops),
        "total_open": _total_block(item, "open", drops),
        "total_close": _total_block(item, "close", drops),
        "home_spread_line": spread_current,
        "orientation_source": orientation,
    }
    rec["flags"] = drops + provider_flags(rec)
    return rec


def provider_flags(rec: dict[str, Any]) -> list[str]:
    """Data-quality flags for one provider item (counted in the audit, never silently fixed)."""
    flags: list[str] = []
    derived = rec["home_spread_line"]
    block = parse_num(rec["home_ps_current"])
    comparable_block = (
        rec["orientation_source"] in ("details_flags", "details_pick")
        and derived is not None
        and block is not None
    )
    if comparable_block and abs(block - derived) > 0.01:
        flags.append("block_vs_details_conflict")
        if block * derived < 0:
            flags.append("block_vs_details_sign_conflict")
    close = spread_ok(parse_num(rec["home_ps_close"]))
    if close is not None and derived is not None and abs(close - derived) > CLOSE_VS_CURRENT_TOL:
        flags.append("close_rejected_vs_current")
    return flags


def provider_values(rec: dict[str, Any]) -> tuple[float | None, float | None]:
    """Per-provider (spread, total) under the pinned per_provider_value / plausibility_drops rules."""
    spread = rec["home_spread_line"]
    close_value = spread_ok(parse_num(rec["home_ps_close"]))
    if close_value is not None and (spread is None or abs(close_value - spread) <= CLOSE_VS_CURRENT_TOL):
        spread = close_value
    total = None
    for key in ("total_close", "raw_over_under", "total_current"):
        total = total_ok(parse_num(rec[key]))
        if total is not None:
            break
    return spread, total


def provider_open_values(rec: dict[str, Any]) -> tuple[float | None, float | None]:
    """Per-provider opening (spread, total): the open blocks only, same plausibility bounds."""
    return spread_ok(parse_num(rec["home_ps_open"])), total_ok(parse_num(rec["total_open"]))


def select_lines(providers: list[dict[str, Any]]) -> dict[str, Any]:
    """The pinned selection rule. Pure function of provider items; no outcome input."""
    eligible = [p for p in providers if not p["is_live"]]
    spreads, totals, open_spreads, open_totals = [], [], [], []
    for provider in eligible:
        spread, total = provider_values(provider)
        if spread is not None:
            spreads.append(spread)
        if total is not None:
            totals.append(total)
        o_spread, o_total = provider_open_values(provider)
        if o_spread is not None:
            open_spreads.append(o_spread)
        if o_total is not None:
            open_totals.append(o_total)
    espn_bet = next((p for p in eligible if p["provider_name"] == "ESPN BET"), None)
    espn_bet_spread = espn_bet_total = None
    if espn_bet is not None:
        espn_bet_spread, espn_bet_total = provider_values(espn_bet)
    return {
        "rule_version": PINNED_RULE["version"],
        "n_nonlive_providers": len(eligible),
        "n_spread": len(spreads),
        "n_total": len(totals),
        "home_spread_line": statistics.median(spreads) if spreads else None,
        "spread_min": min(spreads) if spreads else None,
        "spread_max": max(spreads) if spreads else None,
        "spread_range": (max(spreads) - min(spreads)) if spreads else None,
        "total_line": statistics.median(totals) if totals else None,
        "total_min": min(totals) if totals else None,
        "total_max": max(totals) if totals else None,
        "total_range": (max(totals) - min(totals)) if totals else None,
        "n_open_spread": len(open_spreads),
        "n_open_total": len(open_totals),
        "open_home_spread_line": statistics.median(open_spreads) if open_spreads else None,
        "open_total_line": statistics.median(open_totals) if open_totals else None,
        "espn_bet_close_home_spread": espn_bet_spread,
        "espn_bet_close_total": espn_bet_total,
    }


def build_record(
    event: dict[str, Any], status: int | None, items: list[dict[str, Any]], failed: bool
) -> dict[str, Any]:
    providers = [trim_item(it) for it in items]
    selected = select_lines(providers)
    if failed:
        reason: str | None = "fetch_failed"
    elif status == 404:
        reason = "http_404"
    elif not providers:
        reason = "empty_items"
    elif selected["n_nonlive_providers"] == 0:
        reason = "only_live_items"
    elif selected["n_spread"] == 0:
        reason = "no_spread_resolved"
    else:
        reason = None
    return {
        "event_id": event["event_id"],
        "rule_sha256": _rule_hash(),
        "fetched_at_utc": utc_now(),
        "season_year": event["season_year"],
        "http_status": status,
        "no_line_reason": reason,
        "providers": providers,
        "selected": selected,
    }


# --------------------------------------------------------------------------- HTTP


class Fetcher:
    """Single-worker, paced, backoff-on-429/5xx GET client."""

    def __init__(self, interval: float) -> None:
        self.interval = max(interval, MIN_INTERVAL_S)
        self._last = 0.0
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)
        self.retries = 0

    def _pace(self) -> None:
        wait = self.interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, url: str) -> tuple[int | None, dict[str, Any] | None]:
        """(status, json). status None = failed after retries. 404 returns (404, None)."""
        for attempt in range(MAX_RETRIES + 1):
            self._pace()
            retry_after = 0.0
            try:
                response = self.client.get(url)
            except httpx.HTTPError as exc:
                _logger.warning("GET error %s attempt %d: %s", url, attempt, exc)
            else:
                if response.status_code == 404:
                    return 404, None
                if response.status_code == 200:
                    try:
                        return 200, response.json()
                    except ValueError:
                        _logger.warning("bad json %s", url)
                elif response.status_code == 429 or response.status_code >= 500:
                    retry_after = parse_num(response.headers.get("Retry-After")) or 0.0
                    _logger.warning("HTTP %d %s attempt %d", response.status_code, url, attempt)
                else:
                    _logger.warning("HTTP %d %s (no retry)", response.status_code, url)
                    return response.status_code, None
            if attempt == MAX_RETRIES:
                break
            self.retries += 1
            time.sleep(min(BACKOFF_CAP_S, max(retry_after, BACKOFF_BASE_S * (2**attempt))))
        return None, None

    def fetch_event(self, event: dict[str, Any]) -> dict[str, Any]:
        url = ODDS_URL.format(eid=event["event_id"])
        status, payload = self.get(url)
        if status is None:
            return build_record(event, None, [], failed=True)
        if status != 200 or payload is None:
            return build_record(event, status, [], failed=status not in (404,))
        items = list(payload.get("items") or [])
        page_count = int(payload.get("pageCount") or 1)
        for page in range(2, page_count + 1):
            status_p, payload_p = self.get(f"{url}?page={page}")
            if status_p != 200 or payload_p is None:
                return build_record(event, None, [], failed=True)
            items.extend(payload_p.get("items") or [])
        return build_record(event, 200, items, failed=False)


# --------------------------------------------------------------------------- scoreboard


def _int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


_STAT_NAMES = (
    "fieldGoalsAttempted",
    "fieldGoalsMade",
    "threePointFieldGoalsAttempted",
    "threePointFieldGoalsMade",
    "freeThrowsAttempted",
    "freeThrowsMade",
    "rebounds",
    "assists",
)


def _stat(competitor: dict[str, Any], name: str) -> float | None:
    for stat in competitor.get("statistics") or []:
        if stat.get("name") == name:
            return parse_num(stat.get("displayValue"))
    return None


def slim_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one scoreboard event to the fields the research needs (and nothing price-like)."""
    competition = (event.get("competitions") or [{}])[0]
    status = (competition.get("status") or {}).get("type", {})
    by_side = {item.get("homeAway"): item for item in competition.get("competitors") or []}
    away, home = by_side.get("away"), by_side.get("home")
    if not away or not home:
        return None
    row: dict[str, Any] = {
        "event_id": str(event.get("id")),
        "start_utc": event.get("date"),
        "season_year": (event.get("season") or {}).get("year"),
        "season_type": str((event.get("season") or {}).get("slug", "unknown")),
        "competition_type": str((competition.get("type") or {}).get("abbreviation", "unknown")),
        "neutral": bool(competition.get("neutralSite")),
        "conference_competition": bool(competition.get("conferenceCompetition")),
        "state": status.get("state"),
        "status_name": status.get("name"),
        "completed": bool(status.get("completed")),
    }
    for side, comp in (("home", home), ("away", away)):
        team = comp.get("team") or {}
        row[f"{side}_team"] = team.get("displayName")
        row[f"{side}_team_id"] = str(team.get("id"))
        row[f"{side}_conference_id"] = team.get("conferenceId")
        row[f"{side}_score"] = _int(comp.get("score"))
        for stat in _STAT_NAMES:
            row[f"{side}_{stat}"] = _stat(comp, stat)
        rank = _int((comp.get("curatedRank") or {}).get("current"))
        row[f"{side}_ap_rank"] = rank if rank is not None and 1 <= rank <= 25 else None
    return row


def season_dates() -> list[date]:
    days: list[date] = []
    for start, end in SEASON_WINDOWS.values():
        day = start
        while day <= end:
            days.append(day)
            day += timedelta(days=1)
    return sorted(days)


def read_scoreboard() -> dict[str, dict[str, Any]]:
    """Last record per date; a torn trailing line from a crash is ignored."""
    out: dict[str, dict[str, Any]] = {}
    if not SCOREBOARD_PATH.exists():
        return out
    with SCOREBOARD_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            out[rec["date"]] = rec
    return out


def _ensure_trailing_newline(path: Path) -> None:
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, 2)
            last = fh.read(1)
        if last != b"\n":
            with path.open("ab") as fh:
                fh.write(b"\n")


def cmd_scoreboard(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    done = {d for d, rec in read_scoreboard().items() if rec.get("http_status") == 200}
    todo = [d for d in season_dates() if d.isoformat() not in done]
    _logger.info(
        "scoreboard dates %d, already done %d, to fetch %d", len(season_dates()), len(done), len(todo)
    )
    fetcher = Fetcher(args.interval)
    _ensure_trailing_newline(SCOREBOARD_PATH)
    truncated = 0
    with SCOREBOARD_PATH.open("a", encoding="utf-8") as out:
        for index, day in enumerate(todo, start=1):
            status, payload = fetcher.get(SCOREBOARD_URL.format(d=day.strftime("%Y%m%d")))
            events = [slim_event(e) for e in ((payload or {}).get("events") or [])]
            events = [e for e in events if e is not None]
            rec = {
                "date": day.isoformat(),
                "fetched_at_utc": utc_now(),
                "http_status": status,
                "n_raw_events": len((payload or {}).get("events") or []),
                "events": events,
            }
            truncated += rec["n_raw_events"] >= 500
            out.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
            out.flush()
            if index % 25 == 0 or index == len(todo):
                _logger.info(
                    "%d/%d last=%s events=%d truncated_dates=%d",
                    index,
                    len(todo),
                    day,
                    len(events),
                    truncated,
                )
    return 0


def load_events() -> list[dict[str, Any]]:
    """Completed odds-population events from the scoreboard archive (de-duplicated by event id)."""
    events: dict[str, dict[str, Any]] = {}
    for rec in read_scoreboard().values():
        if rec.get("http_status") != 200:
            continue
        for ev in rec["events"]:
            if ev["season_year"] not in ODDS_SEASONS or not ev["completed"]:
                continue
            if ev["home_score"] is None or ev["away_score"] is None:
                continue
            events[ev["event_id"]] = {
                "event_id": ev["event_id"],
                "season_year": int(ev["season_year"]),
                "start_utc": ev["start_utc"],
                "home": ev["home_team"],
                "away": ev["away_team"],
            }
    order = {season: i for i, season in enumerate(ODDS_ORDER)}
    return sorted(events.values(), key=lambda e: (order[e["season_year"]], e["start_utc"], e["event_id"]))


# --------------------------------------------------------------------------- storage


def read_lines(path: Path = LINES_PATH) -> dict[str, dict[str, Any]]:
    """Last record per event_id; a torn trailing line from a crash is ignored."""
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            out[str(rec["event_id"])] = rec
    return out


def cmd_fetch(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    events = load_events()
    if not events:
        raise SystemExit("FATAL: no scoreboard events; run the scoreboard command first")
    existing = read_lines()
    assert_records_match_rule(existing)
    done = {eid for eid, rec in existing.items() if rec.get("no_line_reason") != "fetch_failed"}
    todo = [e for e in events if e["event_id"] not in done]
    _logger.info("events %d, already done %d, to fetch %d", len(events), len(done), len(todo))
    fetcher = Fetcher(args.interval)
    _ensure_trailing_newline(LINES_PATH)
    started = utc_now()
    if not (read_progress().get("rule") or {}).get("first_fetch_started_at_utc"):
        merge_progress("rule", {"rule_sha256": _rule_hash(), "first_fetch_started_at_utc": started})
    counts: Counter[str] = Counter()
    with LINES_PATH.open("a", encoding="utf-8") as out:
        for index, event in enumerate(todo, start=1):
            rec = fetcher.fetch_event(event)
            out.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
            out.flush()
            counts[rec["no_line_reason"] or "line"] += 1
            if index % 50 == 0 or index == len(todo):
                merge_progress(
                    "fetch",
                    {
                        "updated_at_utc": utc_now(),
                        "run_started_at_utc": started,
                        "events_total": len(events),
                        "done_before_run": len(done),
                        "fetched_this_run": index,
                        "remaining": len(todo) - index,
                        "outcome_counts_this_run": dict(counts),
                        "retries_this_run": fetcher.retries,
                        "last_season_year": event["season_year"],
                    },
                )
                _logger.info("%d/%d %s retries=%d", index, len(todo), dict(counts), fetcher.retries)
    _logger.info("fetch complete: %s", dict(counts))
    return 0


# --------------------------------------------------------------------------- verify


def comparable(rec: dict[str, Any]) -> dict[str, Any]:
    providers = sorted(rec["providers"], key=lambda p: (p["provider_id"] or "", p["provider_name"]))
    return {"no_line_reason": rec["no_line_reason"], "providers": providers, "selected": rec["selected"]}


def cmd_verify(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stored = read_lines()
    assert_records_match_rule(stored)
    events = {e["event_id"]: e for e in load_events()}
    pool = sorted(
        eid for eid, rec in stored.items() if rec.get("no_line_reason") != "fetch_failed" and eid in events
    )
    sample = random.Random(VERIFY_SEED).sample(pool, min(VERIFY_SAMPLE, len(pool)))
    fetcher = Fetcher(args.interval)
    results = []
    for eid in sample:
        fresh = fetcher.fetch_event(events[eid])
        same = comparable(fresh) == comparable(stored[eid])
        diffs = []
        if not same:
            old = {p["provider_id"]: p for p in stored[eid]["providers"]}
            new = {p["provider_id"]: p for p in fresh["providers"]}
            for pid in sorted(set(old) | set(new), key=str):
                if old.get(pid) != new.get(pid):
                    keys = sorted(
                        k
                        for k in set(old.get(pid) or {}) | set(new.get(pid) or {})
                        if (old.get(pid) or {}).get(k) != (new.get(pid) or {}).get(k)
                    )
                    diffs.append({"provider_id": pid, "fields": keys})
        results.append(
            {
                "event_id": eid,
                "season_year": events[eid]["season_year"],
                "match": same,
                "carried_line": stored[eid]["no_line_reason"] is None,
                "providers_stored": len(stored[eid]["providers"]),
                "providers_fresh": len(fresh["providers"]),
                "diffs": diffs,
            }
        )
    payload = {
        "verified_at_utc": utc_now(),
        "seed": VERIFY_SEED,
        "sampled": len(results),
        "matched": sum(1 for r in results if r["match"]),
        "sampled_with_lines": sum(1 for r in results if r["carried_line"]),
        "matched_with_lines": sum(1 for r in results if r["carried_line"] and r["match"]),
        "results": results,
    }
    merge_progress("verify", payload)
    _logger.info("verify: %d/%d matched", payload["matched"], payload["sampled"])
    return 0


# --------------------------------------------------------------------------- analyze (outcome-free)


def _pct(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 2) if den else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def recompute_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Re-derive every provider's orientation and flags from the stored raw fields, then re-select."""
    providers = []
    for prov in rec["providers"]:
        fresh = dict(prov)
        value, source = derive_home_spread(
            prov["details"], prov["home_favorite"], prov["away_favorite"], prov["home_ps_current"]
        )
        fresh["home_spread_line"] = value
        fresh["orientation_source"] = source
        fresh["flags"] = [f for f in prov["flags"] if f.endswith("_dropped")] + provider_flags(fresh)
        if fresh != prov:
            raise ValueError(f"stored provider drifted from re-derivation: {prov['provider_name']}")
        providers.append(fresh)
    return select_lines(providers)


def coverage(events: dict[str, dict[str, Any]], stored: dict[str, dict[str, Any]]) -> dict[str, Any]:
    seasons: dict[int, dict[str, Any]] = {}
    for season in ODDS_SEASONS:
        eids = [eid for eid, e in events.items() if e["season_year"] == season]
        recs = [stored[eid] for eid in eids if eid in stored]
        reasons: Counter[str] = Counter(r["no_line_reason"] or "line" for r in recs)
        with_spread = [r for r in recs if r["selected"]["n_spread"] > 0]
        with_total = [r for r in recs if r["selected"]["n_total"] > 0]
        with_open_s = [r for r in recs if r["selected"]["n_open_spread"] > 0]
        with_open_t = [r for r in recs if r["selected"]["n_open_total"] > 0]
        counts = [
            r["selected"]["n_nonlive_providers"] for r in recs if r["selected"]["n_nonlive_providers"] > 0
        ]
        spread_multi = [r["selected"]["spread_range"] for r in recs if r["selected"]["n_spread"] >= 2]
        total_multi = [r["selected"]["total_range"] for r in recs if r["selected"]["n_total"] >= 2]
        seasons[season] = {
            "events_in_scope": len(eids),
            "attempted": len(recs),
            "with_nonlive_spread": len(with_spread),
            "with_nonlive_spread_pct": _pct(len(with_spread), len(eids)),
            "with_total": len(with_total),
            "with_total_pct": _pct(len(with_total), len(eids)),
            "with_open_spread": len(with_open_s),
            "with_open_total": len(with_open_t),
            "median_nonlive_providers": _median([float(c) for c in counts]),
            "no_line_reasons": {k: v for k, v in sorted(reasons.items()) if k != "line"},
            "spread_multi_provider_events": len(spread_multi),
            "spread_disagree_gt2_pct": _pct(
                sum(r > DISAGREE_POINTS for r in spread_multi), len(spread_multi)
            ),
            "total_multi_provider_events": len(total_multi),
            "total_disagree_gt2_pct": _pct(sum(r > DISAGREE_POINTS for r in total_multi), len(total_multi)),
        }
    return {"per_season": {str(k): v for k, v in seasons.items()}}


def provider_inventory(stored: dict[str, dict[str, Any]]) -> dict[str, Any]:
    names: dict[str, Counter[int]] = defaultdict(Counter)
    orientation: Counter[str] = Counter()
    live_items = close_differs = close_present = 0
    flags: dict[int, Counter[str]] = defaultdict(Counter)
    for rec in stored.values():
        for prov in rec["providers"]:
            names[f"{prov['provider_id']}|{prov['provider_name']}"][rec["season_year"]] += 1
            if prov["is_live"]:
                live_items += 1
                continue
            orientation[prov["orientation_source"]] += 1
            flags[rec["season_year"]]["nonlive_items"] += 1
            flags[rec["season_year"]].update(prov["flags"])
            close = parse_num(prov["home_ps_close"])
            if close is not None:
                close_present += 1
                if prov["home_spread_line"] is not None and abs(close - prov["home_spread_line"]) > 1e-9:
                    close_differs += 1
    return {
        "providers": {
            k: {"items": sum(v.values()), "seasons": f"{min(v)}-{max(v)}"} for k, v in sorted(names.items())
        },
        "live_items_excluded": live_items,
        "nonlive_orientation_sources": dict(orientation),
        "close_block_present": close_present,
        "close_block_differs_from_current": close_differs,
        "flag_counts_by_season": {str(k): dict(v) for k, v in sorted(flags.items())},
    }


def scoreboard_summary() -> dict[str, Any]:
    sb = read_scoreboard()
    per_season: dict[int, Counter[str]] = defaultdict(Counter)
    truncated = failed = 0
    for rec in sb.values():
        failed += rec.get("http_status") != 200
        truncated += rec.get("n_raw_events", 0) >= 500
        for ev in rec["events"]:
            season = ev["season_year"]
            per_season[season]["events"] += 1
            per_season[season]["completed"] += bool(ev["completed"])
            per_season[season]["neutral"] += bool(ev["neutral"])
            per_season[season]["a_side_without_conference_id"] += (
                ev["home_conference_id"] is None or ev["away_conference_id"] is None
            )
    return {
        "dates_archived": len(sb),
        "dates_failed": failed,
        "dates_at_500_event_cap": truncated,
        "per_season_year": {
            str(k): dict(v) for k, v in sorted(per_season.items(), key=lambda kv: str(kv[0]))
        },
    }


def cmd_analyze(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    AUDIT_JSON.parent.mkdir(parents=True, exist_ok=True)
    events = {e["event_id"]: e for e in load_events()}
    stored = {eid: rec for eid, rec in read_lines().items() if eid in events}
    assert_records_match_rule(stored)
    recompute_bad = []
    for eid, rec in stored.items():
        try:
            if recompute_record(rec) != rec["selected"]:
                recompute_bad.append(eid)
        except ValueError:
            recompute_bad.append(eid)
    progress = read_progress()
    audit: dict[str, Any] = {
        "as_of_utc": AS_OF_UTC,
        "generated_at_utc": utc_now(),
        "script": "scripts/ncaab_espn_lines.py",
        "rule_pin": {
            "rule_sha256": _rule_hash(),
            "first_fetch_started_at_utc": (progress.get("rule") or {}).get("first_fetch_started_at_utc"),
        },
        "pinned_rule": PINNED_RULE,
        "lines_file": {
            "path": "data/research/ncaab_market/espn_lines.jsonl (untracked)",
            "sha256": hashlib.sha256(LINES_PATH.read_bytes()).hexdigest(),
            "bytes": LINES_PATH.stat().st_size,
            "records_unique_events": len(stored),
            "events_in_scope": len(events),
            "events_not_fetched": len(set(events) - set(stored)),
            "selected_recompute_mismatches": len(recompute_bad),
        },
        "scoreboard_file": {
            "path": "data/research/ncaab_market/scoreboard.jsonl (untracked)",
            "sha256": hashlib.sha256(SCOREBOARD_PATH.read_bytes()).hexdigest(),
            **scoreboard_summary(),
        },
        "coverage": coverage(events, stored),
        "providers": provider_inventory(stored),
        "verification_sample": progress.get("verify"),
    }
    AUDIT_JSON.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    AUDIT_MD.write_text(render_md(audit), encoding="utf-8")
    _logger.info("wrote %s and %s", AUDIT_JSON, AUDIT_MD)
    return 0


def render_md(audit: dict[str, Any]) -> str:
    lf, sf = audit["lines_file"], audit["scoreboard_file"]
    lines: list[str] = []
    add = lines.append
    add("# NCAAB historical betting lines from ESPN (quick-261115-1m8, Tier 3)")
    add("")
    add(f"- AS_OF_UTC: `{audit['as_of_utc']}`; generated `{audit['generated_at_utc']}`")
    add(
        "- Script: `scripts/ncaab_espn_lines.py`. Read-only GET of ESPN's public scoreboard and odds endpoints, "
        "single worker, generic User-Agent, no price or moneyline field stored."
    )
    add(
        f"- Lines file: {lf['path']}, unique events {lf['records_unique_events']} of {lf['events_in_scope']} in scope "
        f"(not fetched: {lf['events_not_fetched']}), sha256 `{lf['sha256']}`, {lf['bytes']} bytes; stored `selected` "
        f"blocks recomputed from the provider items with {lf['selected_recompute_mismatches']} mismatches."
    )
    add(
        f"- Scoreboard file: {sf['path']}, {sf['dates_archived']} dates ({sf['dates_failed']} failed, "
        f"{sf['dates_at_500_event_cap']} at the 500-event cap), sha256 `{sf['sha256']}`."
    )
    add("")
    add("## What these lines are")
    add("")
    add(
        "Archived closing/final pregame lines from ESPN's odds feed. There is no per-quote `observed_at`, so they are a "
        "closing-line benchmark and NOT decision-time quotes. Live and in-play items are excluded from every selected "
        "value. Only spread and total lines are kept. The opening line is the provider's own open block where present "
        "and is not a decision-time quote either."
    )
    add("")
    add("## Pinned selection rule")
    add("")
    add(
        f"Rule sha256 `{audit['rule_pin']['rule_sha256']}` is stamped on every stored record; first fetch started "
        f"`{audit['rule_pin']['first_fetch_started_at_utc']}`. Fixed before any outcome was joined; the script never "
        "joins outcomes."
    )
    add("")
    for key, value in audit["pinned_rule"].items():
        add(f"- **{key}**: {value}")
    add("")
    add("## Scoreboard archive by ESPN season year")
    add("")
    add("| Season year | Events | Completed | Neutral site | A side without conference id |")
    add("|---|---|---|---|---|")
    for season, row in sf["per_season_year"].items():
        add(
            f"| {season} | {row.get('events', 0)} | {row.get('completed', 0)} | {row.get('neutral', 0)} | "
            f"{row.get('a_side_without_conference_id', 0)} |"
        )
    add("")
    add("## Coverage by season (season year 2023 = 2022-23)")
    add("")
    add(
        "| Season | In scope | Attempted | Non-live spread | % | With total | % | Open spread | Open total | "
        "Median providers | No-line reasons | Spread disagree >2 pts (multi-provider) | Total disagree >2 pts |"
    )
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for season, row in audit["coverage"]["per_season"].items():
        add(
            f"| {season} | {row['events_in_scope']} | {row['attempted']} | {row['with_nonlive_spread']} | "
            f"{row['with_nonlive_spread_pct']} | {row['with_total']} | {row['with_total_pct']} | "
            f"{row['with_open_spread']} | {row['with_open_total']} | {row['median_nonlive_providers']} | "
            f"{row['no_line_reasons'] or '-'} | {row['spread_disagree_gt2_pct']}% of "
            f"{row['spread_multi_provider_events']} | {row['total_disagree_gt2_pct']}% of "
            f"{row['total_multi_provider_events']} |"
        )
    add("")
    add("## Providers seen")
    add("")
    inv = audit["providers"]
    add(
        f"Live items excluded: {inv['live_items_excluded']}. Non-live orientation sources: "
        f"{inv['nonlive_orientation_sources']}. Close block present on {inv['close_block_present']} non-live items; "
        f"differs from current on {inv['close_block_differs_from_current']}."
    )
    add("")
    add("| Provider (id|name) | Items | Seasons |")
    add("|---|---|---|")
    for key, value in inv["providers"].items():
        add(f"| {key} | {value['items']} | {value['seasons']} |")
    add("")
    add("Flag counts per season over non-live items (see `plausibility_drops`):")
    add("")
    for season, counts in inv["flag_counts_by_season"].items():
        add(f"- {season}: {counts}")
    add("")
    add("## Re-fetch verification (25 random events)")
    add("")
    ver = audit["verification_sample"]
    if ver is None:
        add("Not run yet.")
    else:
        add(
            f"Seed {ver['seed']}, sampled {ver['sampled']}, re-fetched at `{ver['verified_at_utc']}`: "
            f"**{ver['matched']} of {ver['sampled']} identical** (provider items, derived values, selected lines); "
            f"{ver['sampled_with_lines']} of the sampled events carried a line, {ver['matched_with_lines']} of those "
            "identical."
        )
        for row in [r for r in ver["results"] if not r["match"]]:
            add(f"- mismatch event {row['event_id']}: {row['diffs']}")
    add("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("scoreboard", cmd_scoreboard), ("fetch", cmd_fetch), ("verify", cmd_verify)):
        p = sub.add_parser(name)
        p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
        p.set_defaults(func=func)
    analyze = sub.add_parser("analyze")
    analyze.set_defaults(func=cmd_analyze)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
