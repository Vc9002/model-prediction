"""Pre-registered, model-free NCAAF stale-price test (quick-261121-spt, overhaul plan step 1).

Question: do 2026 Polymarket NCAAF decision-time asks lag the sportsbook line enough to profit, net of
the ask?

Procedure (everything that decides a pick or a verdict is in ``RULE`` below, committed with its sha256
before the first graded run):

* Polymarket full-game spread and total rungs only (period, half, quarter and team-total markets are
  separate slugs and are excluded by regex). Side mapping: for a spread rung, ``long`` is the event
  title's FIRST team at ``line`` (``long.description`` == line, ``short.description`` == -line, and the
  ``team`` field names the first title team in every resolvable row); for a total rung ``long`` is Over.
* Anchor arm A (primary, UPPER BOUND): the ESPN 2026 consensus close (``espn_lines.jsonl`` selected
  block). It postdates the quote, so any return it shows is an upper bound on a real-time strategy.
* Anchor arm B (secondary, clean, tiny n): the DraftKings line from the forward pregame capture, as of
  the quote time (latest non-live snapshot at or before the quote, at most ``dk_max_age_hours`` old).
* Model-free selection: only the rung whose line equals the anchor line (half-point anchors only, so
  fair is about 50% and a push cannot occur), buy a side whose ask is at or below ``max_ask``, earliest
  qualifying snapshot per event and market, graded from ``games.jsonl`` final scores, P&L per 1U at the
  ask, date-clustered bootstrap CI.

Subcommands:

    rule-sha   print the RULE sha256 (reads no data)
    dry-run    run selection and the whole report path on SEEDED FAKE outcomes; scores are never read.
               Writes to --out-dir (default: a temp dir), never to outputs/.
    run        the one graded run. Refuses unless this script is committed and unmodified at HEAD and
               the outputs do not exist yet. Writes outputs/ncaaf/cfb_stale_price_test.{json,md}.

Read-only over the data; no network, no order or account endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

REPO = Path(__file__).resolve().parents[1]
SCRIPT_REL = "scripts/cfb_stale_price_test.py"
OUT_JSON = REPO / "outputs" / "ncaaf" / "cfb_stale_price_test.json"
OUT_MD = REPO / "outputs" / "ncaaf" / "cfb_stale_price_test.md"
EASTERN = ZoneInfo("America/New_York")

RULE: dict[str, Any] = {
    "rule_version": "cfb-stale-price-rule-1",
    "registered_at_utc": "2026-11-21T04:30:00Z",
    "question": (
        "Do 2026 Polymarket NCAAF decision-time asks lag the sportsbook line enough to profit, net of the "
        "ask? Model-free: only the Polymarket rung at the anchor line is traded."
    ),
    "inputs": {
        "polymarket_glob": "E:/model-prediction-runtime/odds/ncaaf/*/polymarket_snapshots.jsonl",
        "polymarket_extra": ["data/odds/ncaaf/2026-11-04/polymarket_snapshots.jsonl"],
        "espn_lines": "data/research/ncaaf_v6/espn_lines.jsonl",
        "games": "data/processed/ncaaf/games.jsonl",
        "dk_capture": "E:/model-prediction-runtime/ncaaf_pregame_capture/espn_pregame_snapshots.jsonl",
    },
    "data_cutoff_utc": "2026-11-21T04:00:00Z",
    "data_cutoff_note": (
        "Polymarket rows and DraftKings snapshots observed after the cutoff are ignored, and games with "
        "kickoff after it are not graded, so appends to the live files after registration cannot change "
        "the sample. Input sha256 values are recorded at run time."
    ),
    "season_year": 2026,
    "markets": ["spread", "total"],
    "full_game_slug_regex": {
        "spread": r"^asc-cfb-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}-(pos|neg)-\d+pt\d$",
        "total": r"^tsc-cfb-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}-total-\d+pt\d$",
    },
    "row_filters": {
        "market_state": "MARKET_STATE_OPEN",
        "timestamp_valid": True,
        "usage": "prospective_executable_bbo",
        "reconstructed": False,
        "observed_strictly_before_kickoff": True,
        "kickoff": "min(Polymarket event_start_utc over the event's rows, ESPN games.jsonl event_start_utc)",
        "description_check": (
            "spread: float(long.description) == line and float(short.description) == -line; "
            "total: long.description == 'Over' and short.description == 'Under'; rows failing are excluded"
        ),
    },
    "side_mapping": {
        "spread": (
            "long = the event title's FIRST team ('A vs. B' -> A) covering at `line`; short = the other team "
            "at -line. home_line_of_rung = line if A is the ESPN home team else -line. The `team` field is "
            "an assertion only: a row whose `team` resolves to the OTHER team (by school name or mascot) is "
            "excluded; an unresolvable or ambiguous `team` (shared mascots) is accepted."
        ),
        "total": "long = Over at `line`, short = Under at `line`",
    },
    "matching": {
        "method": (
            "normalise names (drop U+FFFD, NFKD accent strip, lowercase, '-' -> space, drop apostrophes, "
            "okina and periods, keep [a-z0-9()& ], collapse spaces, then the alias map). ESPN school = the "
            "longest prefix of the ESPN display name that equals a normalised Polymarket school name; the "
            "rest is the mascot. A Polymarket event matches the unique 2026 games.jsonl game whose two "
            "schools equal the title's two schools with |kickoff difference| <= window; zero or several "
            "candidates = unmatched."
        ),
        "kickoff_window_hours": 36,
        "school_aliases": {
            "san jos state": "san jose state",
            "app state": "appalachian state",
            "k state": "kansas state",
            "la tech": "louisiana tech",
            "liu": "long island university",
            "louisiana monroe": "ul monroe",
            "mich state": "michigan state",
            "nm state": "new mexico state",
            "miami (fl)": "miami",
            "penn": "pennsylvania",
            "southeastern louisiana": "se louisiana",
            "umass": "massachusetts",
            "st thomas (mn)": "st thomas",
            "central connecticut state": "central connecticut",
            "grambling state": "grambling",
            "central state (oh)": "central state",
            "lane": "lane college",
            "fort lauderdale": "uftl",
            "glenville state college": "glenville state",
        },
        "mascot_aliases": {
            "runnin rebels": "rebels",
            "fightin blue hens": "blue hens",
            "ark pine bluff": "arkansas pine bluff",
        },
    },
    "anchors": {
        "espn_close": {
            "role": "PRIMARY, UPPER BOUND (the close postdates the quote); the verdict comes from this arm",
            "spread": "espn_lines.jsonl selected.home_spread_line (season_year 2026)",
            "total": "espn_lines.jsonl selected.total_line (season_year 2026)",
        },
        "dk_asof": {
            "role": "SECONDARY, decision-time clean, descriptive only (no verdict)",
            "source": (
                "forward capture core_odds.providers entry with provider_id '100' (DraftKings) and "
                "is_live false; spread = home_spread_current, total = total_current"
            ),
            "asof": (
                "latest snapshot with core_odds.observed_at_utc <= the quote's observed_at_utc and < kickoff "
                "and <= data cutoff"
            ),
            "dk_max_age_hours": 6.0,
        },
    },
    "selection": {
        "anchor_must_be_half_point": True,
        "integer_anchor_treatment": (
            "NO PICK (counted). Every Polymarket rung is x.5, so no rung equals an integer anchor; a rung "
            "half a point away is not fair at 50% and interpolating needs a margin distribution."
        ),
        "rung": "the full-game rung whose (home-oriented for spreads) line == anchor line exactly",
        "max_ask": 0.48,
        "max_ask_justification": (
            "At a half-point anchor the fair price of either side is about 50%. 48c is fair minus the 2% "
            "minimum edge the NCAAF Main gate already uses (edge band 2%-8%, ROADMAP College Football item), "
            "so the threshold is the house minimum edge, not a fitted value. One value, no grid."
        ),
        "min_ask": 0.0,
        "min_ask_size_contracts": 1.0,
        "one_pick_per": "(ESPN event_id, market) per anchor arm",
        "which_snapshot": "the EARLIEST qualifying row by (observed_at_utc, market_slug)",
        "both_sides_qualify": "take the lower ask; exact tie -> the long side",
    },
    "grading": {
        "source": "games.jsonl",
        "requires": (
            "integer home_score and away_score, status 'completed' or absent (history-appended rows carry no "
            "status but have final scores), kickoff before the data cutoff"
        ),
        "spread": "home covers iff home_score - away_score + home_line > 0 (half-point line: no push)",
        "total": "over iff home_score + away_score > line (half-point line: no push)",
        "pushes": "excluded (impossible at half-point lines; counted if ever seen)",
        "pnl_per_1u": "win: (1 - ask) / ask; loss: -1",
        "fee_rate": 0.0,
        "fee_note": "no platform fee is modelled; P&L is at the executable ask only",
    },
    "statistics": {
        "cluster": "kickoff date in America/New_York",
        "bootstrap_resamples": 2000,
        "bootstrap_seed": 20261121,
        "ci": "percentile 2.5 / 97.5 over resampled clusters",
        "metrics": [
            "ROI = sum(pnl) / n",
            "hit rate",
            "break-even = mean ask of the picks",
            "hit rate minus break-even",
        ],
    },
    "verdict": {
        "arm": "espn_close, spread and total pooled; by-market results are descriptive",
        "PREMISE_DEAD": "95% CI upper bound of ROI <= 0",
        "PREMISE_SUPPORTED_UPPER_BOUND": "95% CI lower bound of ROI > 0",
        "INCONCLUSIVE": "otherwise",
        "small_n": (
            "if the pooled arm has fewer than 30 graded picks or fewer than 5 date clusters the verdict is "
            "INCONCLUSIVE whatever the CI says (a bootstrap over a handful of clusters is degenerate)"
        ),
        "min_picks": 30,
        "min_clusters": 5,
    },
    "volume": (
        "espn_close arm picks per ET Saturday, over Saturdays (ET kickoff date) with at least one kept "
        "full-game rung of an event that has an ESPN close anchor; Saturdays with no valid capture or no "
        "ESPN anchor are listed separately and not in the mean"
    ),
}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def rule_sha256() -> str:
    return hashlib.sha256(canonical_json(RULE).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text)


# --------------------------------------------------------------------------- names
def _clean(text: str) -> str:
    text = text.replace("\ufffd", "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("-", " ")
    for ch in ("'", "\u02bb", "\u2019", "."):
        text = text.replace(ch, "")
    text = re.sub(r"[^a-z0-9()& ]", "", text)
    return " ".join(text.split())


def norm_school(text: str) -> str:
    cleaned = _clean(text)
    return RULE["matching"]["school_aliases"].get(cleaned, cleaned)


def norm_mascot(text: str) -> str:
    cleaned = _clean(text)
    return RULE["matching"]["mascot_aliases"].get(cleaned, cleaned)


@dataclass(frozen=True)
class EspnTeam:
    school: str
    mascot: str


def split_espn_display(display: str, schools: set[str]) -> EspnTeam | None:
    tokens = _clean(display).split()
    for k in range(len(tokens), 0, -1):
        cand = " ".join(tokens[:k])
        cand = RULE["matching"]["school_aliases"].get(cand, cand)
        if cand in schools:
            return EspnTeam(cand, " ".join(tokens[k:]))
    return None


# --------------------------------------------------------------------------- loading
def polymarket_paths() -> list[Path]:
    pattern = RULE["inputs"]["polymarket_glob"]
    base = Path(pattern.split("*")[0])
    paths = sorted(base.glob("*/polymarket_snapshots.jsonl"))
    paths += [REPO / p for p in RULE["inputs"]["polymarket_extra"]]
    return paths


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_inputs() -> dict[str, Any]:
    cutoff = parse_utc(RULE["data_cutoff_utc"])
    pm_paths = polymarket_paths()
    pm_rows: list[dict[str, Any]] = []
    for path in pm_paths:
        pm_rows.extend(read_jsonl(path))
    lines = read_jsonl(REPO / RULE["inputs"]["espn_lines"])
    games = read_jsonl(REPO / RULE["inputs"]["games"])
    dk_path = Path(RULE["inputs"]["dk_capture"])
    dk = read_jsonl(dk_path)
    hashes = {str(p): file_sha256(p) for p in pm_paths}
    hashes[RULE["inputs"]["espn_lines"]] = file_sha256(REPO / RULE["inputs"]["espn_lines"])
    hashes[RULE["inputs"]["games"]] = file_sha256(REPO / RULE["inputs"]["games"])
    hashes[str(dk_path)] = file_sha256(dk_path)
    return {"pm": pm_rows, "lines": lines, "games": games, "dk": dk, "hashes": hashes, "cutoff": cutoff}


# --------------------------------------------------------------------------- matching
def match_events(pm_rows: list[dict[str, Any]], games: list[dict[str, Any]]) -> dict[str, Any]:
    markets = set(RULE["markets"])
    titles: dict[str, str] = {}
    starts: dict[str, list[str]] = defaultdict(list)
    for r in pm_rows:
        if r.get("market_type") not in markets:
            continue
        eid = str(r["event_id"])
        if r.get("event_title"):
            titles.setdefault(eid, r["event_title"])
        if r.get("event_start_utc"):
            starts[eid].append(r["event_start_utc"])
    schools: set[str] = set()
    for title in titles.values():
        parts = title.split(" vs. ")
        if len(parts) == 2:
            schools.update(norm_school(p) for p in parts)
    season_games = [g for g in games if g.get("season_year") == RULE["season_year"]]
    split_cache: dict[str, EspnTeam | None] = {}

    def team_of(display: str) -> EspnTeam | None:
        if display not in split_cache:
            split_cache[display] = split_espn_display(display, schools)
        return split_cache[display]

    window = timedelta(hours=RULE["matching"]["kickoff_window_hours"])
    matched: dict[str, dict[str, Any]] = {}
    reasons: Counter[str] = Counter()
    for eid, title in titles.items():
        parts = title.split(" vs. ")
        if len(parts) != 2 or not starts[eid]:
            reasons["bad_title_or_start"] += 1
            continue
        first, second = (norm_school(p) for p in parts)
        pm_start = min(parse_utc(s) for s in starts[eid])
        cands = []
        for g in season_games:
            if abs(parse_utc(g["event_start_utc"]) - pm_start) > window:
                continue
            home, away = team_of(g["home_team"]), team_of(g["away_team"])
            if home is None or away is None or {home.school, away.school} != {first, second}:
                continue
            cands.append(g)
        if len(cands) != 1:
            reasons["no_game" if not cands else "ambiguous_game"] += 1
            continue
        g = cands[0]
        home, away = team_of(g["home_team"]), team_of(g["away_team"])
        assert home is not None and away is not None
        first_side = "home" if home.school == first else "away"
        kickoff = min(pm_start, parse_utc(g["event_start_utc"]))
        matched[eid] = {
            "game": g,
            "title": title,
            "first_side": first_side,
            "home": home,
            "away": away,
            "kickoff": kickoff,
        }
        reasons["matched"] += 1
    by_espn = Counter(m["game"]["event_id"] for m in matched.values())
    return {
        "matched": matched,
        "reasons": dict(reasons),
        "n_pm_events": len(titles),
        "espn_events_matched_twice": sum(1 for v in by_espn.values() if v > 1),
    }


def team_field_side(team: str | None, info: dict[str, Any]) -> str | None:
    """'home'/'away' when the `team` field names exactly one side, else None (unresolved/ambiguous)."""
    if not team:
        return None
    school, mascot = norm_school(team), norm_mascot(team)
    hits = [
        side
        for side in ("home", "away")
        if school == info[side].school or (info[side].mascot and mascot == info[side].mascot)
    ]
    return hits[0] if len(hits) == 1 else None


# --------------------------------------------------------------------------- candidate rungs
@dataclass(frozen=True)
class Rung:
    pm_event_id: str
    espn_event_id: str
    market: str
    observed: datetime
    market_slug: str
    anchor_line: float  # home-oriented spread line, or total line
    long_side: str  # 'home'/'away' for spreads, 'over' for totals
    short_side: str
    long_ask: float | None
    long_size: float | None
    short_ask: float | None
    short_size: float | None
    long_mid: float | None
    kickoff: datetime


def build_rungs(pm_rows: list[dict[str, Any]], matched: dict[str, Any], cutoff: datetime) -> dict[str, Any]:
    slug_re = {m: re.compile(p) for m, p in RULE["full_game_slug_regex"].items()}
    f = RULE["row_filters"]
    funnel: Counter[str] = Counter()
    rungs: list[Rung] = []
    for r in pm_rows:
        market = r.get("market_type")
        if market not in slug_re:
            continue
        if not slug_re[market].match(str(r.get("market_slug") or "")):
            funnel[f"{market}:not_full_game"] += 1
            continue
        eid = str(r["event_id"])
        info = matched.get(eid)
        if info is None:
            funnel[f"{market}:event_unmatched"] += 1
            continue
        if (
            r.get("market_state") != f["market_state"]
            or r.get("timestamp_valid") is not f["timestamp_valid"]
            or r.get("usage") != f["usage"]
            or r.get("reconstructed") is not f["reconstructed"]
        ):
            funnel[f"{market}:row_filter"] += 1
            continue
        observed = parse_utc(r["observed_at_utc"])
        if observed >= info["kickoff"]:
            funnel[f"{market}:at_or_after_kickoff"] += 1
            continue
        if observed > cutoff:
            funnel[f"{market}:after_cutoff"] += 1
            continue
        long, short = r.get("long") or {}, r.get("short") or {}
        line = float(r["line"])
        try:
            if market == "spread":
                ok = float(long["description"]) == line and float(short["description"]) == -line
            else:
                ok = long.get("description") == "Over" and short.get("description") == "Under"
        except (KeyError, TypeError, ValueError):
            ok = False
        if not ok:
            funnel[f"{market}:description_mismatch"] += 1
            continue
        if market == "spread":
            asserted = team_field_side(r.get("team"), info)
            if asserted is not None and asserted != info["first_side"]:
                funnel["spread:team_field_contradicts_title"] += 1
                continue
            long_side = info["first_side"]
            short_side = "away" if long_side == "home" else "home"
            anchor_line = line if long_side == "home" else -line
        else:
            long_side, short_side, anchor_line = "over", "under", line
        bid, ask = long.get("bid"), long.get("ask")
        mid = (float(bid) + float(ask)) / 2 if bid is not None and ask is not None else None
        rungs.append(
            Rung(
                pm_event_id=eid,
                espn_event_id=str(info["game"]["event_id"]),
                market=market,
                observed=observed,
                market_slug=str(r["market_slug"]),
                anchor_line=anchor_line,
                long_side=long_side,
                short_side=short_side,
                long_ask=None if long.get("ask") is None else float(long["ask"]),
                long_size=None if long.get("ask_size") is None else float(long["ask_size"]),
                short_ask=None if short.get("ask") is None else float(short["ask"]),
                short_size=None if short.get("ask_size") is None else float(short["ask_size"]),
                long_mid=mid,
                kickoff=info["kickoff"],
            )
        )
        funnel[f"{market}:kept"] += 1
    return {"rungs": rungs, "funnel": dict(funnel)}


# --------------------------------------------------------------------------- anchors
def espn_close_anchor(lines: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for rec in lines:
        if rec.get("season_year") != RULE["season_year"]:
            continue
        sel = rec.get("selected") or {}
        if sel.get("home_spread_line") is not None:
            out[(str(rec["event_id"]), "spread")] = float(sel["home_spread_line"])
        if sel.get("total_line") is not None:
            out[(str(rec["event_id"]), "total")] = float(sel["total_line"])
    return out


def dk_series(
    dk: list[dict[str, Any]], cutoff: datetime
) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    out: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for rec in dk:
        core = rec.get("core_odds") or {}
        if not core.get("observed_at_utc"):
            continue
        observed = parse_utc(core["observed_at_utc"])
        if observed > cutoff:
            continue
        for prov in core.get("providers") or []:
            if str(prov.get("provider_id")) != "100" or prov.get("is_live") is not False:
                continue
            eid = str(rec["event_id"])
            if prov.get("home_spread_current") is not None:
                out[(eid, "spread")].append((observed, float(prov["home_spread_current"])))
            if prov.get("total_current") is not None:
                out[(eid, "total")].append((observed, float(prov["total_current"])))
    for series in out.values():
        series.sort()
    return out


def dk_asof(series: list[tuple[datetime, float]], at: datetime, kickoff: datetime) -> float | None:
    max_age = timedelta(hours=RULE["anchors"]["dk_asof"]["dk_max_age_hours"])
    best = None
    for observed, value in series:
        if observed > at or observed >= kickoff:
            break
        best = (observed, value)
    if best is None or at - best[0] > max_age:
        return None
    return best[1]


def is_half_point(x: float) -> bool:
    return abs((x * 2) % 2 - 1) < 1e-9


# --------------------------------------------------------------------------- selection
def side_qualifies(ask: float | None, size: float | None) -> bool:
    sel = RULE["selection"]
    return (
        ask is not None
        and sel["min_ask"] < ask <= sel["max_ask"]
        and size is not None
        and size >= sel["min_ask_size_contracts"]
    )


def select_picks(rungs: list[Rung], anchor_of, arm: str) -> dict[str, Any]:
    """anchor_of(rung) -> anchor line or None. Returns picks plus an outcome-free funnel."""
    by_key: dict[tuple[str, str], list[Rung]] = defaultdict(list)
    for rung in rungs:
        by_key[(rung.espn_event_id, rung.market)].append(rung)
    picks: list[dict[str, Any]] = []
    funnel: Counter[str] = Counter()
    anchor_rung_mids: dict[str, list[float]] = defaultdict(list)
    for key in sorted(by_key):
        rows = sorted(by_key[key], key=lambda x: (x.observed, x.market_slug))
        market = key[1]
        any_anchor = any_half = any_rung = False
        latest_mid: float | None = None
        pick = None
        for rung in rows:
            anchor = anchor_of(rung)
            if anchor is None:
                continue
            any_anchor = True
            if not is_half_point(anchor):
                continue
            any_half = True
            if abs(rung.anchor_line - anchor) > 1e-9:
                continue
            any_rung = True
            if rung.long_mid is not None:
                # P(home covers) or P(over) at the anchor rung: an outcome-free side-mapping check.
                p_long = rung.long_mid
                latest_mid = p_long if rung.long_side in ("home", "over") else 1 - p_long
            if pick is not None:
                continue
            long_ok = side_qualifies(rung.long_ask, rung.long_size)
            short_ok = side_qualifies(rung.short_ask, rung.short_size)
            if not (long_ok or short_ok):
                continue
            if long_ok and short_ok:
                funnel[f"{market}:both_sides_qualified"] += 1
                take_long = rung.long_ask <= rung.short_ask  # type: ignore[operator]
            else:
                take_long = long_ok
            pick = {
                "arm": arm,
                "espn_event_id": key[0],
                "pm_event_id": rung.pm_event_id,
                "market": market,
                "side": rung.long_side if take_long else rung.short_side,
                "line": anchor,
                "ask": rung.long_ask if take_long else rung.short_ask,
                "ask_size": rung.long_size if take_long else rung.short_size,
                "observed_at_utc": rung.observed.isoformat(),
                "kickoff_utc": rung.kickoff.isoformat(),
                "lead_hours": round((rung.kickoff - rung.observed).total_seconds() / 3600, 2),
                "market_slug": rung.market_slug,
            }
        if not any_anchor:
            funnel[f"{market}:no_anchor"] += 1
        elif not any_half:
            funnel[f"{market}:integer_anchor_no_pick"] += 1
        elif not any_rung:
            funnel[f"{market}:no_rung_at_anchor"] += 1
        elif pick is None:
            funnel[f"{market}:rung_but_no_qualifying_ask"] += 1
        else:
            funnel[f"{market}:picked"] += 1
            picks.append(pick)
        if latest_mid is not None:
            anchor_rung_mids[market].append(latest_mid)
    mids_summary = {
        m: {
            "n_events": len(v),
            "quartiles": [round(float(q), 4) for q in np.quantile(v, [0.25, 0.5, 0.75])] if v else None,
            "n_abs_dev_gt_0p15": sum(1 for x in v if abs(x - 0.5) > 0.15),
        }
        for m, v in anchor_rung_mids.items()
    }
    return {"picks": picks, "funnel": dict(funnel), "anchor_rung_latest_mid": mids_summary}


# --------------------------------------------------------------------------- grading
def gradeable(game: dict[str, Any], cutoff: datetime) -> bool:
    return (
        isinstance(game.get("home_score"), int)
        and isinstance(game.get("away_score"), int)
        and game.get("status") in ("completed", None)
        and parse_utc(game["event_start_utc"]) < cutoff
    )


def grade(pick: dict[str, Any], game: dict[str, Any]) -> str:
    margin = game["home_score"] - game["away_score"]
    line = pick["line"]
    if pick["market"] == "spread":
        home_value = margin + line
        if abs(home_value) < 1e-9:
            return "PUSH"
        home_covers = home_value > 0
        return "WIN" if home_covers == (pick["side"] == "home") else "LOSS"
    total = game["home_score"] + game["away_score"]
    if abs(total - line) < 1e-9:
        return "PUSH"
    return "WIN" if (total > line) == (pick["side"] == "over") else "LOSS"


def pnl(result: str, ask: float) -> float:
    fee = RULE["grading"]["fee_rate"]
    return (1 - ask) * (1 - fee) / ask if result == "WIN" else -1.0


def eastern_date(iso: str) -> str:
    return parse_utc(iso).astimezone(EASTERN).date().isoformat()


def summarise(graded: list[dict[str, Any]]) -> dict[str, Any]:
    stats = RULE["statistics"]
    n = len(graded)
    if n == 0:
        return {"n": 0}
    wins = sum(1 for g in graded if g["result"] == "WIN")
    total_pnl = sum(g["pnl"] for g in graded)
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in graded:
        clusters[eastern_date(g["kickoff_utc"])].append(g)
    keys = sorted(clusters)
    arr = [
        (
            len(clusters[k]),
            sum(1 for g in clusters[k] if g["result"] == "WIN"),
            sum(g["pnl"] for g in clusters[k]),
            sum(g["ask"] for g in clusters[k]),
        )
        for k in keys
    ]
    a = np.array(arr, dtype=float)
    rng = np.random.default_rng(stats["bootstrap_seed"])
    roi_s, hit_s, edge_s = [], [], []
    for _ in range(stats["bootstrap_resamples"]):
        idx = rng.integers(0, len(keys), len(keys))
        s = a[idx].sum(axis=0)
        roi_s.append(s[2] / s[0])
        hit_s.append(s[1] / s[0])
        edge_s.append(s[1] / s[0] - s[3] / s[0])

    def ci(xs: list[float]) -> list[float]:
        return [round(float(np.percentile(xs, 2.5)), 4), round(float(np.percentile(xs, 97.5)), 4)]

    asks = [g["ask"] for g in graded]
    sizes = [g["ask_size"] for g in graded]
    return {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "n_clusters": len(keys),
        "hit_rate": round(wins / n, 4),
        "hit_rate_ci95": ci(hit_s),
        "break_even_mean_ask": round(sum(asks) / n, 4),
        "hit_minus_break_even": round(wins / n - sum(asks) / n, 4),
        "hit_minus_break_even_ci95": ci(edge_s),
        "pnl_units": round(total_pnl, 3),
        "roi": round(total_pnl / n, 4),
        "roi_ci95": ci(roi_s),
        "ask_size_quartiles": [round(float(q), 2) for q in np.quantile(sizes, [0.25, 0.5, 0.75])],
        "ask_size_lt_10": sum(1 for s in sizes if s < 10),
        "lead_hours_median": round(float(median(g["lead_hours"] for g in graded)), 2),
    }


def verdict(pooled: dict[str, Any]) -> str:
    v = RULE["verdict"]
    if pooled.get("n", 0) < v["min_picks"] or pooled.get("n_clusters", 0) < v["min_clusters"]:
        return "INCONCLUSIVE"
    lo, hi = pooled["roi_ci95"]
    if hi <= 0:
        return "PREMISE_DEAD"
    if lo > 0:
        return "PREMISE_SUPPORTED_UPPER_BOUND"
    return "INCONCLUSIVE"


# --------------------------------------------------------------------------- pipeline
def run_pipeline(fake_outcomes: bool) -> dict[str, Any]:
    data = load_inputs()
    cutoff: datetime = data["cutoff"]
    games = data["games"]
    if fake_outcomes:
        # Dry run: scores are removed before anything else touches the games, so no outcome is read.
        games = [{k: v for k, v in g.items() if k not in ("home_score", "away_score")} for g in games]
    m = match_events(data["pm"], games)
    matched = m["matched"]
    built = build_rungs(data["pm"], matched, cutoff)
    rungs: list[Rung] = built["rungs"]
    close = espn_close_anchor(data["lines"])
    dk = dk_series(data["dk"], cutoff)

    espn_events_lined = {k[0] for k in close}
    matched_espn = {str(i["game"]["event_id"]) for i in matched.values()}
    match_rates = {
        "pm_spread_total_events": m["n_pm_events"],
        "pm_events_matched_to_game": len(matched),
        "pm_events_matched_with_espn_2026_line": sum(
            1 for i in matched.values() if str(i["game"]["event_id"]) in espn_events_lined
        ),
        "espn_2026_lined_events": len(espn_events_lined),
        "espn_lined_events_covered_by_pm": len(espn_events_lined & matched_espn),
        "espn_events_matched_twice": m["espn_events_matched_twice"],
        "unmatched_reasons": m["reasons"],
    }

    arms = {
        "espn_close": select_picks(rungs, lambda r: close.get((r.espn_event_id, r.market)), "espn_close"),
        "dk_asof": select_picks(
            rungs,
            lambda r: (
                dk_asof(dk[(r.espn_event_id, r.market)], r.observed, r.kickoff)
                if (r.espn_event_id, r.market) in dk
                else None
            ),
            "dk_asof",
        ),
    }

    games_by_id = {str(g["event_id"]): g for g in games if g.get("season_year") == RULE["season_year"]}
    rng = np.random.default_rng(12345)
    results: dict[str, Any] = {}
    for arm, sel in arms.items():
        graded, ungraded = [], 0
        pushes = 0
        for pick in sel["picks"]:
            game = games_by_id.get(pick["espn_event_id"])
            if fake_outcomes:
                if game is None or parse_utc(game["event_start_utc"]) >= cutoff:
                    ungraded += 1
                    continue
                result = "WIN" if rng.random() < 0.5 else "LOSS"
            else:
                if game is None or not gradeable(game, cutoff):
                    ungraded += 1
                    continue
                result = grade(pick, game)
            if result == "PUSH":
                pushes += 1
                continue
            graded.append({**pick, "result": result, "pnl": pnl(result, pick["ask"])})
        by_market = {mk: summarise([g for g in graded if g["market"] == mk]) for mk in RULE["markets"]}
        pooled = summarise(graded)
        results[arm] = {
            "selection_funnel": sel["funnel"],
            "anchor_rung_latest_mid": sel["anchor_rung_latest_mid"],
            "n_picks": len(sel["picks"]),
            "ungraded": ungraded,
            "pushes_excluded": pushes,
            "pooled": pooled,
            "by_market": by_market,
            "picks": graded,
        }

    # Volume: picks per ET Saturday that had any full-game capture (espn_close arm, before grading).
    capture_saturdays = sorted(
        {
            eastern_date(r.kickoff.isoformat())
            for r in rungs
            if r.kickoff.astimezone(EASTERN).weekday() == 5 and (r.espn_event_id, r.market) in close
        }
    )
    all_kickoff_saturdays = sorted(
        {eastern_date(r.kickoff.isoformat()) for r in rungs if r.kickoff.astimezone(EASTERN).weekday() == 5}
    )
    per_sat = Counter(
        eastern_date(p["kickoff_utc"])
        for p in arms["espn_close"]["picks"]
        if parse_utc(p["kickoff_utc"]).astimezone(EASTERN).weekday() == 5
    )
    volume = {
        "espn_close_picks_by_saturday": {d: per_sat.get(d, 0) for d in capture_saturdays},
        "espn_close_mean_picks_per_saturday": (
            round(sum(per_sat.values()) / len(capture_saturdays), 2) if capture_saturdays else None
        ),
        "saturdays_with_kept_rungs_but_no_espn_anchor": sorted(
            set(all_kickoff_saturdays) - set(capture_saturdays)
        ),
        "espn_close_picks_total_all_days": len(arms["espn_close"]["picks"]),
        "dk_asof_picks_total_all_days": len(arms["dk_asof"]["picks"]),
        "note": (
            "the espn_close anchor exists only for games before the 2026-11-15 ESPN fetch, and Saturdays whose "
            "full-game rows are all timestamp_valid false have no kept rung"
        ),
    }
    return {
        "inputs_sha256": data["hashes"],
        "match_rates": match_rates,
        "rung_funnel": built["funnel"],
        "arms": results,
        "volume": volume,
        "verdict": verdict(results["espn_close"]["pooled"]),
        "fake_outcomes": fake_outcomes,
    }


# --------------------------------------------------------------------------- output
def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _fmt_ci(ci: list[float] | None) -> str:
    return "n/a" if not ci else f"[{100 * ci[0]:+.1f}%, {100 * ci[1]:+.1f}%]"


def render_md(report: dict[str, Any]) -> str:
    lines = [
        "# NCAAF stale-price test (quick-261121-spt, overhaul step 1)",
        "",
        *(
            ["**DRY RUN: outcomes are seeded coin flips, not results.**", ""]
            if report["fake_outcomes"]
            else []
        ),
        f"- Verdict (ESPN-close arm, pooled, UPPER BOUND): **{report['verdict']}**",
        f"- Rule `{RULE['rule_version']}` sha256 `{report['rule_sha256']}`",
        f"- Script sha256 `{report['script_sha256']}` at commit `{report.get('script_commit')}`",
        (
            f"- Run at {report['run_at_utc']}; data cutoff {RULE['data_cutoff_utc']}; fee 0; max ask "
            f"{100 * RULE['selection']['max_ask']:.0f}c; ask size >= "
            f"{RULE['selection']['min_ask_size_contracts']}"
        ),
        "",
        "## Match rates",
        "",
    ]
    mr = report["match_rates"]
    lines += [
        (
            f"- Polymarket spread/total events matched to a 2026 game: {mr['pm_events_matched_to_game']} of "
            f"{mr['pm_spread_total_events']}"
        ),
        (
            f"- Matched with an ESPN 2026 close: {mr['pm_events_matched_with_espn_2026_line']} of "
            f"{mr['pm_spread_total_events']}; ESPN lined events covered: "
            f"{mr['espn_lined_events_covered_by_pm']} of {mr['espn_2026_lined_events']}"
        ),
        "",
        "## Results",
        "",
        "| Arm | Market | n | W-L | Hit | Break-even (mean ask) | Hit - BE [95% CI] | ROI [95% CI] | PnL U |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, res in report["arms"].items():
        for label, s in [("pooled", res["pooled"])] + list(res["by_market"].items()):
            if not s.get("n"):
                lines.append(f"| {arm} | {label} | 0 | - | - | - | - | - | - |")
                continue
            lines.append(
                f"| {arm} | {label} | {s['n']} | {s['wins']}-{s['losses']} | {_fmt_pct(s['hit_rate'])} | "
                f"{_fmt_pct(s['break_even_mean_ask'])} | {100 * s['hit_minus_break_even']:+.1f} pts "
                f"{_fmt_ci(s['hit_minus_break_even_ci95'])} | {100 * s['roi']:+.1f}% {_fmt_ci(s['roi_ci95'])} | "
                f"{s['pnl_units']:+.2f} |"
            )
    lines += ["", "## Selection funnel (outcome-free)", ""]
    for arm, res in report["arms"].items():
        lines.append(
            f"- {arm}: {json.dumps(res['selection_funnel'], sort_keys=True)}; ungraded "
            f"{res['ungraded']}; pushes {res['pushes_excluded']}"
        )
    lines += ["", "## Side-mapping check (outcome-free)", ""]
    for arm, res in report["arms"].items():
        lines.append(
            f"- {arm}: latest pre-kickoff midpoint of P(home covers / over) at the anchor rung: "
            f"{json.dumps(res['anchor_rung_latest_mid'], sort_keys=True)}"
        )
    vol = report["volume"]
    lines += [
        "",
        "## Volume",
        "",
        (
            f"- ESPN-close arm picks per captured ET Saturday: mean "
            f"{vol['espn_close_mean_picks_per_saturday']}; by date "
            f"{json.dumps(vol['espn_close_picks_by_saturday'])}"
        ),
        (
            f"- Total picks, all days: ESPN-close {vol['espn_close_picks_total_all_days']}, DraftKings as-of "
            f"{vol['dk_asof_picks_total_all_days']}. {vol['note']}."
        ),
        "",
        "## Caveats",
        "",
        "- The ESPN close postdates every quote, so the ESPN arm is an upper bound on a real-time rule.",
        "- The DraftKings arm is decision-time clean but tiny and carries no verdict.",
        "- Ask sizes are top-of-book; capacity beyond the quoted size is not tested.",
        "- No platform fee is modelled.",
        "",
    ]
    return "\n".join(lines)


def git_guard() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout

    if git("status", "--porcelain", "--", SCRIPT_REL).strip():
        sys.exit("refusing: the script differs from HEAD; commit it first")
    commit = git("log", "-1", "--format=%H", "--", SCRIPT_REL).strip()
    if not commit:
        sys.exit("refusing: the script was never committed")
    return commit


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rule-sha")
    dry = sub.add_parser("dry-run")
    dry.add_argument("--out-dir", default=None)
    sub.add_parser("run")
    args = parser.parse_args()

    if args.cmd == "rule-sha":
        print(rule_sha256())
        return
    script_sha = file_sha256(Path(__file__).resolve())
    if args.cmd == "dry-run":
        out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp(prefix="cfb_spt_dry_"))
        if out_dir.resolve().is_relative_to((REPO / "outputs").resolve()):
            sys.exit("refusing: dry-run output must not go under outputs/")
        out_dir.mkdir(parents=True, exist_ok=True)
        report = run_pipeline(fake_outcomes=True)
        commit = None
        json_path, md_path = out_dir / "dry_run.json", out_dir / "dry_run.md"
    else:
        commit = git_guard()
        if OUT_JSON.exists() or OUT_MD.exists():
            sys.exit("refusing: outputs already exist (the graded run happens once)")
        report = run_pipeline(fake_outcomes=False)
        json_path, md_path = OUT_JSON, OUT_MD
    report = {
        "rule": RULE,
        "rule_sha256": rule_sha256(),
        "script_sha256": script_sha,
        "script_commit": commit,
        "run_at_utc": datetime.now(tz=ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **report,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_md(report), encoding="utf-8")
    print(f"verdict {report['verdict']} (fake_outcomes={report['fake_outcomes']})")
    print(f"wrote {json_path} and {md_path}")


if __name__ == "__main__":
    main()
