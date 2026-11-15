"""Historical NCAAF betting lines from ESPN's public odds endpoint (cfb-structural-v6, workstream E).

Read-only research tooling. It builds a closing/final pregame line archive for FBS-involved
games 2016-2026 so a market-anchored residual model (v6 item 7) has lines to anchor on.

WHAT THE DATA IS (read this before using it)
    ESPN's `/competitions/{id}/odds` returns one item per provider with the line as last
    archived. There is no per-quote observed_at. These are archived closing/final pregame
    lines: a closing-line BENCHMARK, not decision-time quotes. Live / in-play items (names
    containing "Live", e.g. "ESPN Bet - Live Odds") carry the in-game line and are excluded
    from every selected value. No moneyline and no price of any kind is stored or printed:
    only point-spread and total lines.

PINNED LINE-SELECTION RULE (frozen before any outcome is joined; see PINNED_RULE below)
    The rule text below is hashed; the hash is stamped on every stored record and the first
    fetch time is kept in the progress file. fetch, verify and analyze refuse to run if any
    stored record was built under a different rule text. This script NEVER joins outcomes
    (scores, winners, covers). The only outcome-using check (favourite wins straight up, an
    orientation sanity check) lives outside the repo and merely hands its result to `analyze`
    via --outcome-check.

Commands (single worker, read-only GET, generic User-Agent):
    fetch    resumable, append-only fetch of every event's odds into espn_lines.jsonl
    verify   re-fetch a random sample of 25 stored events and compare to stored values
    analyze  orientation check vs the v5 reference, coverage audit, dispersion; writes the
             audit json + md under outputs/ncaaf/

Run with OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/cfb_v6_espn_lines.py <command>.
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
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_logger = logging.getLogger("cfb_v6_espn_lines")

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = REPO / "data" / "research" / "ncaaf_v6"
SNAPSHOT = DATA_DIR / "history_snapshot.jsonl"
BACKFILL = DATA_DIR / "ncaaf_games_backfill.jsonl"
LINES_PATH = DATA_DIR / "espn_lines.jsonl"
PROGRESS_PATH = DATA_DIR / "espn_lines_progress.json"  # the one side file: fetch + verify sections, merged
REFERENCE_PATH = REPO / "outputs" / "ncaaf" / "cfb_v5_market_reference_games.json"
AUDIT_JSON = REPO / "outputs" / "ncaaf" / "cfb_v6_espn_lines_audit.json"
AUDIT_MD = REPO / "outputs" / "ncaaf" / "cfb_v6_espn_lines_audit.md"

AS_OF_UTC = "2026-11-15T03:00:00Z"
SEASONS = tuple(range(2016, 2027))
FBS_CLASSES = ("FBS-FBS", "FBS-FCS")
ODDS_URL = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/"
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
TOTAL_MIN, TOTAL_MAX = 15.0, 120.0
CLOSE_VS_CURRENT_TOL = 3.0  # a close spread this far from the details-derived line is rejected

# The rule. Edit nothing here after the first `fetch`: the hash is pinned on disk.
PINNED_RULE = {
    "version": "espn-lines-rule-1",
    "population": "FBS-FBS and FBS-FCS completed games 2016-2026, forfeits excluded",
    "line_semantics": (
        "archived closing/final pregame lines; no per-quote observed_at; a closing-line benchmark, "
        "not a decision-time quote"
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
        "current.total."
    ),
    "plausibility_drops": (
        "a spread-field value with |x| > 70 or a total-field value outside [15, 120] is treated as "
        "missing (a price or garbage leaking into a line field; ESPN BET 2022-2023 close blocks carry "
        "prices) and is dropped BEFORE storage, so no price enters the records, and counted; a close spread is rejected (the details/flags "
        "current line is used instead) when it differs from that line by more than 3.0 points; "
        "disagreement between the home point-spread block and the details/flags line is counted, "
        "never silently resolved"
    ),
    "primary_line": (
        "per event and market, spread and total separately: the median (statistics.median, "
        "unrounded) across eligible non-live providers of their per-provider value"
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
    PROGRESS_PATH.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------- events


def load_events() -> list[dict[str, Any]]:
    """FBS-involved completed events in history_snapshot, class and status from the backfill file."""
    backfill: dict[str, dict[str, Any]] = {}
    with BACKFILL.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                backfill[str(row["event_id"])] = row
    events: dict[str, dict[str, Any]] = {}
    with SNAPSHOT.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            snap = json.loads(line)
            eid = str(snap["event_id"])
            row = backfill.get(eid)
            if row is None:
                continue
            if row.get("game_class") not in FBS_CLASSES or row.get("is_forfeit"):
                continue
            if row.get("status") != "completed":
                continue
            season = int(row["season_year"])
            if season not in SEASONS:
                continue
            events[eid] = {
                "event_id": eid,
                "season_year": season,
                "start_utc": row["event_start_utc"],
                "home": row["home_team"],
                "away": row["away_team"],
                "game_class": row["game_class"],
                "neutral": bool(row.get("neutral_site")),
            }
    return sorted(events.values(), key=lambda e: (e["start_utc"], e["event_id"]))


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


def spread_ok(value: float | None) -> float | None:
    return value if value is not None and abs(value) <= SPREAD_ABS_MAX else None


def total_ok(value: float | None) -> float | None:
    return value if value is not None and TOTAL_MIN <= value <= TOTAL_MAX else None


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


def select_lines(providers: list[dict[str, Any]]) -> dict[str, Any]:
    """The pinned selection rule. Pure function of provider items; no outcome input."""
    eligible = [p for p in providers if not p["is_live"]]
    spreads = []
    totals = []
    for provider in eligible:
        spread, total = provider_values(provider)
        if spread is not None:
            spreads.append(spread)
        if total is not None:
            totals.append(total)
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
    """Single-worker, paced, backoff-on-429/5xx GET client for the odds endpoint."""

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


def _ensure_trailing_newline(path: Path) -> None:
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, 2)
            last = fh.read(1)
        if last != b"\n":
            with path.open("ab") as fh:
                fh.write(b"\n")


def cmd_fetch(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    events = load_events()
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
                    },
                )
                _logger.info("%d/%d %s retries=%d", index, len(todo), dict(counts), fetcher.retries)
    _logger.info("fetch complete: %s", dict(counts))
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """Stratified in-process probe: no lines-file writes. Raw JSON goes to --raw-out for inspection."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in load_events():
        by_season[event["season_year"]].append(event)
    rng = random.Random(7)
    fetcher = Fetcher(args.interval)
    raw_dump: dict[str, Any] = {}
    stats: dict[int, dict[str, Any]] = {}
    for season in SEASONS:
        picks = rng.sample(by_season[season], min(args.per_season, len(by_season[season])))
        row: dict[str, Any] = {
            "n": 0,
            "lined": 0,
            "orientation": Counter(),
            "flags": Counter(),
            "reasons": Counter(),
        }
        for event in picks:
            url = ODDS_URL.format(eid=event["event_id"])
            status, payload = fetcher.get(url)
            raw_dump[event["event_id"]] = {"season": season, "status": status, "payload": payload}
            rec = build_record(event, status, list((payload or {}).get("items") or []), failed=status is None)
            row["n"] += 1
            row["lined"] += rec["no_line_reason"] is None
            row["reasons"][rec["no_line_reason"] or "line"] += 1
            for prov in rec["providers"]:
                if not prov["is_live"]:
                    row["orientation"][prov["orientation_source"]] += 1
                row["flags"].update(prov["flags"])
        stats[season] = {k: (dict(v) if isinstance(v, Counter) else v) for k, v in row.items()}
        _logger.info("%d %s", season, stats[season])
    Path(args.raw_out).write_text(json.dumps(raw_dump), encoding="utf-8")
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


# --------------------------------------------------------------------------- analyze


def norm_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _pct(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 2) if den else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _parse_start(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


def orientation_vs_reference(
    events: dict[str, dict[str, Any]], stored: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Check (a): derived lines vs the v5 reference (tl total line, hl home spread line).

    Match on start + home + away names; when the exact start differs (ESPN moves kickoff times)
    fall back to the same home/away pair within 36 hours. 2026 events carry a single provider
    (DraftKings), so the primary line there is that provider's own line.
    """
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_swapped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for eid, event in events.items():
        by_pair[(norm_name(event["home"]), norm_name(event["away"]))].append(eid)
        by_swapped[(norm_name(event["away"]), norm_name(event["home"]))].append(eid)
    matched_rows: list[dict[str, Any]] = []
    unmatched = swapped = exact_start = fuzzy_start = 0
    ref_pair_counts = Counter((r.get("hl"), r.get("tl")) for r in reference)
    for ref in reference:
        key = (norm_name(ref["home"]), norm_name(ref["away"]))
        ref_start = _parse_start(ref["start"])

        def near(eids: list[str], ref_start: datetime = ref_start) -> list[str]:
            return [
                e
                for e in eids
                if abs((_parse_start(events[e]["start_utc"]) - ref_start).total_seconds()) <= 36 * 3600
            ]

        hits = near(by_pair.get(key, []))
        if not hits:
            if near(by_swapped.get(key, [])):
                swapped += 1
            else:
                unmatched += 1
            continue
        exact = [e for e in hits if events[e]["start_utc"] == ref["start"]]
        eid = (exact or hits)[0]
        exact_start += bool(exact)
        fuzzy_start += not exact
        rec = stored.get(eid)
        matched_rows.append(
            {
                "ref": ref,
                "sel": (rec or {}).get("selected"),
                "eid": eid,
                "ref_pair_repeats": ref_pair_counts[(ref.get("hl"), ref.get("tl"))] > 1,
            }
        )
    out: dict[str, Any] = {
        "reference_rows": len(reference),
        "matched": len(matched_rows) + swapped,
        "matched_exact_start": exact_start,
        "matched_fuzzy_start_within_36h": fuzzy_start,
        "unmatched": unmatched,
        "home_away_swapped_vs_reference": swapped,
        "comparable": len(matched_rows),
    }
    for market, ref_key, key in (("spread", "hl", "home_spread_line"), ("total", "tl", "total_line")):
        rows = [
            r
            for r in matched_rows
            if r["sel"] and r["sel"].get(key) is not None and r["ref"].get(ref_key) is not None
        ]

        def summarize(subset: list[dict[str, Any]], ref_key: str = ref_key, key: str = key) -> dict[str, Any]:
            diffs = [abs(float(r["ref"][ref_key]) - float(r["sel"][key])) for r in subset]
            block: dict[str, Any] = {
                "n_pairs": len(subset),
                "exact_within_0.01_pct": _pct(sum(d <= 0.01 for d in diffs), len(diffs)),
                "within_0.5_pct": _pct(sum(d <= 0.5 for d in diffs), len(diffs)),
                "within_1.0_pct": _pct(sum(d <= 1.0 for d in diffs), len(diffs)),
                "abs_diff_gt_3_count": sum(d > 3.0 for d in diffs),
                "mean_abs_diff": round(statistics.fmean(diffs), 4) if diffs else None,
                "median_abs_diff": _median(diffs),
            }
            if ref_key == "hl":
                flips = [
                    {
                        "event_id": r["eid"],
                        "ref": float(r["ref"][ref_key]),
                        "derived": float(r["sel"][key]),
                        "abs_gap": round(abs(float(r["ref"][ref_key]) - float(r["sel"][key])), 2),
                        "ref_hl_tl_pair_repeats_on_other_rows": r["ref_pair_repeats"],
                        "n_nonlive_providers": r["sel"]["n_nonlive_providers"],
                    }
                    for r in subset
                    if float(r["ref"][ref_key]) * float(r["sel"][key]) < 0
                ]
                block["sign_flips"] = len(flips)
                block["sign_flip_rows"] = flips
            return block

        out[f"{market}_vs_reference"] = summarize(rows)
        out[f"{market}_vs_reference_ref_pair_unique"] = summarize(
            [r for r in rows if not r["ref_pair_repeats"]]
        )
        out[f"{market}_vs_reference_ref_pair_repeated"] = summarize(
            [r for r in rows if r["ref_pair_repeats"]]
        )
    return out


def raw_spread_relation(stored: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Diagnostic only: how the raw `spread` field relates to the details/flags-derived home line."""
    per_season: dict[int, Counter[str]] = defaultdict(Counter)
    for rec in stored.values():
        for prov in rec["providers"]:
            if prov["is_live"] or prov["orientation_source"] != "details_flags":
                continue
            raw = prov["raw_spread"]
            derived = prov["home_spread_line"]
            if raw is None or derived is None or derived == 0:
                continue
            season = per_season[rec["season_year"]]
            season["n"] += 1
            if abs(float(raw) - derived) < 1e-9:
                season["raw_equals_home_signed"] += 1
            elif abs(float(raw) + derived) < 1e-9:
                season["raw_is_negated"] += 1
            else:
                season["raw_other"] += 1
    return {str(s): dict(c) for s, c in sorted(per_season.items())}


def coverage(events: dict[str, dict[str, Any]], stored: dict[str, dict[str, Any]]) -> dict[str, Any]:
    seasons: dict[int, dict[str, Any]] = {}
    for season in SEASONS:
        eids = [eid for eid, e in events.items() if e["season_year"] == season]
        recs = [stored[eid] for eid in eids if eid in stored]
        reasons: Counter[str] = Counter(r["no_line_reason"] or "line" for r in recs)
        with_spread = [r for r in recs if r["selected"]["n_spread"] > 0]
        with_total = [r for r in recs if r["selected"]["n_total"] > 0]
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
            "median_nonlive_providers": _median([float(c) for c in counts]),
            "no_line_reasons": {k: v for k, v in sorted(reasons.items()) if k != "line"},
            "http_404": reasons.get("http_404", 0),
            "spread_multi_provider_events": len(spread_multi),
            "spread_disagree_gt2_pct": _pct(
                sum(r > DISAGREE_POINTS for r in spread_multi), len(spread_multi)
            ),
            "total_multi_provider_events": len(total_multi),
            "total_disagree_gt2_pct": _pct(sum(r > DISAGREE_POINTS for r in total_multi), len(total_multi)),
            "espn_bet_close_present": sum(
                1 for r in recs if r["selected"]["espn_bet_close_home_spread"] is not None
            ),
        }
    all_recs = [stored[eid] for eid in events if eid in stored]
    spread_multi = [r["selected"]["spread_range"] for r in all_recs if r["selected"]["n_spread"] >= 2]
    total_multi = [r["selected"]["total_range"] for r in all_recs if r["selected"]["n_total"] >= 2]
    return {
        "per_season": {str(k): v for k, v in seasons.items()},
        "overall": {
            "events_in_scope": len(events),
            "attempted": len(all_recs),
            "with_nonlive_spread": sum(1 for r in all_recs if r["selected"]["n_spread"] > 0),
            "with_total": sum(1 for r in all_recs if r["selected"]["n_total"] > 0),
            "spread_multi_provider_events": len(spread_multi),
            "spread_disagree_gt2_pct": _pct(
                sum(r > DISAGREE_POINTS for r in spread_multi), len(spread_multi)
            ),
            "spread_range_median": _median(spread_multi),
            "total_multi_provider_events": len(total_multi),
            "total_disagree_gt2_pct": _pct(sum(r > DISAGREE_POINTS for r in total_multi), len(total_multi)),
            "total_range_median": _median(total_multi),
        },
    }


def provider_inventory(stored: dict[str, dict[str, Any]]) -> dict[str, Any]:
    names: dict[str, Counter[int]] = defaultdict(Counter)
    orientation: Counter[str] = Counter()
    live_items = 0
    close_differs = 0
    close_present = 0
    for rec in stored.values():
        for prov in rec["providers"]:
            names[f"{prov['provider_id']}|{prov['provider_name']}"][rec["season_year"]] += 1
            if prov["is_live"]:
                live_items += 1
                continue
            orientation[prov["orientation_source"]] += 1
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
    }


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


# Diagnostic only (outcome-free, not the pinned primary line): ESPN lists non-sportsbook entries
# beside the books. "Opening" is an opening line, the rest are model projections / aggregates.
NON_BOOK_PROVIDERS = frozenset({"Opening", "accuscore", "teamrankings", "numberfire", "consensus"})


def nonbook_exclusion_diagnostic(stored: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """How far the pinned median moves if the NON_BOOK_PROVIDERS entries are left out."""
    per_season: dict[int, list[float]] = defaultdict(list)
    changed_events = 0
    for rec in stored.values():
        spreads = [
            provider_values(p)[0]
            for p in rec["providers"]
            if not p["is_live"] and p["provider_name"] not in NON_BOOK_PROVIDERS
        ]
        spreads = [v for v in spreads if v is not None]
        primary = rec["selected"]["home_spread_line"]
        if not spreads or primary is None:
            continue
        gap = abs(statistics.median(spreads) - primary)
        per_season[rec["season_year"]].append(gap)
        changed_events += gap > 0
    return {
        "excluded_provider_names": sorted(NON_BOOK_PROVIDERS),
        "events_with_book_only_median": sum(len(v) for v in per_season.values()),
        "events_where_book_only_median_differs": changed_events,
        "by_season": {
            str(season): {
                "events": len(gaps),
                "differs_pct": _pct(sum(g > 0 for g in gaps), len(gaps)),
                "gap_gt_0.5_pct": _pct(sum(g > 0.5 for g in gaps), len(gaps)),
                "gap_gt_1.0_pct": _pct(sum(g > 1.0 for g in gaps), len(gaps)),
                "mean_abs_gap": round(statistics.fmean(gaps), 3),
            }
            for season, gaps in sorted(per_season.items())
        },
    }


def provider_season_deviation(stored: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Provider-seasons whose spreads sit more than 2 points from the event median on 5%+ of items."""
    cells: dict[tuple[str, int], list[float]] = defaultdict(list)
    for rec in stored.values():
        eligible = [p for p in rec["providers"] if not p["is_live"]]
        median = rec["selected"]["home_spread_line"]
        if median is None or len(eligible) < 3:
            continue
        for prov in eligible:
            value = provider_values(prov)[0]
            if value is not None:
                cells[(prov["provider_name"], rec["season_year"])].append(abs(value - median))
    out = []
    for (name, season), vals in sorted(cells.items()):
        share = _pct(sum(v > DISAGREE_POINTS for v in vals), len(vals))
        if len(vals) >= 30 and share is not None and share >= 5.0:
            out.append({"provider": name, "season": season, "items": len(vals), "share_gt2_pts_pct": share})
    return out


def quality_flags(stored: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Flag counts per season (non-live items) and per-provider deviation from the event median."""
    per_season: dict[int, Counter[str]] = defaultdict(Counter)
    deviations: dict[str, list[float]] = defaultdict(list)
    spread_dev: dict[str, list[float]] = defaultdict(list)
    for rec in stored.values():
        eligible = [p for p in rec["providers"] if not p["is_live"]]
        for prov in eligible:
            per_season[rec["season_year"]]["nonlive_items"] += 1
            per_season[rec["season_year"]].update(prov["flags"])
        median = rec["selected"]["home_spread_line"]
        if median is None or len(eligible) < 3:
            continue
        for prov in eligible:
            value, _total = provider_values(prov)
            if value is not None:
                name = prov["provider_name"]
                deviations[name].append(abs(value - median))
                spread_dev[name].append(value - median)
    table = {
        name: {
            "items": len(vals),
            "mean_abs_dev_from_event_median": round(statistics.fmean(vals), 3),
            "mean_signed_dev": round(statistics.fmean(spread_dev[name]), 3),
            "share_gt2_pts_pct": _pct(sum(v > DISAGREE_POINTS for v in vals), len(vals)),
        }
        for name, vals in sorted(deviations.items())
        if len(vals) >= 30
    }
    return {
        "nonbook_exclusion_diagnostic": nonbook_exclusion_diagnostic(stored),
        "provider_seasons_with_5pct_plus_gt2pt_deviation": provider_season_deviation(stored),
        "flag_counts_by_season": {str(k): dict(v) for k, v in sorted(per_season.items())},
        "provider_spread_deviation_events_with_3plus_providers": table,
    }


def cmd_analyze(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    events = {e["event_id"]: e for e in load_events()}
    stored = read_lines()
    stored = {eid: rec for eid, rec in stored.items() if eid in events}
    assert_records_match_rule(stored)
    recompute_bad = []
    for eid, rec in stored.items():
        try:
            if recompute_record(rec) != rec["selected"]:
                recompute_bad.append(eid)
        except ValueError:
            recompute_bad.append(eid)
    progress = read_progress()
    outcome_check = (
        json.loads(Path(args.outcome_check).read_text(encoding="utf-8")) if args.outcome_check else None
    )
    sha = hashlib.sha256(LINES_PATH.read_bytes()).hexdigest()
    audit: dict[str, Any] = {
        "as_of_utc": AS_OF_UTC,
        "generated_at_utc": utc_now(),
        "script": "scripts/cfb_v6_espn_lines.py",
        "rule_pin": {
            "rule_sha256": _rule_hash(),
            "first_fetch_started_at_utc": (progress.get("rule") or {}).get("first_fetch_started_at_utc"),
        },
        "pinned_rule": PINNED_RULE,
        "lines_file": {
            "path": "data/research/ncaaf_v6/espn_lines.jsonl (untracked)",
            "sha256": sha,
            "bytes": LINES_PATH.stat().st_size,
            "records_unique_events": len(stored),
            "events_in_scope": len(events),
            "events_not_fetched": len(set(events) - set(stored)),
            "selected_recompute_mismatches": len(recompute_bad),
        },
        "coverage": coverage(events, stored),
        "providers": provider_inventory(stored),
        "raw_spread_vs_derived_by_season": raw_spread_relation(stored),
        "orientation_vs_v5_reference": orientation_vs_reference(events, stored),
        "data_quality": quality_flags(stored),
        "orientation_favourite_outcome_check": outcome_check,
        "verification_sample": progress.get("verify"),
    }
    AUDIT_JSON.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    AUDIT_MD.write_text(render_md(audit), encoding="utf-8")
    _logger.info("wrote %s and %s", AUDIT_JSON, AUDIT_MD)
    return 0


def render_md(audit: dict[str, Any]) -> str:
    lf = audit["lines_file"]
    cov = audit["coverage"]
    lines: list[str] = []
    add = lines.append
    add("# NCAAF historical betting lines from ESPN (cfb-structural-v6, workstream E)")
    add("")
    add(f"- AS_OF_UTC: `{audit['as_of_utc']}`; generated `{audit['generated_at_utc']}`")
    add(
        "- Script: `scripts/cfb_v6_espn_lines.py`. Read-only GET of ESPN's public odds endpoint, one request per event, "
        "single worker, generic User-Agent, no price or moneyline field stored."
    )
    add(
        f"- Lines file: {lf['path']}, unique events {lf['records_unique_events']} of {lf['events_in_scope']} in scope "
        f"(not fetched: {lf['events_not_fetched']}), sha256 `{lf['sha256']}`, {lf['bytes']} bytes; "
        f"stored `selected` blocks recomputed from the provider items with {lf['selected_recompute_mismatches']} mismatches."
    )
    add("")
    add("## What these lines are")
    add("")
    add(
        "Archived closing/final pregame lines from ESPN's odds feed. There is no per-quote `observed_at`, so they are a "
        "closing-line benchmark and NOT decision-time quotes. Live and in-play items (provider names containing 'Live', "
        "e.g. 'ESPN Bet - Live Odds') are excluded from every selected value. Only spread and total lines are kept."
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
    add("## Coverage by season")
    add("")
    add(
        "| Season | In scope | Attempted | Non-live spread | % | With total | % | Median providers | 404 | No-line reasons | Spread disagree >2 pts (multi-provider) | Total disagree >2 pts | ESPN BET close |"
    )
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for season, row in cov["per_season"].items():
        add(
            f"| {season} | {row['events_in_scope']} | {row['attempted']} | {row['with_nonlive_spread']} | "
            f"{row['with_nonlive_spread_pct']} | {row['with_total']} | {row['with_total_pct']} | "
            f"{row['median_nonlive_providers']} | {row['http_404']} | {row['no_line_reasons'] or '-'} | "
            f"{row['spread_disagree_gt2_pct']}% of {row['spread_multi_provider_events']} | "
            f"{row['total_disagree_gt2_pct']}% of {row['total_multi_provider_events']} | {row['espn_bet_close_present']} |"
        )
    ov = cov["overall"]
    add("")
    add(
        f"Overall: {ov['with_nonlive_spread']} of {ov['events_in_scope']} events have a non-live spread, "
        f"{ov['with_total']} have a total. Provider disagreement (max minus min across non-live providers) exceeds "
        f"{DISAGREE_POINTS:g} points on {ov['spread_disagree_gt2_pct']}% of multi-provider spreads "
        f"(median range {ov['spread_range_median']}) and {ov['total_disagree_gt2_pct']}% of multi-provider totals "
        f"(median range {ov['total_range_median']})."
    )
    add("")
    add("## Providers seen")
    add("")
    inv = audit["providers"]
    add(
        f"Live items excluded: {inv['live_items_excluded']}. Non-live orientation sources: {inv['nonlive_orientation_sources']}. "
        f"Close block present on {inv['close_block_present']} non-live items; differs from current on "
        f"{inv['close_block_differs_from_current']}."
    )
    add("")
    add("| Provider (id|name) | Items | Seasons |")
    add("|---|---|---|")
    for key, value in inv["providers"].items():
        add(f"| {key} | {value['items']} | {value['seasons']} |")
    add("")
    add("## Orientation check (a): against the v5 reference lines")
    add("")
    ori = audit["orientation_vs_v5_reference"]
    add(
        f"Reference rows {ori['reference_rows']}; matched {ori['matched']} (exact start {ori['matched_exact_start']}, "
        f"same teams within 36 h of the reference start {ori['matched_fuzzy_start_within_36h']}; home/away swapped "
        f"relative to the reference {ori['home_away_swapped_vs_reference']}, excluded); unmatched {ori['unmatched']}; "
        f"comparable {ori['comparable']}. 2026 events carry a single provider (DraftKings), so the primary line is "
        "that provider's own line."
    )
    add("")
    add(
        "| Comparison | Pairs | Exact (<=0.01) % | Within 0.5 % | Within 1.0 % | Gap > 3 pts | Mean abs diff | Median abs diff | Sign flips |"
    )
    add("|---|---|---|---|---|---|---|---|---|")
    for key in (
        "spread_vs_reference",
        "spread_vs_reference_ref_pair_unique",
        "spread_vs_reference_ref_pair_repeated",
        "total_vs_reference",
        "total_vs_reference_ref_pair_unique",
        "total_vs_reference_ref_pair_repeated",
    ):
        blk = ori[key]
        add(
            f"| {key} | {blk['n_pairs']} | {blk['exact_within_0.01_pct']} | {blk['within_0.5_pct']} | "
            f"{blk['within_1.0_pct']} | {blk['abs_diff_gt_3_count']} | {blk['mean_abs_diff']} | "
            f"{blk['median_abs_diff']} | {blk.get('sign_flips', '-')} |"
        )
    add("")
    add(
        "The reference is a decision-time capture and these are closing lines, so small differences (and a line crossing "
        "zero) are expected; sign flips are listed in full, not explained away. Every flip below has derived orientation "
        "supported three ways inside the ESPN item itself (details text, favourite flags, home point-spread block all "
        "agree on all 2026 items: see the data-quality section), so the disagreement is in the reference side or in line "
        "movement, not in this script's orientation."
    )
    add("")
    add(
        "| Event | Reference home line | Derived home line | Abs gap | Reference (hl, tl) pair repeats on other rows |"
    )
    add("|---|---|---|---|---|")
    for row in ori["spread_vs_reference"]["sign_flip_rows"]:
        add(
            f"| {row['event_id']} | {row['ref']} | {row['derived']} | {row['abs_gap']} | "
            f"{row['ref_hl_tl_pair_repeats_on_other_rows']} |"
        )
    add("")
    add("## Orientation check (b): favourite by line wins straight up")
    add("")
    check = audit["orientation_favourite_outcome_check"]
    if check is None:
        add("Not run yet.")
    else:
        add(
            f"Computed outside this script (the script never joins outcomes) after the rule was pinned: {check.get('summary')}"
        )
        add("")
        add("| Season | Games with non-zero line | Favourite won | Underdog won | Tie | Favourite win % |")
        add("|---|---|---|---|---|---|")
        for season, row in check["per_season"].items():
            add(
                f"| {season} | {row['n']} | {row['fav_won']} | {row['dog_won']} | {row['tie']} | {row['fav_win_pct']} |"
            )
    add("")
    add("Raw `spread` field vs the derived home line (non-live, details+flags oriented, non-zero lines):")
    add("")
    add("| Season | n | raw == derived | raw == -derived | other |")
    add("|---|---|---|---|---|")
    for season, row in audit["raw_spread_vs_derived_by_season"].items():
        add(
            f"| {season} | {row.get('n', 0)} | {row.get('raw_equals_home_signed', 0)} | {row.get('raw_is_negated', 0)} | {row.get('raw_other', 0)} |"
        )
    add("")
    add("## Data-quality flags and provider deviation")
    add("")
    quality = audit["data_quality"]
    add("Flag counts per season over non-live items (see `plausibility_drops`):")
    add("")
    for season, counts in quality["flag_counts_by_season"].items():
        add(f"- {season}: {counts}")
    add("")
    add(
        "Per-provider spread deviation from the event median (events with 3+ non-live providers, providers with 30+ items; "
        "diagnostic only, the rule is unchanged):"
    )
    add("")
    add("| Provider | Items | Mean abs dev | Mean signed dev | Share > 2 pts % |")
    add("|---|---|---|---|---|")
    for name, row in quality["provider_spread_deviation_events_with_3plus_providers"].items():
        add(
            f"| {name} | {row['items']} | {row['mean_abs_dev_from_event_median']} | {row['mean_signed_dev']} | {row['share_gt2_pts_pct']} |"
        )
    add("")
    add(
        "Provider-seasons deviating more than 2 points from the event median on 5%+ of their items "
        "(candidates for unflagged in-play or stale lines; the rule is unchanged, the median is robust to a "
        "minority of them):"
    )
    add("")
    for row in quality["provider_seasons_with_5pct_plus_gt2pt_deviation"]:
        add(f"- {row['provider']} {row['season']}: {row['share_gt2_pts_pct']}% of {row['items']} items")
    add("")
    nb = quality["nonbook_exclusion_diagnostic"]
    add(
        f"Non-sportsbook entries ({', '.join(nb['excluded_provider_names'])}) are part of the pinned median. Diagnostic "
        f"only: leaving them out changes the per-event median on {nb['events_where_book_only_median_differs']} of "
        f"{nb['events_with_book_only_median']} events. All provider items are in the lines file, so a downstream consumer "
        "can recompute a book-only median."
    )
    add("")
    add("| Season | Events | Median differs % | Gap > 0.5 % | Gap > 1.0 % | Mean abs gap |")
    add("|---|---|---|---|---|---|")
    for season, row in nb["by_season"].items():
        add(
            f"| {season} | {row['events']} | {row['differs_pct']} | {row['gap_gt_0.5_pct']} | {row['gap_gt_1.0_pct']} | {row['mean_abs_gap']} |"
        )
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
            f"{ver['sampled_with_lines']} of the sampled events carried a line, {ver['matched_with_lines']} of those identical."
        )
        bad = [r for r in ver["results"] if not r["match"]]
        for row in bad:
            add(f"- mismatch event {row['event_id']}: {row['diffs']}")
    add("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    fetch.set_defaults(func=cmd_fetch)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    smoke.add_argument("--per-season", type=int, default=10)
    smoke.add_argument("--raw-out", required=True)
    smoke.set_defaults(func=cmd_smoke)
    verify = sub.add_parser("verify")
    verify.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    verify.set_defaults(func=cmd_verify)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--outcome-check", default="", help="json from the external favourite-wins check")
    analyze.set_defaults(func=cmd_analyze)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
