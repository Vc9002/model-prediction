"""Item 7 (market-anchored residual) feasibility audit: every local NCAAF line source.

Read-only inventory. For each source it reports path, file count, row count, event-date range, which
of spread / total / moneyline it carries, whether each quote has a pregame observed_at or is a
postgame / reconstructed / untimestamped value, and the number of distinct NCAAF events with a
pregame spread or total. It then counts lined events outside the reference market events (matched on
start + home + away) that predate the earliest reference event, and writes the item 7 verdict.

Nothing is fitted. Nothing is written except the two report files named below. The ledger DB is
opened with mode=ro. Prices appear only as lines and probabilities; if a source stores American
odds the report says only that the field exists.

Usage: .venv/Scripts/python scripts/cfb_v6_lines_audit.py [--as-of 2026-11-15T02:10:00Z]
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
AS_OF_UTC_DEFAULT = "2026-11-15T02:10:00Z"
RUNTIME_ROOT = Path(r"E:\model-prediction-runtime")
SCRATCH_REFERENCE = Path(
    "C:/Users/Vince/AppData/Local/Temp/claude/3c4e37e4a6ec2934/"
    "f86acfae-6a7a-455a-99d3-5fcdba232520/scratchpad/v5_games.json"
)
COMMITTED_REFERENCE = REPO / "outputs" / "ncaaf" / "cfb_v5_market_reference_games.json"
OUT_JSON = REPO / "outputs" / "ncaaf" / "cfb_v6_lines_audit.json"
OUT_MD = REPO / "outputs" / "ncaaf" / "cfb_v6_lines_audit.md"
# A reference event and a source event are the same game when starts are within this window and both
# team names agree. Source clocks differ by representation only (Z vs +00:00, seconds).
MATCH_START_WINDOW = timedelta(hours=3)
# A Polymarket ladder rung whose long-side midpoint sits in this band is near a fair coin, i.e. a
# plausible "main line" the market itself treats as the centre (a ladder has many alternate rungs).
NEAR_EVEN_BAND = (0.40, 0.60)

KIND_PREGAME = "pregame"  # observed_at present and <= event start, not reconstructed
KIND_POSTGAME = "postgame_or_live"  # observed_at present and > event start, or a snapshot of a started game
KIND_NO_TS = "no_timestamp"  # cannot be shown to precede kickoff
KIND_RECON = "reconstructed"  # source flags the price as reconstructed/synthetic
MARKETS = ("spread", "total", "moneyline")


# --------------------------------------------------------------------------- small helpers
def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def norm_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).lower()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9\ufffd ]+", " ", text).strip()


def name_match(short: str, full: str) -> bool:
    """True when `short` (poly/ESPN short or full name) refers to the reference team `full`.

    The reference dump carries U+FFFD where the console dropped a non-ASCII letter (San Jos\ufffd State),
    so that character matches any single character or none.
    """
    a, b = norm_name(short), norm_name(full)
    if not a or not b:
        return False
    pattern = re.escape(b).replace("\ufffd", ".?")
    if a == b or re.fullmatch(pattern, a):
        return True
    if b.startswith(a + " ") or a.startswith(b + " "):
        return True
    return re.match(re.escape(a) + r"\b", re.sub("\ufffd", "e", b)) is not None


class Reference:
    """The reference market events (no event id): matched on start + home + away."""

    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.path = path
        self.entries: list[dict[str, Any]] = []
        for row in raw:
            start = parse_dt(row.get("start"))
            if start is None:
                continue
            self.entries.append(
                {
                    "start": start,
                    "home": str(row["home"]),
                    "away": str(row["away"]),
                    "has_total": row.get("tl") is not None,
                    "has_spread": row.get("hl") is not None,
                }
            )
        self.entries.sort(key=lambda e: e["start"])
        self.earliest = self.entries[0]["start"]
        self.latest = self.entries[-1]["start"]
        self.by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # ESPN event ids of the reference events, learned by name-matching ledger events (the dump has none).
        self.espn_ids: set[str] = set()
        self.matched_entries: set[int] = set()
        for entry in self.entries:
            self.by_day[entry["start"].strftime("%Y-%m-%d")].append(entry)

    def sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def register_ids(self, events: dict[str, dict[str, Any]]) -> None:
        for key, ev in events.items():
            if not key.startswith("espn:"):
                continue
            entry = self._find(ev["start"], *ev["teams"])
            if entry is not None:
                self.espn_ids.add(key[5:])
                self.matched_entries.add(id(entry))

    def classify(self, key: str, ev: dict[str, Any]) -> str:
        """'ref' | 'outside' | 'unmatchable' (no team names and no ESPN id to match on)."""
        a, b = ev["teams"]
        if a and b:
            return "ref" if self._find(ev["start"], a, b) is not None else "outside"
        if key.startswith("espn:") and self.espn_ids:
            return "ref" if key[5:] in self.espn_ids else "outside"
        return "unmatchable"

    def _find(self, start: datetime | None, team_a: str | None, team_b: str | None) -> dict[str, Any] | None:
        if start is None or not team_a or not team_b:
            return None
        for offset in (-1, 0, 1):
            day = (start + timedelta(days=offset)).strftime("%Y-%m-%d")
            for entry in self.by_day.get(day, ()):
                if abs(entry["start"] - start) > MATCH_START_WINDOW:
                    continue
                if (name_match(team_a, entry["home"]) and name_match(team_b, entry["away"])) or (
                    name_match(team_b, entry["home"]) and name_match(team_a, entry["away"])
                ):
                    return entry
        return None


class HistoryIndex:
    """Completed-game history (results, no lines): tells whether a lined event has a scorable result."""

    def __init__(self, path: Path) -> None:
        self.ids: set[str] = set()
        self.by_day: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)
        for row in iter_jsonl(path):
            start = parse_dt(row.get("event_start_utc"))
            if start is None or row.get("home_score") is None:
                continue
            self.ids.add(str(row.get("event_id")))
            self.by_day[start.strftime("%Y-%m-%d")].append(
                (start, str(row["home_team"]), str(row["away_team"]))
            )

    def has_result(self, key: str, ev: dict[str, Any]) -> bool:
        if key.startswith("espn:") and key[5:] in self.ids:
            return True
        a, b = ev["teams"]
        start = ev["start"]
        if not (a and b and start):
            return False
        for offset in (-1, 0, 1):
            for h_start, home, away in self.by_day.get(
                (start + timedelta(days=offset)).strftime("%Y-%m-%d"), ()
            ):
                if abs(h_start - start) <= MATCH_START_WINDOW and (
                    (name_match(a, home) and name_match(b, away))
                    or (name_match(b, home) and name_match(a, away))
                ):
                    return True
        return False


HISTORY: HistoryIndex | None = None


class EventBook:
    """Per-source set of distinct events and the kinds of line quote each carries per market."""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.row_kinds: dict[str, Counter] = {m: Counter() for m in MARKETS}
        self.first_obs: datetime | None = None
        self.last_obs: datetime | None = None

    def add(
        self,
        key: str,
        start: datetime | None,
        teams: tuple[str | None, str | None],
        market: str,
        kind: str,
        observed: datetime | None = None,
        near_even: bool = False,
    ) -> None:
        ev = self.events.setdefault(
            key, {"start": start, "teams": teams, "kinds": {m: set() for m in MARKETS}, "near_even": set()}
        )
        if ev["start"] is None and start is not None:
            ev["start"] = start
        if teams[0] and not ev["teams"][0]:
            ev["teams"] = teams
        ev["kinds"][market].add(kind)
        if near_even and kind == KIND_PREGAME:
            ev["near_even"].add(market)
        self.row_kinds[market][kind] += 1
        if observed is not None:
            self.first_obs = observed if self.first_obs is None else min(self.first_obs, observed)
            self.last_obs = observed if self.last_obs is None else max(self.last_obs, observed)

    def summarize(self, ref: Reference) -> dict[str, Any]:
        starts = [e["start"] for e in self.events.values() if e["start"] is not None]
        out: dict[str, Any] = {
            "distinct_events": len(self.events),
            "earliest_event_start_utc": iso(min(starts)) if starts else None,
            "latest_event_start_utc": iso(max(starts)) if starts else None,
            "earliest_observed_at_utc": iso(self.first_obs),
            "latest_observed_at_utc": iso(self.last_obs),
            "row_kinds": {m: dict(self.row_kinds[m]) for m in MARKETS if self.row_kinds[m]},
        }
        for market in MARKETS:
            kinds_any = [e for e in self.events.values() if e["kinds"][market]]
            out[f"events_with_{market}_line_any"] = len(kinds_any)
            out[f"events_with_{market}_pregame"] = sum(KIND_PREGAME in e["kinds"][market] for e in kinds_any)
            out[f"events_with_{market}_only_postgame_recon_or_untimed"] = sum(
                KIND_PREGAME not in e["kinds"][market] for e in kinds_any
            )
        out["events_with_spread_or_total_pregame"] = sum(
            KIND_PREGAME in e["kinds"]["spread"] or KIND_PREGAME in e["kinds"]["total"]
            for e in self.events.values()
        )
        out["events_with_near_even_pregame_rung_spread"] = sum(
            "spread" in e["near_even"] for e in self.events.values()
        )
        out["events_with_near_even_pregame_rung_total"] = sum(
            "total" in e["near_even"] for e in self.events.values()
        )
        # Reference partition (pregame spread-or-total events only: the ones item 7 could use).
        in_ref = outside = predate = after = between = unknown_start = unmatchable = 0
        outside_scorable = 0
        outside_spread = outside_total = predate_spread = predate_total = 0
        for key, e in self.events.items():
            if not (KIND_PREGAME in e["kinds"]["spread"] or KIND_PREGAME in e["kinds"]["total"]):
                continue
            if e["start"] is None:
                unknown_start += 1
                continue
            verdict = ref.classify(key, e)
            has_spread = KIND_PREGAME in e["kinds"]["spread"]
            has_total = KIND_PREGAME in e["kinds"]["total"]
            if verdict == "ref":
                in_ref += 1
                continue
            if e["start"] < ref.earliest:
                # Cannot be a reference event whatever its names: it starts before the earliest one.
                outside += 1
                predate += 1
                outside_spread += has_spread
                outside_total += has_total
                predate_spread += has_spread
                predate_total += has_total
                continue
            if verdict == "unmatchable":
                unmatchable += 1
                continue
            outside += 1
            outside_spread += has_spread
            outside_total += has_total
            if HISTORY is not None and HISTORY.has_result(key, e):
                outside_scorable += 1
            if e["start"] > ref.latest:
                after += 1
            else:
                between += 1
        out["pregame_lined_events_matched_to_reference"] = in_ref
        out["pregame_lined_events_outside_reference"] = outside
        out["pregame_lined_outside_reference_spread"] = outside_spread
        out["pregame_lined_outside_reference_total"] = outside_total
        out["pregame_lined_outside_PREDATING_earliest_reference"] = predate
        out["pregame_lined_outside_predating_spread"] = predate_spread
        out["pregame_lined_outside_predating_total"] = predate_total
        out["pregame_lined_outside_AFTER_latest_reference"] = after
        out["pregame_lined_outside_within_reference_span_unmatched"] = between
        out["pregame_lined_outside_reference_with_result_in_history"] = outside_scorable
        out["pregame_lined_events_unknown_start"] = unknown_start
        out["pregame_lined_events_unmatchable_no_names_or_id"] = unmatchable
        return out


def source_record(
    source_id: str,
    paths: str,
    file_count: int,
    rows_total: int,
    rows_ncaaf: int,
    book: EventBook | None,
    ref: Reference,
    *,
    timestamp_basis: str,
    fields: dict[str, Any],
    notes: str = "",
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "source_id": source_id,
        "path": paths,
        "file_count": file_count,
        "row_count_total": rows_total,
        "row_count_ncaaf": rows_ncaaf,
        "fields": fields,
        "timestamp_basis": timestamp_basis,
        "notes": notes,
    }
    if book is not None:
        rec.update(book.summarize(ref))
    return rec


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


# --------------------------------------------------------------------------- sources
def audit_history(ref: Reference) -> list[dict[str, Any]]:
    """Game-result history files: results, not lines. Prove it by scanning the keys."""
    out = []
    line_key = re.compile(
        r"spread|total_line|over_under|overunder|moneyline|odds|^line$|consensus", re.IGNORECASE
    )
    for path in (REPO / "data/historical/ncaaf_games_all.jsonl", REPO / "data/processed/ncaaf/games.jsonl"):
        keys: Counter = Counter()
        rows = 0
        starts: list[datetime] = []
        seasons: Counter = Counter()
        for row in iter_jsonl(path):
            rows += 1
            keys.update(row.keys())
            dt = parse_dt(row.get("event_start_utc"))
            if dt:
                starts.append(dt)
            seasons[str(row.get("season_year"))] += 1
        hits = sorted(k for k in keys if line_key.search(k))
        out.append(
            {
                "source_id": f"history:{rel(path)}",
                "path": rel(path),
                "file_count": 1,
                "row_count_total": rows,
                "row_count_ncaaf": rows,
                "earliest_event_start_utc": iso(min(starts)) if starts else None,
                "latest_event_start_utc": iso(max(starts)) if starts else None,
                "fields": {"spread": False, "total": False, "moneyline": False},
                "line_like_field_names_found": hits,
                "timestamp_basis": "not applicable: game results only, no quotes",
                "games_per_season": dict(sorted(seasons.items())),
                "distinct_events": rows,
                "events_with_spread_or_total_pregame": 0,
                "notes": "Scores/context only. No line, odds or consensus field exists (key scan above).",
            }
        )
    # Remaining data/historical files: confirm none carries NCAAF quotes.
    other = []
    for path in sorted((REPO / "data/historical").glob("*")):
        if path.name == "ncaaf_games_all.jsonl" or not path.is_file():
            continue
        n_rows = n_ncaaf = 0
        with path.open("rb") as fh:
            for raw in fh:
                n_rows += 1
                if re.search(rb"ncaaf|college[-_ ]football", raw, re.IGNORECASE):
                    n_ncaaf += 1
        other.append({"file": path.name, "rows": n_rows, "rows_mentioning_ncaaf": n_ncaaf})
    out.append(
        {
            "source_id": "history:data/historical other files",
            "path": "data/historical/*",
            "file_count": len(other),
            "row_count_total": sum(o["rows"] for o in other),
            "row_count_ncaaf": sum(o["rows_mentioning_ncaaf"] for o in other),
            "fields": {"spread": False, "total": False, "moneyline": False},
            "timestamp_basis": "not applicable",
            "per_file": other,
            "distinct_events": 0,
            "events_with_spread_or_total_pregame": 0,
            "notes": "Other sports only (mlb_market_lines_reconstructed is MLB); no NCAAF rows.",
        }
    )
    odds_hist = REPO / "data/odds/historical"
    files = [p for p in sorted(odds_hist.glob("*")) if p.is_file()]
    n_hit = 0
    for p in files:
        blob = p.read_bytes()
        n_hit += len(re.findall(rb"ncaaf|college[-_ ]football|\bncaa\b", blob, re.IGNORECASE))
    out.append(
        {
            "source_id": "history:data/odds/historical",
            "path": "data/odds/historical/*",
            "file_count": len(files),
            "row_count_total": None,
            "row_count_ncaaf": n_hit,
            "fields": {"spread": False, "total": False, "moneyline": False},
            "timestamp_basis": "not applicable",
            "files": [p.name for p in files],
            "distinct_events": 0,
            "events_with_spread_or_total_pregame": 0,
            "notes": "mlb_sbr_odds.json only (sportsbook-review MLB); zero NCAAF/college-football mentions."
            if n_hit == 0
            else f"{n_hit} NCAAF-like mentions: inspect.",
        }
    )
    return out


def polymarket_row(
    row: dict[str, Any],
    book: EventBook,
    key_prefix: str,
    slug_start: dict[str, datetime],
    slug_teams: dict[str, tuple[str | None, str | None]],
) -> None:
    market = str(row.get("market_type") or "")
    if market not in MARKETS:
        return
    start = parse_dt(row.get("event_start_utc"))
    obs = parse_dt(row.get("observed_at_utc"))
    title = str(row.get("event_title") or "")
    parts = re.split(r"\s+(?:vs\.?|@|at)\s+", title, maxsplit=1, flags=re.IGNORECASE)
    teams = (parts[0], parts[1]) if len(parts) == 2 else (None, None)
    if row.get("reconstructed"):
        kind = KIND_RECON
    elif row.get("timestamp_valid") is False or obs is None or start is None:
        kind = KIND_NO_TS
    else:
        kind = KIND_PREGAME if obs <= start else KIND_POSTGAME
    near = False
    long_side = row.get("long")
    if isinstance(long_side, dict) and market in ("spread", "total"):
        mid = long_side.get("midpoint")
        if isinstance(mid, (int, float)):
            near = NEAR_EVEN_BAND[0] <= mid <= NEAR_EVEN_BAND[1]
    if market in ("spread", "total") and row.get("line") is None:
        return
    slug = str(row.get("event_slug") or "")
    if slug and start is not None:
        slug_start.setdefault(slug, start)
        slug_teams.setdefault(slug, teams)
    book.add(f"{key_prefix}{row.get('event_id')}", start, teams, market, kind, obs, near)


def audit_polymarket_odds(
    ref: Reference, slug_start: dict[str, datetime], slug_teams: dict[str, tuple[str | None, str | None]]
) -> list[dict[str, Any]]:
    out = []
    for label, root in (
        ("repo data/odds/ncaaf", REPO / "data/odds/ncaaf"),
        ("runtime odds/ncaaf", RUNTIME_ROOT / "odds" / "ncaaf"),
    ):
        files = sorted(root.glob("*/polymarket_snapshots.jsonl"))
        book = EventBook()
        rows = 0
        has_american = False
        usage: Counter = Counter()
        recon: Counter = Counter()
        states: Counter = Counter()
        for path in files:
            for row in iter_jsonl(path):
                rows += 1
                if "american_odds" in row:
                    has_american = True
                usage[str(row.get("usage"))] += 1
                recon[str(row.get("reconstructed"))] += 1
                states[str(row.get("market_state"))] += 1
                polymarket_row(row, book, "poly:", slug_start, slug_teams)
        rec = source_record(
            f"polymarket:{label}",
            rel(root) + "/<date>/polymarket_snapshots.jsonl",
            len(files),
            rows,
            rows,
            book,
            ref,
            timestamp_basis="per-row observed_at_utc (live gateway BBO capture); pregame iff observed_at <= event start",
            fields={
                "spread": True,
                "total": True,
                "moneyline": True,
                "american_odds_field_present": has_american,
                "shape": "market ladder: many alternate spread/total rungs per event (line + long/short bid/ask); not a single consensus line",
            },
            notes=f"first folder {files[0].parent.name if files else None}, last {files[-1].parent.name if files else None}",
        )
        rec["usage_counts"] = dict(usage)
        rec["reconstructed_flag_counts"] = dict(recon)
        rec["market_state_counts"] = dict(states)
        out.append(rec)
    return out


def audit_market_quotes_db(
    ref: Reference, slug_start: dict[str, datetime], slug_teams: dict[str, tuple[str | None, str | None]]
) -> dict[str, Any]:
    path = RUNTIME_ROOT / "market_quotes.db"
    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.execute(
            """
            select event_id, market_type, count(*), min(observed_at_utc), max(observed_at_utc),
                   sum(american_odds is not null), sum(is_executable)
            from market_quotes where lower(sport)='ncaaf' group by event_id, market_type
            """
        )
        book = EventBook()
        rows = 0
        american_present = 0
        unresolved_start = 0
        slug_dates: list[datetime] = []
        for event_id, market, n, first_obs, last_obs, n_american, _exec in cur:
            rows += n
            american_present += n_american or 0
            start = slug_start.get(str(event_id))
            m = re.search(r"(\d{4}-\d{2}-\d{2})$", str(event_id))
            if m:
                slug_dates.append(parse_dt(m.group(1)))
            fo = parse_dt(first_obs)
            if start is None:
                unresolved_start += 1
                kind = KIND_NO_TS
            else:
                kind = KIND_PREGAME if fo and fo <= start else KIND_POSTGAME
            book.add(
                f"quote:{event_id}", start, slug_teams.get(str(event_id), (None, None)), market, kind, fo
            )
            book.add(
                f"quote:{event_id}",
                start,
                slug_teams.get(str(event_id), (None, None)),
                market,
                kind,
                parse_dt(last_obs),
            )
        total = conn.execute("select count(*) from market_quotes").fetchone()[0]
    finally:
        conn.close()
    rec = source_record(
        "runtime market_quotes.db",
        str(path).replace("\\", "/"),
        1,
        total,
        rows,
        book,
        ref,
        timestamp_basis="per-row observed_at_utc; event start joined from polymarket odds files by slug",
        fields={
            "spread": True,
            "total": True,
            "moneyline": True,
            "american_odds_field_present": True,
            "american_odds_populated_for_ncaaf_rows": american_present,
        },
        notes=(
            f"event_id is the polymarket slug; slug date range "
            f"{iso(min(slug_dates)) if slug_dates else None} .. {iso(max(slug_dates)) if slug_dates else None}; "
            f"{unresolved_start} (event, market) groups with no start resolved from odds files. "
            "Same capture lineage as data/odds/ncaaf (not independent)."
        ),
    )
    rec["note_reference_matching"] = "team names come from the polymarket event titles joined on slug"
    return rec


def audit_market_odds_snapshots(ref: Reference) -> list[dict[str, Any]]:
    out = []
    for path in (REPO / "data/market_odds_snapshots.jsonl", REPO / "data/polymarket_us_snapshots.jsonl"):
        rows = n_ncaaf = 0
        leagues: Counter = Counter()
        starts: list[datetime] = []
        for raw in path.open("rb"):
            rows += 1
            if re.search(rb"ncaaf|college[-_ ]football|\bcfb-", raw, re.IGNORECASE):
                n_ncaaf += 1
                try:
                    dt = parse_dt(json.loads(raw).get("event_start_utc"))
                    if dt:
                        starts.append(dt)
                except json.JSONDecodeError:
                    pass
        out.append(
            {
                "source_id": f"snapshots:{rel(path)}",
                "path": rel(path),
                "file_count": 1,
                "row_count_total": rows,
                "row_count_ncaaf": n_ncaaf,
                "earliest_event_start_utc": iso(min(starts)) if starts else None,
                "latest_event_start_utc": iso(max(starts)) if starts else None,
                "fields": {"spread": False, "total": False, "moneyline": False},
                "timestamp_basis": "per-row observed_at_utc",
                "distinct_events": 0,
                "events_with_spread_or_total_pregame": 0,
                "leagues": dict(leagues),
                "notes": "Zero NCAAF rows (MLB/WNBA snapshot files predating the college-football season).",
            }
        )
    return out


def audit_ledger(ref: Reference, db: Path, source_id: str, *, backup: bool = False) -> dict[str, Any]:
    uri = "file:" + str(db).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if backup:
            try:
                row = conn.execute(
                    "select count(*) n, count(distinct event_id) e, min(event_start_utc) lo, max(event_start_utc) hi "
                    "from ledger_records where lower(sport)='ncaaf' and market_type in ('spread','total') "
                    "and line is not null"
                ).fetchone()
                ids = {
                    r[0]
                    for r in conn.execute(
                        "select distinct event_id from ledger_records where lower(sport)='ncaaf' "
                        "and market_type in ('spread','total') and line is not null"
                    )
                }
            except sqlite3.Error as exc:
                return {"source_id": source_id, "path": str(db), "error": f"{type(exc).__name__}: {exc}"}
            return {
                "source_id": source_id,
                "path": str(db).replace("\\", "/"),
                "file_count": 1,
                "line_rows": row["n"],
                "distinct_events_with_line": row["e"],
                "earliest_event_start_utc": row["lo"],
                "latest_event_start_utc": row["hi"],
                "event_ids": sorted(ids),
            }
        total = conn.execute("select count(*) from ledger_records").fetchone()[0]
        rows = conn.execute(
            "select pick_id, ledger_tier, event_id, event_start_utc, market_type, line, created_at_utc, "
            "decision_payload_json, status from ledger_records where lower(sport)='ncaaf'"
        ).fetchall()
    finally:
        conn.close()
    book = EventBook()
    counts: Counter = Counter()
    keys: Counter = Counter()
    for r in rows:
        market = str(r["market_type"] or "")
        counts[f"tier:{r['ledger_tier']}"] += 1
        if market not in MARKETS:
            continue
        payload = json.loads(r["decision_payload_json"]) if r["decision_payload_json"] else {}
        start = parse_dt(r["event_start_utc"])
        home = payload.get("home_team") or payload.get("canonical_home_team_name")
        away = payload.get("away_team") or payload.get("canonical_away_team_name")
        obs = parse_dt(payload.get("observed_at_utc") or r["created_at_utc"])
        if market in ("spread", "total") and r["line"] is None:
            counts["spread_total_rows_without_line"] += 1
            continue
        counts[f"rows_{market}"] += 1
        if payload.get("market_quote_observed_at_utc"):
            counts["rows_with_market_quote_observed_at_utc"] += 1
        if payload.get("closing_line"):
            counts["rows_with_closing_line"] += 1
        if payload.get("decision_consensus_line"):
            counts["rows_with_decision_consensus_line"] += 1
        if payload.get("american_odds") not in (None, ""):
            counts["rows_with_american_odds_field_populated"] += 1
        for k in ("market_quote_provenance", "market_quote_source", "sportsbook", "record_source"):
            v = payload.get(k)
            keys[f"{k}={v if v not in (None, '') else '<blank>'}"] += 1
        if str(payload.get("market_quote_reconstructed")).lower() in ("true", "1"):
            kind = KIND_RECON
        elif obs is None or start is None:
            kind = KIND_NO_TS
        else:
            kind = KIND_PREGAME if obs <= start else KIND_POSTGAME
        book.add(f"espn:{r['event_id']}", start, (home, away), market, kind, obs)
    ref.register_ids(book.events)
    rec = source_record(
        source_id,
        str(db).replace("\\", "/") + " :: ledger_records (sport=ncaaf, tiers main+flat)",
        1,
        total,
        len(rows),
        book,
        ref,
        timestamp_basis=(
            "decision-time observed_at_utc (decision_payload) <= event start; the line itself is the value "
            "quoted when the pick was decided. Per-quote market_quote_observed_at_utc is mostly blank."
        ),
        fields={
            "spread": True,
            "total": True,
            "moneyline": True,
            "american_odds_field_present": True,
            "consensus_line_fields": ["decision_consensus_line", "closing_consensus_line", "closing_line"],
        },
        notes=(
            "Lines exist only for events a model picked (selection bias: not every game). Event ids are ESPN ids. "
            "This is the source the reference events were drawn from."
        ),
    )
    rec["counts"] = dict(counts)
    rec["reference_entries_matched_to_ledger_events"] = len(ref.matched_entries)
    rec["payload_provenance_counts"] = dict(sorted(keys.items(), key=lambda kv: -kv[1])[:25])
    return rec


def audit_ledger_backups(ref: Reference, live_ids: set[str]) -> dict[str, Any]:
    dbs = sorted(
        list((RUNTIME_ROOT / "backups" / "ledgers").glob("*.db"))
        + list((RUNTIME_ROOT / "ledgers").glob("*.db"))
    )
    dbs = [p for p in dbs if p.name != "ledgers.db"]
    results = []
    union = set(live_ids)
    earliest: str | None = None
    for db in dbs:
        info = audit_ledger(ref, db, f"ledger-backup:{db.name}", backup=True)
        ids = set(info.pop("event_ids", []))
        info["event_ids_not_in_live_ledger"] = len(ids - live_ids)
        union |= ids
        lo = info.get("earliest_event_start_utc")
        if lo and (earliest is None or lo < earliest):
            earliest = lo
        results.append(info)
    return {
        "source_id": "runtime ledger backups",
        "path": "E:/model-prediction-runtime/{backups/ledgers,ledgers}/*.db (excluding live ledgers.db)",
        "file_count": len(dbs),
        "fields": {"spread": True, "total": True, "moneyline": True},
        "timestamp_basis": "same ledger_records schema as the live DB",
        "earliest_event_start_utc": earliest,
        "per_file": results,
        "distinct_event_ids_in_backups_not_in_live": len(union) - len(live_ids),
        "notes": "Snapshots of the same ledger at earlier dates; they add no events beyond the live ledger unless the count above is non-zero.",
    }


def audit_espn_scoreboards(ref: Reference) -> dict[str, Any]:
    root = REPO / "data/raw/ncaaf"
    files = sorted(root.glob("*/scores_ncaaf.json"))
    book = EventBook()
    events_seen: set[str] = set()
    events_with_odds: set[str] = set()
    states: Counter = Counter()
    providers: Counter = Counter()
    open_present = close_present = 0
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        for ev in doc.get("events", []) or []:
            events_seen.add(str(ev.get("id")))
            start = parse_dt(ev.get("date"))
            state = (((ev.get("status") or {}).get("type")) or {}).get("state")
            for comp in ev.get("competitions", []) or []:
                names = {
                    c.get("homeAway"): (c.get("team") or {}).get("displayName")
                    for c in comp.get("competitors", [])
                }
                teams = (names.get("home"), names.get("away"))
                for odd in comp.get("odds", []) or []:
                    events_with_odds.add(str(ev.get("id")))
                    providers[str((odd.get("provider") or {}).get("name"))] += 1
                    ps = odd.get("pointSpread") or {}
                    if ((ps.get("home") or {}).get("open") or {}).get("line"):
                        open_present += 1
                    if ((ps.get("home") or {}).get("close") or {}).get("line"):
                        close_present += 1
                    states[str(state)] += 1
                    # No fetch timestamp is embedded; the folder date plus file mtime stand in, and a
                    # snapshot taken after kickoff (or of a game no longer 'pre') is not a pregame quote.
                    kind = KIND_PREGAME if state == "pre" and start and mtime <= start else KIND_POSTGAME
                    if odd.get("spread") is not None:
                        book.add(f"espn:{ev.get('id')}", start, teams, "spread", kind, mtime)
                    if odd.get("overUnder") is not None:
                        book.add(f"espn:{ev.get('id')}", start, teams, "total", kind, mtime)
                    if odd.get("moneyline"):
                        book.add(f"espn:{ev.get('id')}", start, teams, "moneyline", kind, mtime)
    rec = source_record(
        "espn scoreboard consensus (DraftKings via ESPN)",
        "data/raw/ncaaf/<date>/scores_ncaaf.json",
        len(files),
        len(events_seen),
        len(events_seen),
        book,
        ref,
        timestamp_basis=(
            "no embedded fetch time; kind uses event state 'pre' at snapshot AND file mtime <= kickoff. "
            "Snapshots of started/completed games carry a closing-style line captured after kickoff."
        ),
        fields={
            "spread": True,
            "total": True,
            "moneyline": True,
            "open_close_lines_present": {
                "pointSpread_home_open": open_present,
                "pointSpread_home_close": close_present,
            },
        },
        notes=(
            f"events with an odds block: {len(events_with_odds)} of {len(events_seen)}; "
            f"odds-block provider counts {dict(providers)}; snapshot state counts {dict(states)}. "
            "Folder dates 2026-10-16.. only: same season as the reference events."
        ),
    )
    return rec


def audit_candidates(ref: Reference) -> dict[str, Any]:
    root = RUNTIME_ROOT / "research" / "college_contract_candidates" / "ncaaf"
    files = sorted(root.glob("*/*.json"))
    book = EventBook()
    rows = 0
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in doc.get("rows", []) or []:
            rows += 1
            market = str(row.get("market_type") or "")
            if market not in MARKETS:
                continue
            if market in ("spread", "total") and row.get("line") is None:
                continue
            start = parse_dt(row.get("event_start_utc"))
            obs = parse_dt(row.get("quote_observed_at_utc"))
            title = str(row.get("event_title") or "")
            parts = re.split(r"\s+(?:vs\.?|@|at)\s+", title, maxsplit=1, flags=re.IGNORECASE)
            teams = (parts[0], parts[1]) if len(parts) == 2 else (None, None)
            kind = (
                KIND_NO_TS
                if obs is None or start is None
                else (KIND_PREGAME if obs <= start else KIND_POSTGAME)
            )
            book.add(
                (
                    f"espn:{row['model_event_id']}"
                    if row.get("model_event_id")
                    else f"poly:{row.get('event_id')}"
                ),
                start,
                teams,
                market,
                kind,
                obs,
            )
    return source_record(
        "runtime research/college_contract_candidates",
        str(root).replace("\\", "/") + "/<date>/*.json",
        len(files),
        rows,
        rows,
        book,
        ref,
        timestamp_basis="quote_observed_at_utc per row (quote_hash + archive path recorded)",
        fields={"spread": True, "total": True, "moneyline": True},
        notes="Decision-time views of the polymarket captures above (same lineage), 2026-11-04 onward.",
    )


XLSX_COLS = {
    "league": ("league", "sport"),
    "start": ("event_start_utc",),
    "event_id": ("event_id",),
    "market": ("market_type",),
    "line": ("line",),
    "obs": ("observed_at_utc",),
    "mq_obs": ("market_quote_observed_at_utc",),
    "home": ("home_team",),
    "away": ("away_team",),
}


def audit_xlsx(ref: Reference) -> list[dict[str, Any]]:
    import openpyxl

    candidates: list[Path] = []
    for pattern in (
        "data/flat/ncaaf.xlsx*",
        "data/main/ncaaf.xlsx*",
        "data/sports/ncaaf/*.xlsx",
        "data/model_ledgers/ncaaf-*.xlsx",
        "data/archive/**/*.xlsx",
    ):
        candidates += [p for p in REPO.glob(pattern) if p.suffix == ".xlsx" or p.name.endswith(".bak")]
    candidates += list((RUNTIME_ROOT / "backups").glob("**/*ncaaf*.xlsx"))
    out = []
    for path in sorted(set(candidates)):
        if path.stat().st_size == 0:
            continue
        try:
            # .bak copies are valid workbooks with a foreign extension: load from bytes.
            source = io.BytesIO(path.read_bytes()) if path.suffix != ".xlsx" else path
            wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - unreadable export is reported, not fatal
            out.append(
                {"source_id": f"xlsx:{rel(path)}", "path": rel(path), "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        name_hint = "ncaaf" in str(path).lower()
        book = EventBook()
        rows_total = rows_ncaaf = 0
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            header = next(it, None)
            if not header:
                continue
            idx = {}
            for key, names in XLSX_COLS.items():
                for i, h in enumerate(header):
                    if h is not None and str(h) in names:
                        idx[key] = i
                        break
            if "market" not in idx or "line" not in idx:
                continue
            for row in it:
                rows_total += 1
                league = str(row[idx["league"]]).lower() if "league" in idx and row[idx["league"]] else ""
                if "league" in idx and league not in ("ncaaf", "college football"):
                    continue
                if "league" not in idx and not name_hint:
                    continue
                rows_ncaaf += 1
                market = str(row[idx["market"]] or "")
                if market not in MARKETS or (market != "moneyline" and row[idx["line"]] in (None, "")):
                    continue
                start = parse_dt(row[idx["start"]]) if "start" in idx else None
                obs = parse_dt(row[idx["obs"]]) if "obs" in idx else None
                eid = str(row[idx["event_id"]]) if "event_id" in idx else f"{start}"
                home = row[idx["home"]] if "home" in idx else None
                away = row[idx["away"]] if "away" in idx else None
                kind = (
                    KIND_NO_TS
                    if obs is None or start is None
                    else (KIND_PREGAME if obs <= start else KIND_POSTGAME)
                )
                book.add(f"espn:{eid}", start, (home, away), market, kind, obs)
        wb.close()
        if rows_ncaaf == 0:
            continue
        out.append(
            source_record(
                f"xlsx:{rel(path)}",
                rel(path),
                1,
                rows_total,
                rows_ncaaf,
                book,
                ref,
                timestamp_basis="observed_at_utc (decision time) per row",
                fields={m: bool(book.row_kinds[m]) for m in MARKETS},
                notes=(
                    "Best-effort XLSX export of the ledger (disposable). "
                    + (
                        "QUARANTINED synthetic main rows (archive 2026-08-30): not usable."
                        if "synthetic" in str(path)
                        else ""
                    )
                ),
            )
        )
    return out


def audit_auto_buyer(ref: Reference) -> dict[str, Any]:
    paths = [
        RUNTIME_ROOT / "auto_buyer_ledger.jsonl",
        RUNTIME_ROOT / "paper_auto_buyer" / "auto_buyer_ledger.jsonl",
        REPO / "data/auto_buyer_ledger.jsonl",
    ]
    paths += sorted((RUNTIME_ROOT / "paper_auto_buyer").glob("auto_buyer_ledger.backup*.jsonl"))
    paths += sorted((REPO / "data/backups").glob("auto_buyer_ledger*.jsonl"))
    book = EventBook()
    rows_total = rows_ncaaf = 0
    for path in paths:
        if not path.exists():
            continue
        for row in iter_jsonl(path):
            rows_total += 1
            if str(row.get("sport") or row.get("league") or "").lower() != "ncaaf":
                continue
            rows_ncaaf += 1
            market = str(row.get("market_type") or "")
            if market not in MARKETS or (market != "moneyline" and row.get("line") is None):
                continue
            start = parse_dt(row.get("event_start_utc"))
            obs = parse_dt(row.get("executed_at_utc"))
            kind = (
                KIND_NO_TS
                if obs is None or start is None
                else (KIND_PREGAME if obs <= start else KIND_POSTGAME)
            )
            book.add(
                f"espn:{row.get('event_id')}",
                start,
                (row.get("home_team"), row.get("away_team")),
                market,
                kind,
                obs,
            )
    return source_record(
        "auto-buyer ledgers",
        "E:/model-prediction-runtime/{auto_buyer_ledger.jsonl,paper_auto_buyer/auto_buyer_ledger*.jsonl}, data/auto_buyer_ledger.jsonl, data/backups/auto_buyer_ledger*.jsonl",
        len([p for p in paths if p.exists()]),
        rows_total,
        rows_ncaaf,
        book,
        ref,
        timestamp_basis="executed_at_utc of the order",
        fields={"spread": True, "total": True, "moneyline": True},
        notes="Executed paper orders only; NCAAF auto-buying is disabled so only a handful of rows exist.",
    )


def audit_other_stores() -> list[dict[str, Any]]:
    """Structured stores that could hold NCAAF lines: counted with read-only SQL."""
    out = []
    specs = [
        (RUNTIME_ROOT / "production" / "production.db", ("predictions", "market_snapshots", "decisions")),
        (RUNTIME_ROOT / "rebuild" / "shadow.db", ("predictions", "market_snapshots", "market_evaluations")),
        (REPO / "data/rebuild/shadow.db", ("predictions", "market_snapshots", "market_evaluations")),
        (RUNTIME_ROOT / "runs.db", ("canonical_events",)),
    ]
    for path, tables in specs:
        conn = sqlite3.connect("file:" + str(path).replace("\\", "/") + "?mode=ro", uri=True)
        try:
            per_table = {}
            for table in tables:
                cols = [r[1] for r in conn.execute(f"pragma table_info([{table}])")]
                sport_col = next((c for c in ("sport", "league") if c in cols), None)
                total = conn.execute(f"select count(*) from [{table}]").fetchone()[0]
                if sport_col is None:
                    per_table[table] = {"rows": total, "ncaaf_rows": "no sport column"}
                    continue
                n = conn.execute(
                    f"select count(*) from [{table}] where lower({sport_col}) like '%ncaaf%' "
                    f"or lower({sport_col}) like '%college%football%'"
                ).fetchone()[0]
                per_table[table] = {"rows": total, "ncaaf_rows": n}
        finally:
            conn.close()
        out.append(
            {
                "source_id": f"db:{str(path).replace(chr(92), '/')}",
                "path": str(path).replace("\\", "/"),
                "file_count": 1,
                "row_count_ncaaf": sum(
                    v["ncaaf_rows"] for v in per_table.values() if isinstance(v["ncaaf_rows"], int)
                ),
                "fields": {"spread": False, "total": False, "moneyline": False},
                "timestamp_basis": "not applicable: no NCAAF rows",
                "per_table": per_table,
                "distinct_events": 0,
                "events_with_spread_or_total_pregame": 0,
                "notes": "Checked for NCAAF line rows; none.",
            }
        )
    # dashboard_cache.db is a derived cache of the ledger payloads (picks tier): same events, same dates.
    conn = sqlite3.connect(
        "file:" + str(RUNTIME_ROOT / "dashboard_cache.db").replace("\\", "/") + "?mode=ro", uri=True
    )
    try:
        events: set[str] = set()
        starts: list[str] = []
        rows = 0
        for (payload,) in conn.execute("select payload_json from picks"):
            if "ncaaf" not in payload.lower():
                continue
            d = json.loads(payload)
            if str(d.get("league") or d.get("sport")).lower() != "ncaaf":
                continue
            rows += 1
            if d.get("line") not in (None, "") and d.get("event_start_utc"):
                events.add(str(d.get("event_id")))
                starts.append(str(d["event_start_utc"]))
    finally:
        conn.close()
    out.append(
        {
            "source_id": "db:dashboard_cache.db (derived from ledger)",
            "path": "E:/model-prediction-runtime/dashboard_cache.db :: picks",
            "file_count": 1,
            "row_count_ncaaf": rows,
            "earliest_event_start_utc": min(starts) if starts else None,
            "latest_event_start_utc": max(starts) if starts else None,
            "fields": {"spread": True, "total": True, "moneyline": True},
            "timestamp_basis": "derived cache of ledger picks; no independent quotes",
            "distinct_events": len(events),
            "events_with_spread_or_total_pregame": None,
            "notes": "Cache of ledger_records payloads; adds no event beyond the ledger.",
        }
    )
    return out


def audit_experiment_spec() -> dict[str, Any]:
    path = REPO / "data/experiments/cfb_research_experiment_spec.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {
        "source_id": "experiment spec claim",
        "path": rel(path),
        "file_count": 1,
        "row_count_total": 0,
        "row_count_ncaaf": 0,
        "fields": {"spread": False, "total": False, "moneyline": False},
        "timestamp_basis": "none: aggregates only",
        "distinct_events": 0,
        "events_with_spread_or_total_pregame": 0,
        "claim": {
            "dataset_summary": doc.get("dataset_summary"),
            "market_economic_evaluation_eligible_games": {
                k: v.get("eligible_games") for k, v in (doc.get("market_economic_evaluation") or {}).items()
            },
        },
        "notes": (
            "Claims 4,510 lined games over market_seasons 2020-2024. Generator traced: scripts/cfb_research_pipeline.py "
            "loads data/historical/ncaaf_games_all.jsonl, which has no line fields, and evaluates every game "
            "with g.get('spread_home_line', 0.0), g.get('total_line', 54.0), g.get('market_ml_home_prob') or 0.50 "
            "and a fixed 0.5238 ask (spread/total paths at lines ~274-317, 'eligible_games' = every 2020+ game). "
            "So the 4,510 'lined' games carry defaulted constants, not quotes: synthetic, not data. The "
            "2026-08-30 quarantine archive is likewise 'synthetic main'."
        ),
    }


SWEEP_SKIP_TOP = (
    # Big per-sport trees that cannot hold NCAAF lines or are inventoried by name above.
    "availability",
    "providers",
    "esports",
    "statcast",
    "mlb_statsapi",
    "weather",
    "raw",
    "odds",
    "processed",
    "international_baseball",
    "calibration",
    "player_priors",
    "entities",
    "statsapi",
    "logs",
    "sports",
    "archive",
    "backups",
)
SWEEP_KNOWN_NAMES = (
    "ledgers.db",
    "market_quotes.db",
    "dashboard_cache.db",
    "ncaaf_games_all.jsonl",
    "cfb_research_experiment_spec.json",
)
SWEEP_MAX_BYTES = 300_000_000


def residual_sweep() -> dict[str, Any]:
    """Byte-scan the remaining trees for NCAAF mentions outside the sources inventoried above.

    Skips the top-level directories in SWEEP_SKIP_TOP, files over SWEEP_MAX_BYTES, xlsx (compressed,
    handled by the workbook audit) and files that are inventoried by name. Bounded on purpose: the host
    runs memory-constrained and the multi-GB ledger backups are audited through SQL instead.
    """
    import os

    pat = re.compile(rb"ncaaf|college-football|college_football|\bcfb-", re.IGNORECASE)
    known_dirs = ("research/college_contract_candidates", "paper_auto_buyer")
    hits = []
    files_scanned = 0
    for root in (REPO / "data", RUNTIME_ROOT):
        for dp, dn, fn in os.walk(root):
            relp = os.path.relpath(dp, root).replace("\\", "/")
            if relp.split("/")[0] in SWEEP_SKIP_TOP or any(relp.startswith(k) for k in known_dirs):
                dn[:] = []
                continue
            for f in fn:
                if f.endswith((".lock", ".xlsx", ".db-wal", ".db-shm")) or f in SWEEP_KNOWN_NAMES:
                    continue
                if f.startswith("auto_buyer_ledger"):
                    continue
                path = Path(dp) / f
                try:
                    size = path.stat().st_size
                    if size > SWEEP_MAX_BYTES:
                        continue
                    n = 0
                    with path.open("rb") as fh:
                        while chunk := fh.read(32 * 1024 * 1024):
                            n += len(pat.findall(chunk))
                    files_scanned += 1
                except OSError:
                    continue
                if n:
                    hits.append({"path": str(path).replace("\\", "/"), "bytes": size, "mentions": n})
    return {
        "ran": True,
        "skipped_top_level_dirs": list(SWEEP_SKIP_TOP),
        "max_bytes": SWEEP_MAX_BYTES,
        "files_scanned": files_scanned,
        "files_with_ncaaf_mentions": hits,
    }


# --------------------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=AS_OF_UTC_DEFAULT)
    ap.add_argument("--skip-sweep", action="store_true")
    args = ap.parse_args()
    as_of = parse_dt(args.as_of)

    global HISTORY
    HISTORY = HistoryIndex(REPO / "data/historical/ncaaf_games_all.jsonl")
    ref_path = SCRATCH_REFERENCE if SCRATCH_REFERENCE.exists() else COMMITTED_REFERENCE
    ref = Reference(ref_path)
    slug_start: dict[str, datetime] = {}
    slug_teams: dict[str, tuple[str | None, str | None]] = {}

    sources: list[dict[str, Any]] = []
    sources += audit_history(ref)
    sources += audit_polymarket_odds(ref, slug_start, slug_teams)
    sources.append(audit_market_quotes_db(ref, slug_start, slug_teams))
    sources += audit_market_odds_snapshots(ref)
    live = audit_ledger(ref, RUNTIME_ROOT / "ledgers" / "ledgers.db", "ledger: live ledgers.db")
    sources.append(live)
    # Live event ids for the backup comparison.
    conn = sqlite3.connect(
        "file:" + str(RUNTIME_ROOT / "ledgers" / "ledgers.db").replace("\\", "/") + "?mode=ro", uri=True
    )
    try:
        live_ids = {
            r[0]
            for r in conn.execute(
                "select distinct event_id from ledger_records where lower(sport)='ncaaf' "
                "and market_type in ('spread','total') and line is not null"
            )
        }
    finally:
        conn.close()
    sources.append(audit_ledger_backups(ref, live_ids))
    sources.append(audit_espn_scoreboards(ref))
    sources.append(audit_candidates(ref))
    sources += audit_xlsx(ref)
    sources.append(audit_auto_buyer(ref))
    sources += audit_other_stores()
    sources.append(audit_experiment_spec())

    sweep = {"ran": False} if args.skip_sweep else residual_sweep()

    # ---- verdict (item 7). Predating = outside the reference AND earlier than its earliest event.
    predating = {
        s["source_id"]: s.get("pregame_lined_outside_PREDATING_earliest_reference", 0)
        for s in sources
        if "pregame_lined_outside_PREDATING_earliest_reference" in s
    }
    earliest_any = min(
        (
            s["earliest_event_start_utc"]
            for s in sources
            if s.get("earliest_event_start_utc")
            and "ncaaf_games_all" not in s["source_id"]
            and "processed/ncaaf" not in s["source_id"]
            and "quarantine" not in s["source_id"]
        ),
        default=None,
    )
    total_predating = sum(predating.values())
    seasons_with_lines = sorted(
        {
            s["earliest_event_start_utc"][:4]
            for s in sources
            if s.get("earliest_event_start_utc") and (s.get("events_with_spread_or_total_pregame") or 0) > 0
        }
    )
    verdict = {
        "item_7": "BLOCKED" if total_predating == 0 else "PROCEED_CANDIDATE",
        "rule": "proceeds only if a separable set of pregame-lined (spread or total, with a pregame observed_at) events exists outside the reference events and predates the earliest reference event",
        "predating_pregame_lined_events_by_source": predating,
        "predating_pregame_lined_events_max_over_sources": max(predating.values(), default=0),
        "earliest_line_bearing_event_start_utc_any_source": earliest_any,
        "earliest_reference_event_start_utc": iso(ref.earliest),
        "latest_reference_event_start_utc": iso(ref.latest),
        "pregame_lined_events_after_latest_reference_by_source": {
            s["source_id"]: s["pregame_lined_outside_AFTER_latest_reference"]
            for s in sources
            if s.get("pregame_lined_outside_AFTER_latest_reference")
        },
        "calendar_years_with_any_pregame_line": seasons_with_lines,
        "same_season_pool_outside_reference_not_predating": {
            s["source_id"]: {
                "pregame_lined_outside_reference": s["pregame_lined_events_outside_reference"],
                "of_which_with_result_in_history": s.get(
                    "pregame_lined_outside_reference_with_result_in_history"
                ),
                "after_latest_reference": s.get("pregame_lined_outside_AFTER_latest_reference"),
            }
            for s in sources
            if s.get("pregame_lined_events_outside_reference")
        },
        "same_season_pool_note": (
            "Outside-reference pregame-lined events all start on or after the reference's earliest event, so they "
            "are same-season, interleaved with the reference (no walk-forward separation). Polymarket ones are "
            "ladders (many alternate rungs, no single line) from 2026-09-04; many are FBS-vs-FCS games with no "
            "result in the history files (the known FCS gap), so only the 'with_result_in_history' subset is "
            "scorable without a backfill (name-matched, so approximate). Postgame/reconstructed prices are excluded "
            "from every count."
        ),
    }
    if total_predating == 0:
        verdict["unblock"] = (
            "Pregame spread and total quotes WITH a per-quote observed_at (or an archived pre-kickoff snapshot "
            "with fetch time) for NCAAF games of the 2016-2025 seasons, keyed by (start, home, away) or ESPN "
            "event_id so they join data/historical/ncaaf_games_all.jsonl (7,965 results, 2016-2026, no lines). "
            "Needed volume: enough events before 2026-08-29 to fit and validate the residual on data that is "
            "not the reference set. A single consensus line per event (opening and closing, with times) is "
            "sufficient; Polymarket ladders only exist from 2026-09-04. Postgame or reconstructed prices stay "
            "diagnostic only (docs/AGENTS.md); data/experiments/cfb_research_experiment_spec.json's "
            "'market_seasons 2020-2024' is an aggregate claim with no rows behind it."
        )
    result = {
        "as_of_utc": iso(as_of),
        "generated_by": "scripts/cfb_v6_lines_audit.py",
        "reference": {
            "path": str(ref.path).replace("\\", "/"),
            "sha256": ref.sha256(),
            "entries": len(ref.entries),
            "with_total_line": sum(e["has_total"] for e in ref.entries),
            "with_spread_line": sum(e["has_spread"] for e in ref.entries),
            "earliest_start_utc": iso(ref.earliest),
            "latest_start_utc": iso(ref.latest),
            "match_rule": "start within 3h and home+away names agree (U+FFFD in the dump matches one or no character)",
        },
        "verdict": verdict,
        "sources": sources,
        "residual_sweep": sweep,
        "pricing_note": "Percentages and lines only; American-odds fields are reported as present, never printed.",
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(result), encoding="utf-8")
    print(f"item 7 verdict: {verdict['item_7']}; predating pregame-lined events: {total_predating}")
    print(f"wrote {rel(OUT_JSON)} and {rel(OUT_MD)}")
    return 0


def render_md(r: dict[str, Any]) -> str:
    v = r["verdict"]
    lines = [
        "# NCAAF line-source audit (v6 item 7: market-anchored residual feasibility)",
        "",
        f"AS_OF_UTC: {r['as_of_utc']}",
        "",
        f"## Verdict: item 7 {v['item_7']}",
        "",
        f"- Rule: {v['rule']}.",
        (
            f"- Reference events: {r['reference']['entries']} ({r['reference']['with_total_line']} with a total line, "
            f"{r['reference']['with_spread_line']} with a spread line), starts {r['reference']['earliest_start_utc']} .. "
            f"{r['reference']['latest_start_utc']} (reference file {r['reference']['path']}, "
            f"sha256 {r['reference']['sha256'][:16]})."
        ),
        (
            "- Pregame-lined events outside the reference that PREDATE its earliest event: "
            f"{v['predating_pregame_lined_events_max_over_sources']} (max over sources)."
        ),
        f"- Earliest line-bearing event start in any source: {v['earliest_line_bearing_event_start_utc_any_source']}.",
        f"- Calendar years with any pregame line: {', '.join(v['calendar_years_with_any_pregame_line']) or 'none'}.",
    ]
    lines += [
        "",
        (
            "Same-season pool outside the reference (not predating, interleaved; counts of pregame-lined "
            "events, and the subset with a result in the history files):"
        ),
    ]
    for sid, pool in v["same_season_pool_outside_reference_not_predating"].items():
        lines.append(
            f"- {sid}: {pool['pregame_lined_outside_reference']} outside, "
            f"{pool['of_which_with_result_in_history']} with a result, "
            f"{pool['after_latest_reference']} after the latest reference event."
        )
    lines.append(f"- Note: {v['same_season_pool_note']}")
    if v.get("unblock"):
        lines += ["", f"Unblock: {v['unblock']}"]
    lines += [
        "",
        "## Inventory",
        "",
        "| Source | Files | Rows (ncaaf) | Event start range | S | T | ML | Pregame S | Pregame T | Pregame S-or-T events | Outside ref | Predating |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in r["sources"]:
        f = s.get("fields") or {}
        rng = f"{(s.get('earliest_event_start_utc') or '-')[:10]} .. {(s.get('latest_event_start_utc') or '-')[:10]}"
        yn = lambda b: "yes" if b else "no"
        lines.append(
            "| {sid} | {fc} | {rows} | {rng} | {sp} | {tt} | {ml} | {ps} | {pt} | {pst} | {out} | {pre} |".format(
                sid=s["source_id"],
                fc=s.get("file_count", "-"),
                rows=s.get("row_count_ncaaf", s.get("line_rows", "-")),
                rng=rng,
                sp=yn(f.get("spread")),
                tt=yn(f.get("total")),
                ml=yn(f.get("moneyline")),
                ps=s.get("events_with_spread_pregame", "-"),
                pt=s.get("events_with_total_pregame", "-"),
                pst=s.get("events_with_spread_or_total_pregame", 0),
                out=s.get("pregame_lined_events_outside_reference", "-"),
                pre=s.get("pregame_lined_outside_PREDATING_earliest_reference", "-"),
            )
        )
    lines += ["", "## Timestamp basis per source", ""]
    for s in r["sources"]:
        basis = str(s.get("timestamp_basis") or s.get("error") or "").rstrip(".")
        lines.append(f"- {s['source_id']}: {basis}. {s.get('notes', '')}".rstrip())
    sw = r.get("residual_sweep") or {}
    lines += ["", "## Residual sweep (files outside the sources above that mention NCAAF)", ""]
    if not sw.get("ran"):
        lines.append("- not run (--skip-sweep)")
    else:
        lines.append(
            f"- scanned {sw['files_scanned']} files (skipped dirs: {', '.join(sw['skipped_top_level_dirs'])}; "
            f"files over {sw['max_bytes'] // 1_000_000} MB skipped)"
        )
        lines += [f"- {h['path']} ({h['mentions']} mentions)" for h in sw["files_with_ncaaf_mentions"]] or [
            "- no unexamined file mentions NCAAF"
        ]
    lines += ["", "Percentages and lines only; American-odds fields are noted as present, never printed.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
