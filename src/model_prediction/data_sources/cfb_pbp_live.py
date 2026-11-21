"""Live NCAAF play-by-play store for cfb-structural-v7: discovery, incremental fetch, incremental rows, seed.

Everything lives under ``RuntimePaths.ncaaf_v7_root`` (``<runtime>/research/ncaaf_v7``):

* ``schedule.jsonl``: ESPN scoreboard records for groups 80 (FBS-involved) and 81 (FCS-involved), so
  FCS-vs-FCS games are discovered too. Append-on-change; the latest record per event observed at or before a
  decision time is that event's state then. ``observed_at_utc`` is the scan time.
* ``scan_ledger.json``: per ET date, when it was first and last scanned and whether it is closed (every listed
  event final, forfeited, cancelled or postponed, scanned after the date ended). Open dates are rescanned.
* ``raw/<season>/<event>.json.gz``: ESPN summary wrappers, the research crawl's format (body + sha256 +
  ``fetched_at_utc``); ``crawl_manifest.jsonl`` holds fetch outcomes (last record per event wins).
* ``pbp/<season>.jsonl``: team-game rows built by ``features.cfb_pbp_rows.game_rows`` (the research builder),
  plus ``observed_at_utc`` (the raw fetch time) and the event's ``kickoff_utc``. Only raw files not yet in
  ``features_index.jsonl`` are processed, so a run costs the new games only.
* ``classes/<season>.json``: the season's ESPN group-80 (FBS) team list; any other team is FCS that season.
  This matches the research labels ("plays an FBS-FBS game that season") on every team that played 2024-2026.
* ``cfbd/team_season_priors.jsonl``: CFBD preseason rows, copied whole (covariates are z-scored per season,
  so a partial season would shift every value).

Point in time: a summary is fetched only for an event the scoreboard reports final with kickoff + 5 h (the
rating core's result buffer) in the past; "no plays" counts as terminal only when fetched at least 24 h after
kickoff (before that it is retried). The serving side admits a game by kickoff + 5 h <= T and drops rows
observed after T (``models/cfb_structural_v7.py``).

Fail-soft: ``update`` never raises for network or parse problems; it returns a status dict, and whatever is
missing makes v7 no-call (NO_CALL_V7_PBP_MISSING) rather than price on a stale season. A held lock skips the run.

Commands (``MODEL_PREDICTION_RUNTIME_ROOT`` must be set)::

    python -m model_prediction.data_sources.cfb_pbp_live seed --dry-run   # build into a temp dir, report
    python -m model_prediction.data_sources.cfb_pbp_live seed             # one time, into an empty root
    python -m model_prediction.data_sources.cfb_pbp_live update [--budget-seconds 900]
    python -m model_prediction.data_sources.cfb_pbp_live status
    python -m model_prediction.data_sources.cfb_pbp_live sync-cfbd [--source PATH]  # a new season's CFBD rows
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import PROJECT_ROOT
from ..domain import parse_utc
from ..features.cfb_features_v7 import RESULT_BUFFER_HOURS
from ..features.cfb_pbp_rows import game_rows, load_raw
from ..filelock import lock_exclusive, unlock
from ..runtime_paths import RuntimePaths, rerouted_data_root

_logger = logging.getLogger(__name__)

SITE_API = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
SCOREBOARD_URL = f"{SITE_API}/scoreboard"
SUMMARY_URL = f"{SITE_API}/summary"
FBS_TEAMS_URL = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/{season}/types/2/"
    "groups/80/teams"
)
FBS_GROUP, FCS_GROUP = 80, 81
USER_AGENT = "Mozilla/5.0"  # the repo's generic ESPN user agent
MIN_INTERVAL_S = 1.0  # one request per second at most: the daily run shares this host
HTTP_TIMEOUT_S = 15.0
DEFAULT_BUDGET_S = (
    180.0  # same order as the NCAAF day-ahead capture; a Saturday backlog spans the 4 daily runs
)
BLOCK_STREAK_LIMIT = 3  # consecutive 403/429 responses end the run (resume next run)
RESULT_BUFFER = timedelta(hours=RESULT_BUFFER_HOURS)
NO_PLAYS_FINAL_AFTER = timedelta(hours=24)  # ESPN sometimes posts the play feed late; retry until then
SEASON_FIRST_DAY = (8, 15)  # (month, day): first scoreboard date of a season (week 0 is late August)
SEASON_LAST_DAY = (1, 31)  # (month, day) of the next calendar year: after the national championship
MIN_FBS_TEAMS = 100  # an FBS list shorter than this is a broken response, never a class map
SEED_MARKER = "seed.json"
_ET = ZoneInfo("America/New_York")

FINAL, FORFEIT, CANCELED, POSTPONED, PENDING = "final", "forfeit", "canceled", "postponed", "pending"
TERMINAL_EVENT_STATES = frozenset({FINAL, FORFEIT, CANCELED, POSTPONED})
NOT_PLAYED_STATES = frozenset({FORFEIT, CANCELED, POSTPONED})


# ------------------------------------------------------------------------------------------------ paths
def v7_paths(data_root: str | Path) -> RuntimePaths:
    """RuntimePaths for a daily ``data_root``: the repo's legacy ``data/`` maps to the runtime root, any other
    root (a test's tmp dir) is used as is. The writer and the serving reader both resolve through this."""
    return RuntimePaths(repo_root=PROJECT_ROOT, runtime_root=rerouted_data_root(data_root))


@dataclass(frozen=True)
class Store:
    root: Path

    @property
    def schedule(self) -> Path:
        return self.root / "schedule.jsonl"

    @property
    def scan_ledger(self) -> Path:
        return self.root / "scan_ledger.json"

    @property
    def manifest(self) -> Path:
        return self.root / "crawl_manifest.jsonl"

    @property
    def features_index(self) -> Path:
        return self.root / "features_index.jsonl"

    @property
    def cfbd_priors(self) -> Path:
        return self.root / "cfbd" / "team_season_priors.jsonl"

    @property
    def seed_marker(self) -> Path:
        return self.root / SEED_MARKER

    def raw_path(self, season: int, event_id: str) -> Path:
        return self.root / "raw" / str(season) / f"{event_id}.json.gz"

    def pbp_path(self, season: int) -> Path:
        return self.root / "pbp" / f"{season}.jsonl"

    def classes_path(self, season: int) -> Path:
        return self.root / "classes" / f"{season}.json"


def store_for(paths: RuntimePaths) -> Store:
    return Store(paths.ncaaf_v7_root)


# ------------------------------------------------------------------------------------------------ small io
def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:  # a torn last line from a killed run
                continue


def _append_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> int:
    records = list(records)
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return len(records)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def cfb_season(day: date) -> int:
    """CFB season of an ET calendar date: January-June belong to the previous year's season."""
    return day.year - 1 if day.month <= 6 else day.year


def season_dates(season: int) -> tuple[date, date]:
    return date(season, *SEASON_FIRST_DAY), date(season + 1, *SEASON_LAST_DAY)


def et_date(moment: datetime) -> date:
    return moment.astimezone(_ET).date()


# ------------------------------------------------------------------------------------------------ readers
def read_manifest(store: Store, as_of: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Latest fetch record per event (fetched at or before ``as_of`` when given)."""
    out: dict[str, dict[str, Any]] = {}
    for rec in _iter_jsonl(store.manifest):
        if as_of is not None and parse_utc(rec["fetched_at_utc"]) > as_of:
            continue
        out[str(rec["event_id"])] = rec
    return out


def read_schedule(store: Store, as_of: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Latest schedule record per event observed at or before ``as_of`` (file order = observation order)."""
    out: dict[str, dict[str, Any]] = {}
    for rec in _iter_jsonl(store.schedule):
        if as_of is not None and parse_utc(rec["observed_at_utc"]) > as_of:
            continue
        out[str(rec["event_id"])] = rec
    return out


def read_scan_ledger(store: Store) -> dict[str, dict[str, Any]]:
    if not store.scan_ledger.exists():
        return {}
    return dict(json.loads(store.scan_ledger.read_text(encoding="utf-8")))


def read_features_index(store: Store) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in _iter_jsonl(store.features_index):
        out[str(rec["event_id"])] = rec
    return out


def read_pbp_rows(
    store: Store, seasons: Iterable[int], as_of: datetime | None = None
) -> list[dict[str, Any]]:
    """Team-game rows of ``seasons``: the last row per (event, team), dropping rows observed after ``as_of``."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for season in seasons:
        for row in _iter_jsonl(store.pbp_path(season)):
            observed = row.get("observed_at_utc")
            if as_of is not None and (observed is None or parse_utc(observed) > as_of):
                continue
            latest[(str(row["event_id"]), str(row["team_id"]))] = row
    return list(latest.values())


def read_fbs_teams(store: Store, season: int, as_of: datetime | None = None) -> frozenset[str] | None:
    """The season's FBS team ids, or None when the list is missing, implausible or observed after ``as_of``."""
    path = store.classes_path(season)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if as_of is not None and parse_utc(payload["observed_at_utc"]) > as_of:
        return None
    teams = frozenset(str(t) for t in payload.get("fbs_team_ids") or ())
    return teams if len(teams) >= MIN_FBS_TEAMS else None


def read_cfbd_rows(store: Store, season: int) -> list[dict[str, Any]]:
    return [r for r in _iter_jsonl(store.cfbd_priors) if int(r.get("season", -1)) == season]


def terminal_without_pbp(
    store: Store,
    schedule: Mapping[str, Mapping[str, Any]],
    as_of: datetime | None = None,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
    index: Mapping[str, Mapping[str, Any]] | None = None,
) -> set[str]:
    """Events ESPN will not give play-by-play for: 404, "no plays" fetched >= 24 h after kickoff, or a final
    feed with no scrimmage plays. The research fit used these games as score-only rows; requiring pbp for them
    would blank both teams for the rest of the season."""
    manifest = manifest if manifest is not None else read_manifest(store, as_of)
    index = index if index is not None else read_features_index(store)
    out: set[str] = set()
    for eid, rec in manifest.items():
        status = rec.get("status")
        if status == "http_404" or (status == "no_plays" and _no_plays_is_final(rec, schedule.get(eid))):
            out.add(eid)
        elif status == "ok":
            idx = index.get(eid)
            if (
                idx is not None
                and idx.get("raw_sha256") == rec.get("sha256")
                and int(idx.get("rows", 0)) == 0
            ):
                out.add(eid)
    return out


def _no_plays_is_final(rec: Mapping[str, Any], sched: Mapping[str, Any] | None) -> bool:
    if rec.get("seeded"):
        return True  # the research crawl treated "no plays" as terminal; its fetches came long after kickoff
    kickoff = rec.get("start_utc") or (sched or {}).get("kickoff_utc")
    if not kickoff:
        return False
    return parse_utc(rec["fetched_at_utc"]) >= parse_utc(kickoff) + NO_PLAYS_FINAL_AFTER


# ------------------------------------------------------------------------------------------------ scoreboard
def _event_state(competition: Mapping[str, Any]) -> tuple[str, str | None]:
    stype = (competition.get("status") or {}).get("type") or {}
    name = stype.get("name")
    if name == "STATUS_FORFEIT":
        return FORFEIT, name
    if name in ("STATUS_CANCELED", "STATUS_CANCELLED"):
        return CANCELED, name
    if name == "STATUS_POSTPONED":
        return POSTPONED, name
    if stype.get("completed") and str(stype.get("state")) == "post":
        return FINAL, name
    return PENDING, name


def parse_scoreboard_events(
    payloads: Mapping[int, Mapping[str, Any]], scan_date: date
) -> dict[str, dict[str, Any]]:
    """Merge one date's group-80 and group-81 scoreboards into schedule fields keyed by event id."""
    out: dict[str, dict[str, Any]] = {}
    for group, payload in payloads.items():
        for event in payload.get("events") or []:
            eid = str(event.get("id"))
            if eid in out:
                out[eid]["feed_groups"] = sorted(set(out[eid]["feed_groups"]) | {group})
                continue
            comp = (event.get("competitions") or [{}])[0]
            sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
            home, away = sides.get("home"), sides.get("away")
            if not home or not away:
                continue
            state, name = _event_state(comp)
            season = _int((event.get("season") or {}).get("year"))
            kickoff = event.get("date") or comp.get("date")
            if season is None or not kickoff:
                continue
            home_id = str((home.get("team") or {}).get("id") or home.get("id"))
            away_id = str((away.get("team") or {}).get("id") or away.get("id"))
            scored = state == FINAL
            out[eid] = {
                "event_id": eid,
                "season": season,
                "kickoff_utc": _iso(parse_utc(kickoff)),
                "home_id": home_id,
                "away_id": away_id,
                "home_score": _int(home.get("score")) if scored else None,
                "away_score": _int(away.get("score")) if scored else None,
                "neutral_site": bool(comp.get("neutralSite")),
                "status": state,
                "status_name": name,
                "feed_groups": [group],
                "scan_date": scan_date.isoformat(),
            }
    return out


_CHANGE_KEYS = (
    "season",
    "kickoff_utc",
    "home_id",
    "away_id",
    "home_score",
    "away_score",
    "neutral_site",
    "status",
    "feed_groups",
)


def _schedule_updates(
    parsed: Mapping[str, dict[str, Any]], known: dict[str, dict[str, Any]], observed: datetime
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for eid, rec in parsed.items():
        prev = known.get(eid)
        if prev is not None and all(prev.get(k) == rec.get(k) for k in _CHANGE_KEYS):
            continue
        rec = dict(rec, observed_at_utc=_iso(observed), source="espn_scoreboard")
        if rec["status"] == FINAL:
            same_final = (
                prev is not None
                and prev.get("status") == FINAL
                and all(prev.get(k) == rec.get(k) for k in ("home_score", "away_score"))
            )
            rec["final_observed_at_utc"] = (
                prev["final_observed_at_utc"] if same_final else rec["observed_at_utc"]
            )
        known[eid] = rec
        out.append(rec)
    return out


def _date_closed(day: date, states: Iterable[str], scanned_at: datetime) -> bool:
    day_end = datetime.combine(day + timedelta(days=1), dtime(0), tzinfo=_ET)
    return scanned_at >= day_end and all(s in TERMINAL_EVENT_STATES for s in states)


def scan_gap(store: Store, season: int, decision: datetime) -> str | None:
    """None when every date of ``season`` from its first day through the decision's ET date was scanned at or
    before the decision; otherwise a description of the first gap. Unscanned dates may hide finished games
    whose missing play-by-play the per-team check could not see."""
    first, last = season_dates(season)
    end = min(et_date(decision), last)
    if end < first:
        return None
    ledger = read_scan_ledger(store)
    missing: list[str] = []
    day = first
    while day <= end:
        entry = ledger.get(day.isoformat())
        if entry is None or parse_utc(entry["first_scanned_at_utc"]) > decision:
            missing.append(day.isoformat())
        day += timedelta(days=1)
    if not missing:
        return None
    return f"{len(missing)} unscanned date(s) in season {season}, first {missing[0]}"


# ------------------------------------------------------------------------------------------------ http
class _Blocked(RuntimeError):
    pass


class _BudgetSpent(RuntimeError):
    pass


@dataclass
class Fetcher:
    """Paced GET with a hard deadline. One request per ``min_interval`` seconds; never retries inside a run."""

    client: httpx.Client
    deadline: float
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    min_interval: float = MIN_INTERVAL_S
    requests: int = 0
    block_streak: int = 0
    _last: float = field(default=-1e18)

    def get(self, url: str, params: Mapping[str, Any] | None = None) -> httpx.Response:
        wait = self._last + self.min_interval - self.clock()
        if self.clock() + max(wait, 0.0) >= self.deadline:
            raise _BudgetSpent("time budget spent")
        if wait > 0:
            self.sleep(wait)
        self._last = self.clock()
        self.requests += 1
        resp = self.client.get(url, params=dict(params or {}))
        if resp.status_code in (403, 429):
            self.block_streak += 1
            if self.block_streak >= BLOCK_STREAK_LIMIT:
                raise _Blocked(f"{self.block_streak} consecutive {resp.status_code} responses")
        else:
            self.block_streak = 0
        return resp


def _default_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_S, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def fetch_fbs_teams(fetcher: Fetcher, season: int) -> frozenset[str]:
    resp = fetcher.get(FBS_TEAMS_URL.format(season=season), {"limit": 300})
    resp.raise_for_status()
    payload = resp.json()
    if int(payload.get("pageCount") or 1) != 1:
        raise ValueError(f"FBS team list for {season} is paged; not handled")
    ids = set()
    for item in payload.get("items") or []:
        m = re.search(r"/teams/(\d+)", str(item.get("$ref") or ""))
        if m:
            ids.add(m.group(1))
    if len(ids) < MIN_FBS_TEAMS:
        raise ValueError(f"FBS team list for {season} has {len(ids)} teams; refusing it as a class map")
    return frozenset(ids)


def ensure_class_list(store: Store, fetcher: Fetcher, season: int, now: datetime) -> str:
    if read_fbs_teams(store, season) is not None:
        return "present"
    teams = fetch_fbs_teams(fetcher, season)
    _write_json_atomic(
        store.classes_path(season),
        {
            "season": season,
            "source": FBS_TEAMS_URL.format(season=season),
            "rule": "FBS = listed in ESPN group 80 for the season; every other team is FCS",
            "observed_at_utc": _iso(now),
            "fbs_team_ids": sorted(teams, key=int),
        },
    )
    return "fetched"


def _scan_date(fetcher: Fetcher, day: date) -> dict[int, dict[str, Any]]:
    payloads: dict[int, dict[str, Any]] = {}
    for group in (FBS_GROUP, FCS_GROUP):
        resp = fetcher.get(SCOREBOARD_URL, {"dates": day.strftime("%Y%m%d"), "groups": group, "limit": 500})
        resp.raise_for_status()
        payloads[group] = resp.json()
    return payloads


def _summary_final(payload: Mapping[str, Any]) -> tuple[bool, int]:
    comps = (payload.get("header") or {}).get("competitions") or [{}]
    stype = (comps[0].get("status") or {}).get("type") or {}
    final = bool(stype.get("completed")) and (
        stype.get("name") in ("STATUS_FINAL", "STATUS_FINAL_OVERTIME") or str(stype.get("state")) == "post"
    )
    drives = (payload.get("drives") or {}).get("previous") or []
    return final, sum(len(d.get("plays") or []) for d in drives)


def _game_class(rec: Mapping[str, Any], fbs: frozenset[str] | None) -> str:
    if fbs is not None:
        levels = sorted("FBS" if t in fbs else "FCS" for t in (rec["home_id"], rec["away_id"]))
        return "-".join(levels)
    groups = set(rec.get("feed_groups") or ())
    if groups == {FBS_GROUP}:
        return "FBS-FBS"
    if groups == {FCS_GROUP}:
        return "FCS-FCS"
    return "FBS-FCS" if groups else "UNKNOWN"


def _with_game_class(store: Store, parsed: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Label each scanned event FBS-FBS / FBS-FCS / FCS-FCS from its season's class list (feed groups when the
    list is missing). Informational, like the research rows' ``game_class``: serving labels come from the lists."""
    lists: dict[int, frozenset[str] | None] = {}
    for rec in parsed.values():
        season = int(rec["season"])
        if season not in lists:
            lists[season] = read_fbs_teams(store, season)
        rec["game_class"] = _game_class(rec, lists[season])
    return parsed


def fetch_summary(
    fetcher: Fetcher, store: Store, rec: Mapping[str, Any], game_class: str, now_fn: Callable[[], datetime]
) -> dict[str, Any]:
    """Fetch one event summary; cache it when final. Returns the manifest record (not yet appended)."""
    eid, season = str(rec["event_id"]), int(rec["season"])
    out: dict[str, Any] = {
        "event_id": eid,
        "season": season,
        "game_class": game_class,
        "start_utc": rec["kickoff_utc"],
    }
    try:
        resp = fetcher.get(SUMMARY_URL, {"event": eid})
    except httpx.HTTPError as exc:
        return dict(
            out, fetched_at_utc=_iso(now_fn()), http_status=0, status="error", error=type(exc).__name__
        )
    fetched_at = _iso(now_fn())
    out.update(fetched_at_utc=fetched_at, http_status=resp.status_code)
    if resp.status_code == 404:
        return dict(out, status="http_404")
    if resp.status_code != 200:
        return dict(out, status="error", error=f"http {resp.status_code}")
    body = resp.content
    try:
        text = body.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return dict(out, status="error", error=f"bad body: {type(exc).__name__}")
    final, n_plays = _summary_final(payload)
    out["n_plays"] = n_plays
    if not final:
        return dict(out, status="not_final")
    sha = hashlib.sha256(body).hexdigest()
    path = store.raw_path(season, eid)
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapper = {
        "event_id": eid,
        "season": season,
        "game_class": game_class,
        "start_utc": rec["kickoff_utc"],
        "url": f"{SUMMARY_URL}?event={eid}",
        "fetched_at_utc": fetched_at,
        "http_status": resp.status_code,
        "sha256": sha,
        "n_bytes": len(body),
        "body": text,
    }
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
        json.dump(wrapper, fh)
    os.replace(tmp, path)
    return dict(
        out,
        status="ok" if n_plays > 0 else "no_plays",
        sha256=sha,
        path=path.relative_to(store.root).as_posix(),
    )


def _fetch_done(rec: Mapping[str, Any] | None, sched: Mapping[str, Any], store: Store) -> bool:
    if rec is None:
        return False
    status = rec.get("status")
    if status == "ok":
        return store.raw_path(int(sched["season"]), str(sched["event_id"])).exists() or bool(
            rec.get("seeded")
        )
    if status == "http_404":
        return True
    if status == "no_plays":
        return _no_plays_is_final(rec, sched)
    return False


# ------------------------------------------------------------------------------------------------ rows
def build_new_rows(store: Store) -> dict[str, Any]:
    """Turn raw files not yet in the features index into team-game rows (append-only, per season)."""
    manifest = read_manifest(store)
    index = read_features_index(store)
    added_rows = processed = 0
    unknown: Counter[str] = Counter()
    for eid, rec in manifest.items():
        sha = rec.get("sha256")
        if rec.get("status") not in ("ok", "no_plays") or not sha:
            continue
        if index.get(eid, {}).get("raw_sha256") == sha:
            continue
        path = store.raw_path(int(rec["season"]), eid)
        if not path.exists():
            continue
        meta, payload = load_raw(path)
        rows, unk = game_rows(meta, payload)
        unknown.update(unk)
        for row in rows:
            row["observed_at_utc"] = meta["fetched_at_utc"]
        by_season: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            by_season.setdefault(int(row["season"]), []).append(row)
        for season, season_rows in by_season.items():
            added_rows += _append_jsonl(store.pbp_path(season), season_rows)
        _append_jsonl(
            store.features_index,
            [{"event_id": eid, "raw_sha256": sha, "rows": len(rows), "processed_at_utc": _iso(_utc_now())}],
        )
        processed += 1
    return {"raw_processed": processed, "rows_added": added_rows, "unknown_play_types": dict(unknown)}


# ------------------------------------------------------------------------------------------------ update
@dataclass
class _Lock:
    path: Path
    fh: Any = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("a+", encoding="utf-8")
        try:
            lock_exclusive(self.fh, blocking=False)
        except BlockingIOError:
            self.fh.close()
            self.fh = None
            return False
        return True

    def release(self) -> None:
        if self.fh is not None:
            try:
                unlock(self.fh)
            finally:
                self.fh.close()
                self.fh = None


def update(
    paths: RuntimePaths,
    *,
    budget_seconds: float = DEFAULT_BUDGET_S,
    client: httpx.Client | None = None,
    now_fn: Callable[[], datetime] = _utc_now,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    min_interval: float = MIN_INTERVAL_S,
    require_seed: bool = True,
) -> dict[str, Any]:
    """One incremental pass: class lists, scoreboard discovery, summary fetches, new rows. Never raises for
    network or data problems; returns a status dict."""
    started = clock()
    store = store_for(paths)
    if require_seed and not store.seed_marker.exists():
        return {"status": "skipped", "reason": "not_seeded", "root": str(store.root)}
    lock = _Lock(paths.ncaaf_v7_lock)
    if not lock.acquire():
        return {"status": "skipped", "reason": "locked"}
    own_client = client is None
    http = client or _default_client()
    fetcher = Fetcher(
        http, deadline=started + budget_seconds, clock=clock, sleep=sleep, min_interval=min_interval
    )
    report: dict[str, Any] = {"status": "ok", "errors": []}
    try:
        _update_locked(store, fetcher, now_fn, report)
    except _BudgetSpent:
        report["budget_spent"] = True
    except _Blocked as exc:
        report["status"] = "partial"
        report["errors"].append(f"blocked: {exc}")
    except Exception as exc:  # fail-soft by contract: v7 then no-calls, the daily run goes on
        _logger.warning("NCAAF v7 pbp update failed", exc_info=True)
        report["status"] = "error"
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            report["features"] = build_new_rows(store)
        except Exception as exc:  # same contract
            _logger.warning("NCAAF v7 incremental row build failed", exc_info=True)
            report["status"] = "error"
            report["errors"].append(f"rows: {type(exc).__name__}: {exc}")
        if own_client:
            http.close()
        lock.release()
    if report["errors"] and report["status"] == "ok":
        report["status"] = "partial"
    report["requests"] = fetcher.requests
    report["seconds"] = round(clock() - started, 1)
    return report


def _update_locked(
    store: Store, fetcher: Fetcher, now_fn: Callable[[], datetime], report: dict[str, Any]
) -> None:
    now = now_fn()
    today = et_date(now)
    season = cfb_season(today)
    first, last = season_dates(season)
    report["season"] = season
    if today < first or today > last:
        report["status"] = "offseason"
        return
    # 1. class lists (this season and last: last season's finals are the prior source)
    classes: dict[int, str] = {}
    for s in (season - 1, season):
        try:
            classes[s] = ensure_class_list(store, fetcher, s, now_fn())
        except (httpx.HTTPError, ValueError) as exc:
            classes[s] = "error"
            report["errors"].append(f"classes {s}: {exc}")
    report["classes"] = classes
    # 2. discovery: every unclosed date of the season through tomorrow, oldest first
    known = read_schedule(store)
    ledger = read_scan_ledger(store)
    end = min(today + timedelta(days=1), last)
    todo: list[date] = []
    day = first
    while day <= end:
        entry = ledger.get(day.isoformat())
        if entry is None or not entry.get("closed"):
            todo.append(day)
        day += timedelta(days=1)
    scanned = new_records = 0
    try:
        for day in todo:
            try:
                payloads = _scan_date(fetcher, day)
            except (httpx.HTTPError, ValueError) as exc:
                report["errors"].append(f"scoreboard {day}: {type(exc).__name__}")
                continue
            observed = now_fn()
            parsed = _with_game_class(store, parse_scoreboard_events(payloads, day))
            new_records += _append_jsonl(store.schedule, _schedule_updates(parsed, known, observed))
            key = day.isoformat()
            prev = ledger.get(key) or {}
            ledger[key] = {
                "first_scanned_at_utc": prev.get("first_scanned_at_utc") or _iso(observed),
                "scanned_at_utc": _iso(observed),
                "events": sorted(parsed),
                "closed": _date_closed(day, (r["status"] for r in parsed.values()), observed),
            }
            scanned += 1
    finally:
        _write_json_atomic(store.scan_ledger, ledger)
        report["discovery"] = {
            "dates_due": len(todo),
            "dates_scanned": scanned,
            "schedule_records": new_records,
        }
    # 3. summaries for final games past the result buffer, oldest first
    manifest = read_manifest(store)
    fbs = read_fbs_teams(store, season)
    due = [
        r
        for r in known.values()
        if int(r["season"]) == season
        and r["status"] == FINAL
        and parse_utc(r["kickoff_utc"]) + RESULT_BUFFER <= now
        and not _fetch_done(manifest.get(str(r["event_id"])), r, store)
    ]
    due.sort(key=lambda r: (r["kickoff_utc"], r["event_id"]))
    outcomes: Counter[str] = Counter()
    report["fetch"] = {"due": len(due), "outcomes": outcomes}
    for rec in due:
        result = fetch_summary(fetcher, store, rec, _game_class(rec, fbs), now_fn)
        _append_jsonl(store.manifest, [result])
        outcomes[result["status"]] += 1
    report["fetch"] = {"due": len(due), "outcomes": dict(outcomes)}


def update_from_data_root(
    data_root: str | Path, *, budget_seconds: float = DEFAULT_BUDGET_S
) -> dict[str, Any]:
    """The daily step's entry point (``cli/daily.py``)."""
    return update(v7_paths(data_root), budget_seconds=budget_seconds)


# ------------------------------------------------------------------------------------------------ seed
def _season_of_kickoff(kickoff: datetime) -> int:
    # scripts/cfb_v7_fit.py::season_of, used there for the score-only rows of the v6 snapshot
    return kickoff.year - 1 if kickoff.month in (1, 2) else kickoff.year


def seed(
    paths: RuntimePaths,
    research_dir: Path,
    snapshot: Path,
    *,
    raw_seasons: Sequence[int] = (2025, 2026),
    sweep_season: int | None = 2026,
    client: httpx.Client | None = None,
    now_fn: Callable[[], datetime] = _utc_now,
    min_interval: float = MIN_INTERVAL_S,
) -> dict[str, Any]:
    """Populate an EMPTY v7 root from the research crawl. Adds files only; refuses a non-empty target.

    Copies the pbp rows (all seasons, adding ``observed_at_utc`` = the research fetch time), the raw files and
    manifest of ``raw_seasons``, the v6 snapshot as schedule records, the CFBD priors (whole file); fetches the
    FBS lists of ``raw_seasons``; then sweeps ``sweep_season``'s scoreboards (groups 80/81) so the scan ledger
    is complete and the first update knows the catch-up list."""
    store = store_for(paths)
    if store.root.exists() and any(store.root.iterdir()):
        raise FileExistsError(f"{store.root} is not empty; the seed only adds files to a new directory")
    now = now_fn()
    report: dict[str, Any] = {"root": str(store.root), "started_at_utc": _iso(now)}
    store.root.mkdir(parents=True, exist_ok=True)

    # pbp rows
    pbp_counts: Counter[int] = Counter()
    rows_by_event: Counter[str] = Counter()
    buffers: dict[int, list[dict[str, Any]]] = {}
    for row in _iter_jsonl(research_dir / "team_game_pbp.jsonl"):
        row["observed_at_utc"] = row["fetched_at_utc"]
        season = int(row["season"])
        buffers.setdefault(season, []).append(row)
        pbp_counts[season] += 1
        rows_by_event[str(row["event_id"])] += 1
    for season, rows in buffers.items():
        _append_jsonl(store.pbp_path(season), rows)
    report["pbp_rows"] = dict(sorted(pbp_counts.items()))

    # raw + manifest + features index (raw copied for raw_seasons only)
    research_manifest = {}
    for rec in _iter_jsonl(research_dir / "crawl_manifest.jsonl"):
        research_manifest[str(rec["event_id"])] = rec
    manifest_out, index_out = [], []
    copied: Counter[int] = Counter()
    for eid, rec in research_manifest.items():
        season = int(rec["season"])
        rec = dict(rec, seeded=True)
        src = research_dir / "raw" / str(season) / f"{eid}.json.gz"
        if season in raw_seasons and src.exists():
            dst = store.raw_path(season, eid)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            rec["path"] = dst.relative_to(store.root).as_posix()
            copied[season] += 1
        else:
            rec.pop("path", None)
        manifest_out.append(rec)
        if rec.get("sha256"):
            index_out.append(
                {
                    "event_id": eid,
                    "raw_sha256": rec["sha256"],
                    "rows": rows_by_event.get(eid, 0),
                    "seeded": True,
                }
            )
    _append_jsonl(store.manifest, manifest_out)
    _append_jsonl(store.features_index, index_out)
    report["raw_copied"] = dict(sorted(copied.items()))
    report["manifest_records"] = len(manifest_out)

    # schedule from the v6 snapshot
    snap_observed = _iso(datetime.fromtimestamp(snapshot.stat().st_mtime, UTC))
    sched_out = []
    for s in _iter_jsonl(snapshot):
        kick = parse_utc(s["start_utc"])
        scored = s.get("home_score") is not None and s.get("away_score") is not None
        status = FORFEIT if s.get("is_forfeit") else (FINAL if scored else PENDING)
        sched_out.append(
            {
                "event_id": str(s["event_id"]),
                "season": _season_of_kickoff(kick),
                "kickoff_utc": _iso(kick),
                "home_id": str(s["home_id"]),
                "away_id": str(s["away_id"]),
                "home_score": s.get("home_score") if status == FINAL else None,
                "away_score": s.get("away_score") if status == FINAL else None,
                "neutral_site": bool(s.get("neutral")),
                "status": status,
                "status_name": None,
                "feed_groups": None,
                "game_class": s.get("game_class"),
                "observed_at_utc": snap_observed,
                "final_observed_at_utc": snap_observed if status == FINAL else None,
                "source": "seed:ncaaf_v6_history_snapshot",
            }
        )
    _append_jsonl(store.schedule, sched_out)
    report["schedule_seed_records"] = len(sched_out)

    # CFBD priors, whole file
    cfbd_src = research_dir / "cfbd" / "team_season_priors.jsonl"
    store.cfbd_priors.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfbd_src, store.cfbd_priors)
    report["cfbd_rows"] = sum(1 for _ in _iter_jsonl(store.cfbd_priors))

    # network: class lists and the season sweep
    own_client = client is None
    http = client or _default_client()
    fetcher = Fetcher(http, deadline=float("inf"), min_interval=min_interval)
    try:
        report["classes"] = {s: ensure_class_list(store, fetcher, s, now_fn()) for s in raw_seasons}
        if sweep_season is not None:
            report["sweep"] = _sweep_season(store, fetcher, sweep_season, now_fn)
    finally:
        if own_client:
            http.close()
    report["requests"] = fetcher.requests
    _write_json_atomic(store.seed_marker, report)
    return report


def _sweep_season(
    store: Store, fetcher: Fetcher, season: int, now_fn: Callable[[], datetime]
) -> dict[str, Any]:
    first, last = season_dates(season)
    end = min(et_date(now_fn()) + timedelta(days=1), last)
    known = read_schedule(store)
    ledger = read_scan_ledger(store)
    day, scanned, records, errors = first, 0, 0, []
    while day <= end:
        try:
            payloads = _scan_date(fetcher, day)
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{day}: {type(exc).__name__}")
            day += timedelta(days=1)
            continue
        observed = now_fn()
        parsed = _with_game_class(store, parse_scoreboard_events(payloads, day))
        records += _append_jsonl(store.schedule, _schedule_updates(parsed, known, observed))
        ledger[day.isoformat()] = {
            "first_scanned_at_utc": _iso(observed),
            "scanned_at_utc": _iso(observed),
            "events": sorted(parsed),
            "closed": _date_closed(day, (r["status"] for r in parsed.values()), observed),
        }
        scanned += 1
        day += timedelta(days=1)
    _write_json_atomic(store.scan_ledger, ledger)
    manifest = read_manifest(store)
    now = now_fn()
    catch_up: Counter[str] = Counter()
    fbs = read_fbs_teams(store, season)
    for r in known.values():
        if int(r["season"]) != season or r["status"] != FINAL:
            continue
        if parse_utc(r["kickoff_utc"]) + RESULT_BUFFER > now:
            continue
        if not _fetch_done(manifest.get(str(r["event_id"])), r, store):
            catch_up[_game_class(r, fbs)] += 1
    return {
        "dates_scanned": scanned,
        "schedule_records": records,
        "errors": errors,
        "catch_up_events_by_class": dict(catch_up),
    }


def status(paths: RuntimePaths) -> dict[str, Any]:
    store = store_for(paths)
    manifest = read_manifest(store)
    sched = read_schedule(store)
    now = _utc_now()
    season = cfb_season(et_date(now))
    return {
        "root": str(store.root),
        "seeded": store.seed_marker.exists(),
        "season": season,
        "scan_gap": scan_gap(store, season, now),
        "schedule_events": len(sched),
        "manifest": dict(Counter(r.get("status") for r in manifest.values())),
        "pbp_rows_by_season": {
            p.stem: sum(1 for _ in _iter_jsonl(p)) for p in sorted((store.root / "pbp").glob("*.jsonl"))
        },
        "classes": {s: (len(t) if (t := read_fbs_teams(store, s)) else None) for s in (season - 1, season)},
    }


def sync_cfbd(paths: RuntimePaths, source: Path) -> dict[str, Any]:
    """Append CFBD prior rows of seasons the store does not hold yet, whole seasons only (covariates are z-scored
    within a season, so a season is never merged or partially replaced). Run once a new season's rows exist in
    ``source`` (``scripts/cfbd_backfill.py`` writes them), before that season's first v7 call."""
    store = store_for(paths)
    have = {int(r["season"]) for r in _iter_jsonl(store.cfbd_priors)}
    new = [r for r in _iter_jsonl(source) if int(r["season"]) not in have]
    _append_jsonl(store.cfbd_priors, new)
    return {
        "seasons_present": sorted(have),
        "seasons_added": sorted({int(r["season"]) for r in new}),
        "rows": len(new),
    }


# ------------------------------------------------------------------------------------------------ cli
def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NCAAF v7 live play-by-play store")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--research-dir", type=Path, default=PROJECT_ROOT / "data" / "research" / "ncaaf_v7")
    p_seed.add_argument(
        "--snapshot",
        type=Path,
        default=PROJECT_ROOT / "data" / "research" / "ncaaf_v6" / "history_snapshot.jsonl",
    )
    p_seed.add_argument(
        "--dry-run", action="store_true", help="seed a temporary directory, report, delete it"
    )
    p_up = sub.add_parser("update")
    p_up.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_S)
    sub.add_parser("status")
    p_cfbd = sub.add_parser("sync-cfbd")
    p_cfbd.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "data" / "research" / "ncaaf_v7" / "cfbd" / "team_season_priors.jsonl",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "seed" and args.dry_run:
        with tempfile.TemporaryDirectory(prefix="ncaaf_v7_seed_") as tmp:
            paths = RuntimePaths(repo_root=PROJECT_ROOT, runtime_root=Path(tmp))
            report = seed(paths, args.research_dir, args.snapshot)
            report["dry_run"] = True
            report["status_after"] = status(paths)
    else:
        paths = RuntimePaths.resolve(require_external_runtime=True)
        if args.cmd == "seed":
            report = seed(paths, args.research_dir, args.snapshot)
        elif args.cmd == "update":
            report = update(paths, budget_seconds=args.budget_seconds)
        elif args.cmd == "sync-cfbd":
            report = sync_cfbd(paths, args.source)
        else:
            report = status(paths)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
