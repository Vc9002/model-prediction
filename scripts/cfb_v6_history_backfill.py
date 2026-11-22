"""NCAAF history gap audit and read-only ESPN groups=80/81 backfill builder (cfb-structural-v6, item 6).

Why this exists: ``ESPNClient.scoreboard`` sends no ``groups`` parameter for NCAAF, so the default
feed is a featured-games subset and the live history files miss many games (mostly FCS-involved
ones). This script re-fetches every 2016-2026 regular and post season week from the ESPN
college-football scoreboard with ``groups=80`` (FBS-involved games) and ``groups=81`` (FCS-involved
games), compares the completed events with the live history by ``event_id`` and writes a v6-only
backfill file under ``data/research/ncaaf_v6/``.

Division reading of the two feeds (verified live on 2026 week 4 before building this): an event in
only groups=80 is FBS-FBS, an event in only groups=81 is FCS-FCS (the label includes games against
lower-division opponents), and an event in both is FBS-FCS.

Safety: network use is read-only GET against the public scoreboard (never any order, execute or
sell endpoint); the live history files under data/historical and data/processed are opened read
only and every output path under them is refused; nothing under src/, config/ or tests/ is touched.

Subcommands: ``audit`` (fetch, compare, write the audit JSON/markdown), ``build`` (fetch, write the
backfill jsonl, record its sha256 and row count in the audit JSON) and ``all`` (one fetch feeding
both, so the audit and the backfill describe the same event set).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from model_prediction.data_sources.cfb_data import resolve_team
from model_prediction.data_sources.espn import ESPNClient
from model_prediction.domain import parse_utc

AS_OF_UTC = "2026-11-15T02:10:00Z"  # pinned by the task; recorded in every output
PARSER_VERSION = "cfb-v6-backfill-1"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
USER_AGENT = "Mozilla/5.0"  # generic on purpose: no project or personal identifiers
FIRST_SEASON, LAST_SEASON = 2016, 2026
# ESPN regular season runs to week 15 (Army-Navy week sits there); the margin catches a longer
# calendar. Postseason is a single week in every season probed, the extra weeks return empty.
REGULAR_WEEKS = range(1, 18)
POST_WEEKS = range(1, 4)
GROUPS = (80, 81)  # 80 = FBS-involved, 81 = FCS-involved
MIN_INTERVAL_S = 0.6  # >= 0.5 s between requests, with margin
MAX_ATTEMPTS = 6
EVENT_LIMIT = 500
EASTERN = ZoneInfo("America/New_York")

HISTORY_PATHS = (  # same precedence as models/college_football.py::_load_cfb_history
    REPO_ROOT / "data" / "processed" / "ncaaf" / "games.jsonl",
    REPO_ROOT / "data" / "historical" / "ncaaf_games_all.jsonl",
)
FORBIDDEN_OUTPUT_DIRS = (
    REPO_ROOT / "data" / "historical",
    REPO_ROOT / "data" / "processed",
)
DEFAULT_BACKFILL = REPO_ROOT / "data" / "research" / "ncaaf_v6" / "ncaaf_games_backfill.jsonl"
DEFAULT_AUDIT_JSON = REPO_ROOT / "outputs" / "ncaaf" / "cfb_v6_history_audit.json"
DEFAULT_AUDIT_MD = REPO_ROOT / "outputs" / "ncaaf" / "cfb_v6_history_audit.md"

CLASSES = ("FBS-FBS", "FBS-FCS", "FCS-FCS")
MAX_EXAMPLES = 40


# --------------------------------------------------------------------------- safety helpers


def _is_under(path: Path, directory: Path) -> bool:
    resolved, root = path.resolve(), directory.resolve()
    return resolved == root or root in resolved.parents


def guard_output_path(path: Path) -> Path:
    """Refuse any output path under the live history trees (they are read-only inputs)."""
    for forbidden in FORBIDDEN_OUTPUT_DIRS:
        if _is_under(path, forbidden):
            raise SystemExit(f"refusing to write {path}: under live history tree {forbidden}")
    return path


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(name: str) -> str:
    return " ".join(str(name).casefold().replace(".", "").replace("'", "").replace("-", " ").split())


# --------------------------------------------------------------------------- fetching


class PacedFetcher:
    """Read-only GET client: paced, with backoff on 429/5xx and transport errors."""

    def __init__(self, min_interval_s: float = MIN_INTERVAL_S) -> None:
        self.client = httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
        self.min_interval_s = max(min_interval_s, 0.5)
        self._last = 0.0
        self.requests = 0
        self.retries = 0
        self.max_events_seen = 0

    def get(self, params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, str]:
        """Return (payload, error, fetched_at_utc). Exactly one of payload/error is set."""
        error = "unknown"
        for attempt in range(MAX_ATTEMPTS):
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            retry_after = 0.0
            try:
                response = self.client.get(SCOREBOARD_URL, params=params)
            except httpx.TransportError as exc:
                error = f"transport:{type(exc).__name__}"
            else:
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError:
                        return None, "invalid_json", utc_now_iso()
                    if not isinstance(payload, dict):
                        return None, "payload_not_object", utc_now_iso()
                    return payload, None, utc_now_iso()
                error = f"http_{response.status_code}"
                if response.status_code != 429 and response.status_code < 500:
                    return None, error, utc_now_iso()
                try:
                    retry_after = float(response.headers.get("Retry-After", "0"))
                except ValueError:
                    retry_after = 0.0
            self.retries += 1
            time.sleep(min(max(1.5 * 2**attempt, retry_after), 60.0))
        return None, f"{error}_after_{MAX_ATTEMPTS}_attempts", utc_now_iso()


def _raw_score_ok(value: Any) -> bool:
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number >= 0 and number == int(number)


def normalize_event(
    event: dict[str, Any], *, feed: int, season: int, season_type_id: int, fetched_at: str
) -> tuple[dict[str, Any] | None, str]:
    """Return (row, reason). row is None for a non-completed or rejected event; reason says why."""
    competition = (event.get("competitions") or [{}])[0]
    status_type = (competition.get("status") or {}).get("type") or {}
    if not status_type.get("completed"):
        return None, "not_completed"
    competitors = competition.get("competitors") or []
    by_side = {item.get("homeAway"): item for item in competitors}
    away, home = by_side.get("away"), by_side.get("home")
    if not away or not home:
        return None, "missing_competitor"
    if not (_raw_score_ok(away.get("score")) and _raw_score_ok(home.get("score"))):
        return None, "bad_score"
    try:
        games = ESPNClient.completed_games({"events": [event]})
    except (TypeError, ValueError, KeyError, AttributeError):
        return None, "normalize_error"
    if len(games) != 1:
        return None, "normalize_dropped"
    game = games[0]
    event_id = game["event_id"]
    if not event_id.isdigit():
        return None, "bad_event_id"
    try:
        parse_utc(str(game["event_start_utc"]))
    except (TypeError, ValueError):
        return None, "bad_timestamp"
    if not game["away_team"] or not game["home_team"]:
        return None, "missing_team_name"
    if not (game["away_team_id"].isdigit() and game["home_team_id"].isdigit()):
        return None, "bad_team_id"
    if game["away_team_id"] == game["home_team_id"]:
        return None, "same_team"
    ev_season = event.get("season") or {}
    if ev_season.get("year") != season or ev_season.get("type") != season_type_id:
        return None, "season_mismatch"
    week_number = (event.get("week") or {}).get("number")
    if not isinstance(week_number, int):
        return None, "bad_week"
    neutral = competition.get("neutralSite")
    core = {
        "id": event_id,
        "date": game["event_start_utc"],
        "neutral": neutral,
        "away": [game["away_team_id"], game["away_score"]],
        "home": [game["home_team_id"], game["home_score"]],
    }
    row = {
        "event_id": event_id,
        "event_start_utc": game["event_start_utc"],  # full UTC timestamp, never reduced to a date
        "league": "NCAAF",
        "away_team": game["away_team"],
        "home_team": game["home_team"],
        "away_team_id": game["away_team_id"],
        "home_team_id": game["home_team_id"],
        "away_score": game["away_score"],
        "home_score": game["home_score"],
        "status": "completed",
        "status_name": str(status_type.get("name", "unknown")),
        "season_type": game["season_type"],
        "espn_season_type_id": season_type_id,
        "season_year": season,
        "week": week_number,
        "neutral_site": bool(neutral) if isinstance(neutral, bool) else None,
        "competition_type": game["competition_type"],
        "raw_source": f"espn:college-football:scoreboard:season={season}:type={season_type_id}",
        "raw_hash": hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest(),
        "parser_version": PARSER_VERSION,
        "fetched_at_utc": fetched_at,
        "_feeds": [feed],
    }
    return row, "ok"


def fetch_all(
    fetcher: PacedFetcher,
    seasons: list[int],
    as_of: datetime,
    log: Any,
) -> dict[str, Any]:
    events: dict[str, dict[str, Any]] = {}
    coverage: dict[int, dict[str, Any]] = {}
    reject: Counter[str] = Counter()
    status_names: Counter[str] = Counter()
    duplicates_across_weeks = 0
    after_as_of = 0
    failures: list[dict[str, Any]] = []
    limit_saturated: list[dict[str, Any]] = []
    for season in seasons:
        cov: dict[str, Any] = {
            "requests": 0,
            "failed_requests": 0,
            "feed_event_counts": {"80": 0, "81": 0},
            "weeks_with_events": [],
        }
        for season_type_id, weeks in ((2, REGULAR_WEEKS), (3, POST_WEEKS)):
            for week in weeks:
                week_total = 0
                for group in GROUPS:
                    params = {
                        "dates": str(season),
                        "seasontype": season_type_id,
                        "week": week,
                        "groups": group,
                        "limit": EVENT_LIMIT,
                    }
                    payload, error, fetched_at = fetcher.get(params)
                    cov["requests"] += 1
                    if payload is None:
                        cov["failed_requests"] += 1
                        failures.append({**params, "error": error})
                        log(f"  FAIL season={season} st={season_type_id} wk={week} g={group}: {error}")
                        continue
                    raw_events = payload.get("events") or []
                    if not isinstance(raw_events, list):
                        cov["failed_requests"] += 1
                        failures.append({**params, "error": "events_not_list"})
                        continue
                    fetcher.max_events_seen = max(fetcher.max_events_seen, len(raw_events))
                    if len(raw_events) >= EVENT_LIMIT:
                        limit_saturated.append(params)
                    week_total += len(raw_events)
                    for raw in raw_events:
                        if not isinstance(raw, dict):
                            reject["event_not_object"] += 1
                            continue
                        row, reason = normalize_event(
                            raw,
                            feed=group,
                            season=season,
                            season_type_id=season_type_id,
                            fetched_at=fetched_at,
                        )
                        if row is None:
                            reject[reason] += 1
                            continue
                        if parse_utc(row["event_start_utc"]) > as_of:
                            after_as_of += 1
                            continue
                        existing = events.get(row["event_id"])
                        if existing is None:
                            events[row["event_id"]] = row
                            status_names[row["status_name"]] += 1
                            cov["feed_event_counts"][str(group)] += 1
                        else:
                            if group not in existing["_feeds"]:
                                existing["_feeds"].append(group)
                                cov["feed_event_counts"][str(group)] += 1
                            else:
                                duplicates_across_weeks += 1
                if week_total:
                    cov["weeks_with_events"].append(f"st{season_type_id}-wk{week}")
        coverage[season] = cov
        log(
            f"season {season}: events so far {len(events)}, requests {cov['requests']}, "
            f"failed {cov['failed_requests']}"
        )
    for row in events.values():
        row["_feeds"].sort()
    return {
        "events": events,
        "coverage": coverage,
        "reject": dict(reject),
        "status_names": dict(status_names),
        "duplicates_across_weeks": duplicates_across_weeks,
        "events_started_after_as_of": after_as_of,
        "failures": failures,
        "limit_saturated": limit_saturated,
        "requests": fetcher.requests,
        "retries": fetcher.retries,
    }


# --------------------------------------------------------------------------- history


def load_history() -> dict[str, Any]:
    """Same precedence and parse rules as _load_cfb_history, plus the raw neutral/week/season."""
    rows: dict[str, dict[str, Any]] = {}
    file_meta: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for path in HISTORY_PATHS:
        meta: dict[str, Any] = {"path": str(path.relative_to(REPO_ROOT)).replace("\\", "/")}
        if not path.exists():
            meta["exists"] = False
            file_meta.append(meta)
            continue
        meta["exists"] = True
        meta["sha256"] = sha256_file(path)
        meta["rows"] = 0
        meta["new_events"] = 0
        with path.open("r", encoding="utf-8") as handle:  # read-only
            for line in handle:
                if not line.strip():
                    continue
                meta["rows"] += 1
                try:
                    g = json.loads(line)
                    eid = str(g.get("event_id"))
                    if eid in rows:
                        continue
                    start_str = g.get("event_start_utc") or g.get("game_start_utc") or ""
                    start_dt = parse_utc(start_str)
                    away_score = int(
                        g.get("away_score")
                        if g.get("away_score") is not None
                        else (g.get("away") or {}).get("score", 0)
                    )
                    home_score = int(
                        g.get("home_score")
                        if g.get("home_score") is not None
                        else (g.get("home") or {}).get("score", 0)
                    )
                    away_team = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
                    home_team = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""
                    if not away_team or not home_team:
                        rejected["missing_team_name"] += 1
                        continue
                except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                    rejected["parse_error"] += 1
                    continue
                season_year = g.get("season_year")
                if not isinstance(season_year, int):
                    season_year = start_dt.year - 1 if start_dt.month <= 2 else start_dt.year
                rows[eid] = {
                    "event_id": eid,
                    "event_start_utc": start_str,
                    "start": start_dt,
                    "away_team": away_team,
                    "home_team": home_team,
                    "away_score": away_score,
                    "home_score": home_score,
                    "season_year": season_year,
                    "is_neutral_site": g.get("is_neutral_site"),
                    "source": meta["path"],
                }
                meta["new_events"] += 1
        file_meta.append(meta)
    return {"rows": rows, "files": file_meta, "rejected": dict(rejected)}


# --------------------------------------------------------------------------- classification


def feed_class(feeds: list[int]) -> str:
    if feeds == [80]:
        return "FBS-FBS"
    if feeds == [81]:
        return "FCS-FCS"
    return "FBS-FCS"


def build_divisions(
    events: dict[str, dict[str, Any]],
) -> tuple[dict[int, dict[str, str]], dict[int, dict[str, str]]]:
    """Per season: team id -> 'FBS'/'lower', and normalized name -> 'FBS'/'lower'.

    FBS means the team plays in at least one groups=80-only event that season (both teams FBS).
    Everything else is 'lower' (FCS plus the lower-division opponents ESPN files under groups=81).
    """
    by_id: dict[int, dict[str, str]] = defaultdict(dict)
    by_name: dict[int, dict[str, str]] = defaultdict(dict)
    for row in events.values():
        if row["_feeds"] != [80]:
            continue
        season = row["season_year"]
        for side in ("away", "home"):
            by_id[season][row[f"{side}_team_id"]] = "FBS"
            by_name[season][_norm(row[f"{side}_team"])] = "FBS"
    for row in events.values():
        season = row["season_year"]
        for side in ("away", "home"):
            by_id[season].setdefault(row[f"{side}_team_id"], "lower")
            by_name[season].setdefault(_norm(row[f"{side}_team"]), "lower")
    return by_id, by_name


def assign_classes(events: dict[str, dict[str, Any]]) -> None:
    """Primary class = team-division label; the feed label is kept as a cross-check.

    The feeds are not perfectly clean: a 2023 FCS bowl (Celebration Bowl) sits in both feeds, so the
    feed label alone would call it FBS-FCS. A team is FBS when it plays a groups=80-only event.
    """
    by_id, _ = build_divisions(events)
    for row in events.values():
        row["feed_class"] = feed_class(row["_feeds"])
        row["game_class"] = (
            class_from_divisions(
                by_id[row["season_year"]].get(row["away_team_id"]),
                by_id[row["season_year"]].get(row["home_team_id"]),
            )
            or row["feed_class"]
        )
        row["is_forfeit"] = row["status_name"] == "STATUS_FORFEIT"


def class_from_divisions(away: str | None, home: str | None) -> str | None:
    if away is None or home is None:
        return None
    fbs = (away == "FBS") + (home == "FBS")
    return CLASSES[2 - fbs]  # 2 FBS -> FBS-FBS, 1 -> FBS-FCS, 0 -> FCS-FCS


# --------------------------------------------------------------------------- audit


def _empty_counts() -> dict[str, int]:
    return {**dict.fromkeys(CLASSES, 0), "unclassified": 0, "total": 0}


def run_audit(fetch: dict[str, Any], history: dict[str, Any], seasons: list[int]) -> dict[str, Any]:
    events = fetch["events"]
    hist = history["rows"]
    by_id, by_name = build_divisions(events)

    assign_classes(events)

    season_set = set(seasons)
    # classify history rows: ESPN feed label when the id is covered, else team-division lookup
    hist_class: dict[str, str] = {}
    hist_class_source: Counter[str] = Counter()
    for eid, h in hist.items():
        if h["season_year"] not in season_set:
            continue
        if eid in events:
            hist_class[eid] = events[eid]["game_class"]
            hist_class_source["espn_feed"] += 1
            continue
        names = by_name.get(h["season_year"], {})
        label = class_from_divisions(names.get(_norm(h["away_team"])), names.get(_norm(h["home_team"])))
        if label is None:  # fall back to the repo's own team table: it lists FBS plus some FCS programs
            tiers = []
            for team in (h["away_team"], h["home_team"]):
                resolved = resolve_team(team)
                tiers.append(None if resolved is None else ("lower" if resolved.tier == "FCS" else "FBS"))
            label = class_from_divisions(tiers[0], tiers[1])
            hist_class_source["team_table" if label else "unclassified"] += 1
        else:
            hist_class_source["season_team_division"] += 1
        hist_class[eid] = label or "unclassified"

    per_season: dict[int, dict[str, Any]] = {}
    for season in seasons:
        h_counts, e_counts = _empty_counts(), _empty_counts()
        for eid, h in hist.items():
            if h["season_year"] == season:
                h_counts[hist_class.get(eid, "unclassified")] += 1
                h_counts["total"] += 1
        for row in events.values():
            if row["season_year"] == season:
                e_counts[row["game_class"]] += 1
                e_counts["total"] += 1
        espn_only = _empty_counts()
        hist_only = _empty_counts()
        for eid, row in events.items():
            if row["season_year"] == season and eid not in hist:
                espn_only[row["game_class"]] += 1
                espn_only["total"] += 1
        for eid, h in hist.items():
            if h["season_year"] == season and eid not in events:
                hist_only[hist_class.get(eid, "unclassified")] += 1
                hist_only["total"] += 1
        per_season[season] = {
            "history": h_counts,
            "espn": e_counts,
            "espn_not_in_history": espn_only,
            "history_not_in_espn": hist_only,
            "coverage": fetch["coverage"].get(season, {}),
        }

    # id-vs-key cross check: is an ESPN-only event really absent, or present under another id?
    hist_keys = {
        (h["event_start_utc"], _norm(h["home_team"]), _norm(h["away_team"])): eid for eid, h in hist.items()
    }
    hist_start_keys = {
        (h["start"].isoformat(), _norm(h["home_team"]), _norm(h["away_team"])): eid for eid, h in hist.items()
    }
    missing_ids = [eid for eid in events if eid not in hist and events[eid]["season_year"] in season_set]
    present_under_other_id = []
    for eid in missing_ids:
        row = events[eid]
        key = (row["event_start_utc"], _norm(row["home_team"]), _norm(row["away_team"]))
        key2 = (parse_utc(row["event_start_utc"]).isoformat(), key[1], key[2])
        if key in hist_keys or key2 in hist_start_keys:
            present_under_other_id.append(
                {"espn_event_id": eid, "history_event_id": hist_keys.get(key) or hist_start_keys.get(key2)}
            )

    # overlap comparison
    score_disagreements: list[dict[str, Any]] = []
    orientation_swaps = 0
    start_disagreements = 0
    neutral_compared = 0
    neutral_disagreements = 0
    neutral_history_missing = 0
    team_name_mismatch = 0
    name_examples: list[dict[str, Any]] = []
    overlap = 0
    for eid, row in events.items():
        h = hist.get(eid)
        if h is None:
            continue
        overlap += 1
        if _norm(h["home_team"]) != _norm(row["home_team"]) or _norm(h["away_team"]) != _norm(
            row["away_team"]
        ):
            if _norm(h["home_team"]) == _norm(row["away_team"]) and _norm(h["away_team"]) == _norm(
                row["home_team"]
            ):
                orientation_swaps += 1
            else:
                team_name_mismatch += 1
                if len(name_examples) < 10:
                    name_examples.append(
                        {
                            "event_id": eid,
                            "history": [h["away_team"], h["home_team"]],
                            "espn": [row["away_team"], row["home_team"]],
                        }
                    )
        if h["start"] != parse_utc(row["event_start_utc"]):
            start_disagreements += 1
        if (h["away_score"], h["home_score"]) != (row["away_score"], row["home_score"]):
            score_disagreements.append(
                {
                    "event_id": eid,
                    "season": row["season_year"],
                    "start": row["event_start_utc"],
                    "away": row["away_team"],
                    "home": row["home_team"],
                    "history_away_home": [h["away_score"], h["home_score"]],
                    "espn_away_home": [row["away_score"], row["home_score"]],
                    "history_source": h["source"],
                }
            )
        hn = h["is_neutral_site"]
        if isinstance(hn, bool) and isinstance(row["neutral_site"], bool):
            neutral_compared += 1
            if hn != row["neutral_site"]:
                neutral_disagreements += 1
        elif not isinstance(hn, bool):
            neutral_history_missing += 1

    # feed label vs team-division label (sanity check of the division reading)
    label_mismatch = 0
    label_examples: list[dict[str, Any]] = []
    team_table_conflicts = []
    for row in events.values():
        season = row["season_year"]
        if row["feed_class"] != row["game_class"]:
            label_mismatch += 1
            if len(label_examples) < 10:
                label_examples.append(
                    {
                        "event_id": row["event_id"],
                        "start": row["event_start_utc"],
                        "teams": [row["away_team"], row["home_team"]],
                        "feeds": row["_feeds"],
                        "feed_class": row["feed_class"],
                        "division_class": row["game_class"],
                    }
                )
        # cross-check against the repo's own table: a FCS-tier row there must not be FBS here
        for side in ("away", "home"):
            resolved = resolve_team(row[f"{side}_team"])
            if resolved is None:
                continue
            division = by_id[season].get(row[f"{side}_team_id"])
            if resolved.tier == "FCS" and division == "FBS":
                team_table_conflicts.append(
                    {"season": season, "team": row[f"{side}_team"], "table_tier": "FCS", "espn": "FBS"}
                )
    team_table_conflict_teams = sorted({(c["season"], c["team"]) for c in team_table_conflicts})

    # lower-division proxy: groups=81-involved events with a team appearing only once in that season's feed
    appearances: dict[int, Counter[str]] = defaultdict(Counter)
    for row in events.values():
        if 81 in row["_feeds"]:
            for side in ("away", "home"):
                appearances[row["season_year"]][row[f"{side}_team_id"]] += 1
    singleton_teams: dict[int, int] = {}
    for season, counter in appearances.items():
        singleton_teams[season] = sum(
            1 for team, count in counter.items() if count == 1 and by_id[season].get(team) == "lower"
        )

    audit_2026 = audit_season_2026(events, hist, by_id, hist_class)

    return {
        "per_season": per_season,
        "history_class_source": dict(hist_class_source),
        "overlap": {
            "events_in_both": overlap,
            "score_disagreement_count": len(score_disagreements),
            "score_disagreements": score_disagreements[:MAX_EXAMPLES],
            "score_disagreements_truncated": max(len(score_disagreements) - MAX_EXAMPLES, 0),
            "orientation_swaps": orientation_swaps,
            "team_name_mismatch_same_id": team_name_mismatch,
            "team_name_mismatch_examples": name_examples,
            "start_timestamp_disagreements": start_disagreements,
            "neutral_flag_compared": neutral_compared,
            "neutral_flag_disagreements": neutral_disagreements,
            "history_rows_without_neutral_flag": neutral_history_missing,
        },
        "id_cross_check": {
            "espn_events_missing_by_id": len(missing_ids),
            "of_which_present_under_another_id": len(present_under_other_id),
            "examples": present_under_other_id[:10],
        },
        "division_reading_check": {
            "feed_label_vs_team_division_label_mismatches": label_mismatch,
            "feed_label_mismatch_examples": label_examples,
            "team_table_fcs_but_espn_fbs_conflicts": len(team_table_conflict_teams),
            "team_table_conflict_examples": [list(x) for x in team_table_conflict_teams[:10]],
            "singleton_lower_division_teams_per_season": {
                str(k): v for k, v in sorted(singleton_teams.items())
            },
        },
        "season_2026": audit_2026,
    }


def _eastern_date(start_utc: str) -> str:
    return parse_utc(start_utc).astimezone(EASTERN).date().isoformat()


def audit_season_2026(
    events: dict[str, dict[str, Any]],
    hist: dict[str, dict[str, Any]],
    by_id: dict[int, dict[str, str]],
    hist_class: dict[str, str],
) -> dict[str, Any]:
    season = LAST_SEASON
    games = [r for r in events.values() if r["season_year"] == season]
    missing = [r for r in games if r["event_id"] not in hist]
    divisions = by_id.get(season, {})

    teams: dict[str, dict[str, Any]] = {}
    for row in games:
        for side in ("away", "home"):
            tid = row[f"{side}_team_id"]
            entry = teams.setdefault(
                tid,
                {
                    "team_id": tid,
                    "team": row[f"{side}_team"],
                    "division": divisions.get(tid, "lower"),
                    "espn_games": 0,
                    "history_games": 0,
                    "missing_games": 0,
                },
            )
            entry["espn_games"] += 1
            if row["event_id"] in hist:
                entry["history_games"] += 1
            else:
                entry["missing_games"] += 1

    def dist(entries: list[dict[str, Any]]) -> dict[str, Any]:
        miss = [e["missing_games"] for e in entries]
        if not miss:
            return {"teams": 0}
        return {
            "teams": len(miss),
            "mean_missing_per_team": round(statistics.fmean(miss), 3),
            "median_missing_per_team": statistics.median(miss),
            "max_missing_per_team": max(miss),
            "teams_with_zero_missing": sum(1 for m in miss if m == 0),
            "mean_espn_games_per_team": round(statistics.fmean(e["espn_games"] for e in entries), 3),
            "mean_history_games_per_team": round(statistics.fmean(e["history_games"] for e in entries), 3),
            "missing_histogram": dict(sorted(Counter(miss).items())),
        }

    fbs_entries = [e for e in teams.values() if e["division"] == "FBS"]
    low_entries = [e for e in teams.values() if e["division"] != "FBS"]

    def day_block(key_fn: Any) -> dict[str, Any]:
        out: dict[str, dict[str, Any]] = {}
        for row in games:
            key = key_fn(row["event_start_utc"])
            slot = out.setdefault(
                key, {"espn_games": 0, "missing": 0, "fbs_involved": 0, "fbs_involved_missing": 0}
            )
            slot["espn_games"] += 1
            absent = row["event_id"] not in hist
            slot["missing"] += absent
            if row["game_class"] != "FCS-FCS":
                slot["fbs_involved"] += 1
                slot["fbs_involved_missing"] += absent
        return out

    utc_days = day_block(lambda s: parse_utc(s).date().isoformat())
    et_days = day_block(_eastern_date)
    week_blocks: dict[str, dict[str, int]] = {}
    for row in games:
        key = f"st{row['espn_season_type_id']}-wk{row['week']}"
        slot = week_blocks.setdefault(
            key, {"espn_games": 0, "missing": 0, "fbs_involved": 0, "fbs_involved_missing": 0}
        )
        slot["espn_games"] += 1
        absent = row["event_id"] not in hist
        slot["missing"] += absent
        if row["game_class"] != "FCS-FCS":
            slot["fbs_involved"] += 1
            slot["fbs_involved_missing"] += absent

    by_class: dict[str, dict[str, int]] = {c: {"espn": 0, "missing": 0} for c in CLASSES}
    for row in games:
        by_class[row["game_class"]]["espn"] += 1
        by_class[row["game_class"]]["missing"] += row["event_id"] not in hist

    fbs_games = [r for r in games if r["game_class"] != "FCS-FCS"]
    week4_feeds = {"80": 0, "81": 0}
    for row in games:
        if row["espn_season_type_id"] == 2 and row["week"] == 4:
            for feed in row["_feeds"]:
                week4_feeds[str(feed)] += 1

    return {
        "espn_completed_games": len(games),
        "history_games": sum(1 for h in hist.values() if h["season_year"] == season),
        "missing_from_history": len(missing),
        "missing_by_class": by_class,
        "fbs_involved_games_espn": len(fbs_games),
        "fbs_involved_games_missing": sum(1 for r in fbs_games if r["event_id"] not in hist),
        "fbs_team_count": len(fbs_entries),
        "lower_division_team_count": len(low_entries),
        "fbs_teams_missing_distribution": dist(fbs_entries),
        "lower_division_teams_missing_distribution": dist(low_entries),
        "fbs_teams_with_most_missing": sorted(fbs_entries, key=lambda e: (-e["missing_games"], e["team"]))[
            :15
        ],
        "day_2026_09_26": {
            "by_utc_start_date": utc_days.get("2026-09-26"),
            "by_us_eastern_date": et_days.get("2026-09-26"),
        },
        "week4_completed_games_in_feed": week4_feeds,
        "by_week": dict(
            sorted(week_blocks.items(), key=lambda kv: (int(kv[0][2]), int(kv[0].split("wk")[1])))
        ),
        "by_us_eastern_day": dict(sorted(et_days.items())),
        "teams": sorted(teams.values(), key=lambda e: (e["division"] != "FBS", e["team"])),
    }


# --------------------------------------------------------------------------- build


def write_backfill(events: dict[str, dict[str, Any]], out_path: Path) -> dict[str, Any]:
    guard_output_path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    assign_classes(events)
    rows = sorted(events.values(), key=lambda r: (parse_utc(r["event_start_utc"]), r["event_id"]))
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            out = {k: v for k, v in row.items() if not k.startswith("_")}
            out["feed_groups"] = row["_feeds"]
            out["game_class"] = row["game_class"]
            out["feed_class"] = row["feed_class"]
            out["is_forfeit"] = row["is_forfeit"]
            out["raw_source"] = f"{row['raw_source']}:groups={'+'.join(str(g) for g in row['_feeds'])}"
            handle.write(json.dumps(out, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(tmp, out_path)
    return {
        "path": _display_path(out_path),
        "sha256": sha256_file(out_path),
        "rows": len(rows),
        "bytes": out_path.stat().st_size,
        "first_event_start_utc": rows[0]["event_start_utc"] if rows else None,
        "last_event_start_utc": rows[-1]["event_start_utc"] if rows else None,
    }


# --------------------------------------------------------------------------- rendering


def _row(season: int, block: dict[str, Any]) -> str:
    h, e = block["history"], block["espn"]
    eo, ho = block["espn_not_in_history"], block["history_not_in_espn"]
    cov = block["coverage"]
    return (
        f"| {season} | {h['FBS-FBS']} / {h['FBS-FCS']} / {h['FCS-FCS']} / {h['unclassified']} | {h['total']} "
        f"| {e['FBS-FBS']} / {e['FBS-FCS']} / {e['FCS-FCS']} | {e['total']} "
        f"| {eo['total']} ({eo['FBS-FBS']} / {eo['FBS-FCS']} / {eo['FCS-FCS']}) "
        f"| {ho['total']} | {cov.get('failed_requests', 0)}/{cov.get('requests', 0)} |"
    )


def render_markdown(report: dict[str, Any]) -> str:
    a = report["audit"]
    lines: list[str] = []
    add = lines.append
    add("# NCAAF history gap audit and ESPN groups=80/81 backfill (cfb-structural-v6, item 6)")
    add("")
    add(
        f"- AS_OF_UTC: `{report['as_of_utc']}`; fetch started `{report['fetch_started_utc']}`, "
        f"finished `{report['fetch_finished_utc']}`"
    )
    add(
        f"- Script: `scripts/cfb_v6_history_backfill.py` (parser `{PARSER_VERSION}`); read-only GET of the public "
        f"ESPN scoreboard, groups=80 and groups=81, limit={EVENT_LIMIT}, "
        f"{report['fetch']['requests']} requests, {report['fetch']['retries']} retries, "
        f"{len(report['fetch']['failures'])} failed after retries"
    )
    add(
        "- Division reading: an event only in groups=80 is FBS-FBS, only in groups=81 is FCS-FCS "
        "(includes lower-division opponents), in both is FBS-FCS."
    )
    add("")
    add("## Live history inputs (opened read-only)")
    add("")
    for meta in report["history_files"]:
        add(
            f"- `{meta['path']}`: rows {meta.get('rows')}, new events after dedupe {meta.get('new_events')}, "
            f"sha256 `{meta.get('sha256')}`"
        )
    add(
        f"- Loaded events (loader precedence, deduped by event_id): {report['history_loaded_events']}; "
        f"rejected rows: {report['history_rejected']}"
    )
    add("")
    add("## Backfill file")
    add("")
    bf = report.get("backfill")
    if bf:
        add(
            f"- `{bf['path']}` (gitignored): rows **{bf['rows']}**, sha256 `{bf['sha256']}`, "
            f"{bf['bytes']} bytes, first start `{bf['first_event_start_utc']}`, "
            f"last start `{bf['last_event_start_utc']}`"
        )
    else:
        add("- not built in this run (`audit` only)")
    add("")
    add("## Seasons covered")
    add("")
    cov = report["seasons_covered"]
    add(f"- Seasons with every request answered: {cov['complete']}")
    add(f"- Seasons with at least one failed request: {cov['partial']}")
    add(f"- Seasons with zero ESPN events: {cov['empty']}")
    add("")
    add("## Per season: existing history vs ESPN (FBS-FBS / FBS-FCS / FCS-FCS)")
    add("")
    add(
        "History column shows FBS-FBS / FBS-FCS / FCS-FCS / unclassified. The ESPN-only column is "
        "ESPN events whose event_id is not in the history, split by class."
    )
    add("")
    add(
        "| Season | History classes | History total | ESPN classes | ESPN total | ESPN not in history (by class) | History not in ESPN | Failed/total requests |"
    )
    add("|---|---|---|---|---|---|---|---|")
    for season, block in sorted(a["per_season"].items(), key=lambda kv: int(kv[0])):
        add(_row(int(season), block))
    tot_h = sum(b["history"]["total"] for b in a["per_season"].values())
    tot_e = sum(b["espn"]["total"] for b in a["per_season"].values())
    tot_eo = sum(b["espn_not_in_history"]["total"] for b in a["per_season"].values())
    tot_ho = sum(b["history_not_in_espn"]["total"] for b in a["per_season"].values())
    add(f"| all | | {tot_h} | | {tot_e} | {tot_eo} | {tot_ho} | |")
    add("")
    add(f"History class source: {a['history_class_source']}")
    add("")
    s26 = a["season_2026"]
    add("## 2026 missing games")
    add("")
    add(
        f"- ESPN completed 2026 games (start <= AS_OF): {s26['espn_completed_games']}; in history by "
        f"event_id: {s26['espn_completed_games'] - s26['missing_from_history']}; **missing: "
        f"{s26['missing_from_history']}** (history holds {s26['history_games']} 2026 events in total)"
    )
    add(f"- By class (espn / missing): {s26['missing_by_class']}")
    add(
        f"- FBS-involved games: {s26['fbs_involved_games_espn']} on ESPN, {s26['fbs_involved_games_missing']} missing"
    )
    add(f"- FBS teams: {s26['fbs_team_count']}; lower-division teams: {s26['lower_division_team_count']}")
    add("")
    add("Per-team missing counts (a missing game counts once for each of its two teams):")
    add("")
    add(
        "| Group | Teams | Mean missing | Median | Max | Zero-missing teams | Mean ESPN games | Mean history games |"
    )
    add("|---|---|---|---|---|---|---|---|")
    for label, key in (
        ("FBS teams", "fbs_teams_missing_distribution"),
        ("Lower-division teams", "lower_division_teams_missing_distribution"),
    ):
        d = s26[key]
        if d.get("teams"):
            add(
                f"| {label} | {d['teams']} | {d['mean_missing_per_team']} | {d['median_missing_per_team']} | "
                f"{d['max_missing_per_team']} | {d['teams_with_zero_missing']} | {d['mean_espn_games_per_team']} | "
                f"{d['mean_history_games_per_team']} |"
            )
    add("")
    add(
        f"FBS team missing-count histogram (missing games -> teams): {s26['fbs_teams_missing_distribution'].get('missing_histogram')}"
    )
    add("")
    add(
        "FBS teams with the most missing games: "
        + "; ".join(
            f"{e['team']} {e['missing_games']}/{e['espn_games']}" for e in s26["fbs_teams_with_most_missing"]
        )
    )
    add("")
    add("Day check for 2026-09-26 (the '16 of 65 games' claim):")
    add("")
    add(f"- By UTC start date: {s26['day_2026_09_26']['by_utc_start_date']}")
    add(f"- By US-Eastern date: {s26['day_2026_09_26']['by_us_eastern_date']}")
    add(f"- Week 4 completed games per feed (80 / 81): {s26['week4_completed_games_in_feed']}")
    add("")
    add("By ESPN week (st2 = regular season, st3 = postseason):")
    add("")
    add("| Week | ESPN games | Missing | FBS-involved | FBS-involved missing |")
    add("|---|---|---|---|---|")
    for key, slot in s26["by_week"].items():
        add(
            f"| {key} | {slot['espn_games']} | {slot['missing']} | {slot['fbs_involved']} | {slot['fbs_involved_missing']} |"
        )
    add("")
    ov = a["overlap"]
    add("## Overlapping events (same event_id in history and ESPN)")
    add("")
    add(
        f"- Events in both: {ov['events_in_both']}; score disagreements: **{ov['score_disagreement_count']}**; "
        f"home/away swapped: {ov['orientation_swaps']}; other team-name mismatches: {ov['team_name_mismatch_same_id']}; "
        f"start timestamp disagreements: {ov['start_timestamp_disagreements']}"
    )
    add(
        f"- Neutral flag: compared {ov['neutral_flag_compared']}, disagreements {ov['neutral_flag_disagreements']}, "
        f"history rows with no neutral flag {ov['history_rows_without_neutral_flag']}"
    )
    for item in ov["score_disagreements"]:
        add(
            f"  - {item['event_id']} {item['start']} {item['away']} at {item['home']}: history "
            f"{item['history_away_home']} vs ESPN {item['espn_away_home']} ({item['history_source']})"
        )
    if ov["score_disagreements_truncated"]:
        add(f"  - ... {ov['score_disagreements_truncated']} more in the JSON")
    add("")
    ic = a["id_cross_check"]
    add("## Cross-checks")
    add("")
    add(
        f"- ESPN events absent by event_id: {ic['espn_events_missing_by_id']}; of those present in history under "
        f"a different id (same start and teams): {ic['of_which_present_under_another_id']}"
    )
    dr = a["division_reading_check"]
    add(
        f"- Feed label vs team-division label mismatches: {dr['feed_label_vs_team_division_label_mismatches']} "
        f"{dr['feed_label_mismatch_examples']}; "
        f"repo team table lists FCS but ESPN feeds put the team in FBS: {dr['team_table_fcs_but_espn_fbs_conflicts']} "
        f"team-seasons {dr['team_table_conflict_examples']}"
    )
    add(
        f"- Lower-division teams seen in exactly one groups=81 event per season (proxy for non-FCS opponents): "
        f"{dr['singleton_lower_division_teams_per_season']}"
    )
    add("")
    add("## Fetch integrity")
    add("")
    fe = report["fetch"]
    add(f"- Rejected events by reason: {fe['reject']}")
    add(f"- Completed-event status names: {fe['status_names']}")
    add(
        f"- Events started after AS_OF dropped: {fe['events_started_after_as_of']}; duplicate (same id, same feed) "
        f"across weeks: {fe['duplicates_across_weeks']}; feed responses at the {EVENT_LIMIT}-event limit: "
        f"{len(fe['limit_saturated'])}; largest response: {report['max_events_in_one_response']} events"
    )
    add(f"- Failed requests after retries: {fe['failures'] if fe['failures'] else 'none'}")
    add("")
    add("## Regime note")
    add("")
    add(report["regime_note"])
    add("")
    return "\n".join(lines) + "\n"


def regime_note(audit: dict[str, Any], covered: dict[str, list[int]]) -> str:
    """State whether the history gap, hence the backfill's effect on ratings, is the same in every season."""
    shares: dict[int, dict[str, float]] = {}
    for season, block in audit["per_season"].items():
        shares[int(season)] = {
            cls: (block["espn_not_in_history"][cls] / block["espn"][cls] if block["espn"][cls] else 0.0)
            for cls in CLASSES
        }
    fit = [s for s in shares if s < LAST_SEASON]
    parts = []
    incomplete = sorted(covered["partial"] + covered["empty"])
    if incomplete:
        parts.append(f"Seasons not fully fetched: {incomplete}; the backfill is incomplete there.")
    else:
        parts.append(f"All seasons {FIRST_SEASON}-{LAST_SEASON} were fetched without a failed request.")
    for cls in CLASSES:
        fit_vals = [shares[s][cls] for s in fit]
        last = shares.get(LAST_SEASON, {}).get(cls)
        if fit_vals and last is not None:
            parts.append(
                f"{cls}: share of ESPN games absent from the history is {min(fit_vals):.1%} to "
                f"{max(fit_vals):.1%} over {min(fit)}-{max(fit)} and {last:.1%} in {LAST_SEASON}."
            )
    fcs_fit = [shares[s]["FCS-FCS"] for s in fit]
    fcs_last = shares.get(LAST_SEASON, {}).get("FCS-FCS")
    only_last = bool(fcs_fit) and fcs_last is not None and max(fcs_fit) < 0.1 and fcs_last > 0.1
    if only_last:
        parts.append(
            "Only 2026 gains FCS-FCS games, so the rating regime differs between the fit years and the "
            "2026 market check."
        )
    elif fcs_fit and fcs_last is not None:
        parts.append(
            "The history is missing FCS-involved games in every fit season as well as 2026, so the backfill "
            "changes the rating inputs in the fit years and in the market-check season alike; the regime is "
            "consistent across them."
        )
    thin = {
        s: audit["per_season"][s]["espn"]["FCS-FCS"]
        for s in audit["per_season"]
        if audit["per_season"][s]["espn"]["FCS-FCS"] < 100
    }
    if thin:
        parts.append(
            f"Seasons with under 100 ESPN FCS-FCS games in the requested season types: {thin}. "
            "The 2020 FCS season was played in spring 2021 and ESPN files it under season type 5, which "
            "this script does not request (probed once by hand: 238 completed games, 236 groups=81-only "
            "and 2 in both feeds, none of them in the backfill file), so 2020 FCS coverage is "
            "understated, not thin on ESPN."
        )
    return " ".join(parts)


# --------------------------------------------------------------------------- commands


def _as_of_dt(value: str) -> datetime:
    return parse_utc(value)


def run(args: argparse.Namespace) -> int:
    as_of = _as_of_dt(args.as_of)
    seasons = list(range(args.first_season, args.last_season + 1))
    audit_json = guard_output_path(Path(args.audit_json))
    audit_md = guard_output_path(Path(args.audit_md))
    backfill_path = guard_output_path(Path(args.backfill))
    do_audit = args.command in ("audit", "all")
    do_build = args.command in ("build", "all")

    def log(message: str) -> None:
        print(message, flush=True)

    started = utc_now_iso()
    fetcher = PacedFetcher()
    log(f"AS_OF_UTC={args.as_of} fetch start {started}; seasons {seasons[0]}-{seasons[-1]}")
    fetch = fetch_all(fetcher, seasons, as_of, log)
    finished = utc_now_iso()
    history = load_history()
    log(f"history events loaded: {len(history['rows'])}; ESPN completed events: {len(fetch['events'])}")

    covered = {"complete": [], "partial": [], "empty": []}
    for season in seasons:
        cov = fetch["coverage"][season]
        n_events = sum(1 for r in fetch["events"].values() if r["season_year"] == season)
        if n_events == 0:
            covered["empty"].append(season)
        elif cov["failed_requests"]:
            covered["partial"].append(season)
        else:
            covered["complete"].append(season)

    audit = run_audit(fetch, history, seasons)
    report: dict[str, Any] = {
        "as_of_utc": args.as_of,
        "fetch_started_utc": started,
        "fetch_finished_utc": finished,
        "script": "scripts/cfb_v6_history_backfill.py",
        "parser_version": PARSER_VERSION,
        "history_files": history["files"],
        "history_loaded_events": len(history["rows"]),
        "history_rejected": history["rejected"],
        "seasons_covered": covered,
        "fetch": {
            k: fetch[k]
            for k in (
                "requests",
                "retries",
                "reject",
                "status_names",
                "duplicates_across_weeks",
                "events_started_after_as_of",
                "failures",
                "limit_saturated",
            )
        },
        "max_events_in_one_response": fetcher.max_events_seen,
        "espn_completed_events": len(fetch["events"]),
        "audit": audit,
        "regime_note": regime_note(audit, covered),
    }

    if do_build:
        report["backfill"] = write_backfill(fetch["events"], backfill_path)
        log(f"backfill written: {report['backfill']}")
    elif audit_json.exists():
        try:
            previous = json.loads(audit_json.read_text(encoding="utf-8")).get("backfill")
        except ValueError:
            previous = None
        if previous:
            report["backfill"] = previous
            report["backfill_note"] = "carried over from the previous run; not rebuilt by this audit"

    if do_audit or do_build:
        audit_json.parent.mkdir(parents=True, exist_ok=True)
        audit_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        audit_md.write_text(render_markdown(report), encoding="utf-8")
        log(f"wrote {audit_json} and {audit_md}")
    return 1 if fetch["failures"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("audit", "build", "all"))
    parser.add_argument("--as-of", default=AS_OF_UTC)
    parser.add_argument("--first-season", type=int, default=FIRST_SEASON)
    parser.add_argument("--last-season", type=int, default=LAST_SEASON)
    parser.add_argument("--backfill", default=str(DEFAULT_BACKFILL))
    parser.add_argument("--audit-json", default=str(DEFAULT_AUDIT_JSON))
    parser.add_argument("--audit-md", default=str(DEFAULT_AUDIT_MD))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
