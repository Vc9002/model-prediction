"""NBA v10 forward research track: player-model shadow log, Polymarket mid polls and T-C2 events.

Pre-registered in ``outputs/nba/v10_forward/PREREG_FORWARD.md`` (tests A and B). Called by the
pre-tip worker after its ESPN/Polymarket capture has finished, inside a time budget and a daemon
thread (``nba_pretip_capture_worker``), so nothing here can change that capture's behaviour or exit
status. Research logs only: no ledger, no Main/Flat row, no production config, no champion, no
buyer, no served price.

One run, in order (each step fail-soft, each checks the deadline first):

1. ``injury``: new official injury report PDFs (``data_sources.nba_injury_live``).
2. ``polymarket``: one event-detail request per game in the ESPN 12-hour window; the long-side
   mid of the moneyline and of every spread/total rung inside 10-90c (plus rungs fixed by open
   T-C2 events) to ``pm_mids/<ET date>.jsonl``. This is a separate research file on purpose:
   ``polymarket_snapshots.jsonl`` feeds the closing-quote and CLV readers.
3. ``player``: ``nba-player-v10`` margin/total means for every window game as of now, appended to
   ``player_log.jsonl``. The player state for ET date D holds games on dates before D (the
   research contract); availability comes from stored reports with ``fetched_at_utc`` <= the
   record's ``observed_at_utc``, which is stamped after every slow load.
4. ``tc2``: status changes between consecutive stored reports (``tc2_events.jsonl``), with the
   model delta and the fixed Polymarket rungs, plus follow-up quotes for 40 minutes.

Box scores: the research box file (dates < 2026-02-23) plus the ESPN summary captures in
``data/availability/nba/espn_boxscores/``, joined onto the schedule so a missing box is a
``has_box = False`` game. No scheduled job writes current-season captures today (only the manual
``scripts/backfill_nba_boxscores.py``), so a game whose team's latest completed game has no box is
``NO_CALL_BOX_FEED_STALE``.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .data_sources import nba_injury_live
from .data_sources.nba_injury_report import NOT_SUBMITTED, TEAM_TRICODES
from .data_sources.nba_pretip_capture import espn_odds_path, match_polymarket_event
from .domain import EASTERN
from .features.nba_player_ratings import (
    HOLDOUT_START,
    STAT_KEYS,
    BoxPrior,
    GameBox,
    InjuryReportIndex,
    MinutesModel,
    PlayerLayerState,
    PlayerLine,
    RatingParams,
    TeamReport,
    by_date,
    game_features,
    load_box_games,
    match_report,
    normalize_name,
    normalize_status,
    parse_utc,
    season_year_of,
)
from .models.nba_player_v10 import MODEL_VERSION, PlayerV10Coefficients, predict

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"
PLAYER_ARTIFACT = Path("config/models/research/nba-player-v10.json")
ANCHOR_ARTIFACT = Path("config/models/research/nba-anchor-v10.json")
VARIANTS = ("avail", "exavail")
ESPN_WINDOW = timedelta(hours=12)
CONSENSUS_MAX_AGE = timedelta(minutes=15)  # PREREG 2: consensus at t
MID_MAX_WIDTH = 0.10  # nba-anchor-v10's two-sided mid rule
POLL_KEEP_LOW, POLL_KEEP_HIGH = 0.10, 0.90
POLL_REQUEST_INTERVAL_SECONDS = 0.25  # the capture's event-detail spacing
TC2_MIN_MPG = 20.0  # plan section 9 (pinned)
TC2_MPG_WINDOW = 10
TC2_MIN_PLAYED = 3
TC2_MAX_REPORT_GAP = timedelta(minutes=20)  # consecutive slots only
TC2_BEFORE_MAX_AGE = timedelta(minutes=20)
TC2_FOLLOW = timedelta(minutes=40)
TC2_SCAN_LOOKBACK = timedelta(hours=3)
TC2_MIN_DELTA = 0.05
LISTED_GROUP = {"available", "probable", "unlisted"}
COLD_BUILD_MIN_SECONDS = 40.0
BLACKOUT_ET = (20, 80)  # 00:20-01:20 ET: no state build (host memory pressure, no games)

NO_CALL_BOX_FEED_STALE = "NO_CALL_BOX_FEED_STALE"
NO_CALL_TEAM_UNMAPPED = "NO_CALL_TEAM_UNMAPPED"
NO_CALL_NOT_TODAY = "NO_CALL_NOT_TODAY"
NO_CALL_ARTIFACT_HASH = "NO_CALL_ARTIFACT_HASH"

Clock = Callable[[], datetime]

# ESPN summary stat key (and part of an "M-A" pair) for each STAT_KEYS entry.
_ESPN_KEYS = {
    "pts": ("points", 0),
    "fg3m": ("threePointFieldGoalsMade-threePointFieldGoalsAttempted", 0),
    "fga": ("fieldGoalsMade-fieldGoalsAttempted", 1),
    "fta": ("freeThrowsMade-freeThrowsAttempted", 1),
    "ast": ("assists", 0),
    "tov": ("turnovers", 0),
    "oreb": ("offensiveRebounds", 0),
    "dreb": ("defensiveRebounds", 0),
    "stl": ("steals", 0),
    "blk": ("blocks", 0),
    "pf": ("fouls", 0),
}
MIN_TEAM_MINUTES = 215.0  # rounded ESPN minutes sum to 223-232 in regulation; rejects partial boxes


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class ForwardConfig:
    """Where the forward track reads and writes. Every path is explicit (tests, scratch runs)."""

    runtime_root: Path  # ESPN odds captures are read from here
    repo_data: Path  # schedule, box captures, research box file (read-only)
    repo_root: Path  # research artifacts under config/models/research
    out_root: Path  # research logs: player_log.jsonl, tc2_events.jsonl, pm_mids/, box_cache.jsonl
    injury_root: Path  # official report store

    @classmethod
    def default(cls, runtime_root: Path, repo_root: Path, research_root: Path) -> ForwardConfig:
        return cls(
            runtime_root=Path(runtime_root),
            repo_data=Path(repo_root) / "data",
            repo_root=Path(repo_root),
            out_root=Path(research_root) / "nba_v10_forward",
            injury_root=nba_injury_live.injury_store_root(runtime_root),
        )

    def with_scratch(self, scratch: Path) -> ForwardConfig:
        """Same inputs, every write redirected under ``scratch`` (test runs)."""
        return ForwardConfig(
            runtime_root=self.runtime_root,
            repo_data=self.repo_data,
            repo_root=self.repo_root,
            out_root=Path(scratch) / "research" / "nba_v10_forward",
            injury_root=Path(scratch) / "injury_reports" / "nba",
        )


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _append(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")
    return len(rows)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    opener: Callable[..., Any] = gzip.open if path.suffix == ".gz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for raw in handle:
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
    return out


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def anchor_mean(line: float, p_low: float, sigma: float) -> float:
    """``cli.forecast_nba_anchor_v10.anchor_mean``: line-space mean implied by P(low side) at a line."""
    from .cli.forecast_nba_anchor_v10 import anchor_mean as _anchor_mean

    return _anchor_mean(line, p_low, sigma)


# --------------------------------------------------------------------------- box scores
def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _espn_stat(raw: list[Any], index: dict[str, int], key: str, part: int) -> float:
    i = index.get(key)
    if i is None or i >= len(raw):
        return 0.0
    text = str(raw[i])
    if "-" in text and key.count("-") == 1:
        pieces = text.split("-")
        return _num(pieces[part]) if len(pieces) == 2 else 0.0
    return _num(text)


def summary_box_rows(payload: Mapping[str, Any], home_name: str, away_name: str) -> list[list[Any]] | None:
    """Player rows ``[id, name, home, minutes, starter, *STAT_KEYS]`` of a completed ESPN summary.

    The same parser ``scripts/nba_v10/player_build_data.py`` uses for the research box file, so the
    forward state reads live captures exactly as the fit did. None unless the game is completed and
    both sides are the scheduled teams.
    """
    comp = ((payload.get("header") or {}).get("competitions") or [{}])[0]
    if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
        return None
    rows: list[list[Any]] = []
    sides = 0
    for team_entry in (payload.get("boxscore") or {}).get("players") or []:
        team_name = (team_entry.get("team") or {}).get("displayName")
        if team_name == home_name:
            home = 1
        elif team_name == away_name:
            home = 0
        else:
            return None
        sides += 1
        group = (team_entry.get("statistics") or [{}])[0]
        index = {name: i for i, name in enumerate(group.get("keys") or [])}
        for athlete in group.get("athletes") or []:
            meta = athlete.get("athlete") or {}
            if meta.get("id") is None:
                continue
            raw = athlete.get("stats") or []
            dnp = bool(athlete.get("didNotPlay")) or not raw
            minutes = 0.0 if dnp else _espn_stat(raw, index, "minutes", 0)
            stats = (
                [0.0] * len(STAT_KEYS) if dnp else [_espn_stat(raw, index, *_ESPN_KEYS[k]) for k in STAT_KEYS]
            )
            rows.append(
                [
                    str(meta["id"]),
                    meta.get("displayName") or "",
                    home,
                    minutes,
                    1 if athlete.get("starter") else 0,
                ]
                + stats
            )
    return rows if sides == 2 else None


def team_minutes_ok(players: Sequence[Sequence[Any]]) -> bool:
    for side in (0, 1):
        if sum(p[3] for p in players if p[2] == side) < MIN_TEAM_MINUTES:
            return False
    return True


def _schedule_after_holdout(repo_data: Path, before: str) -> list[dict[str, Any]]:
    """Completed non-preseason franchise games with HOLDOUT_START <= ET date < ``before``.

    The research scoreboard index (proper season types) first; ``nba_games_all.jsonl`` adds any
    event the index does not have (dates after its last fetch). Rows without a season type (the
    file's older format) are skipped: the index covers those dates.
    """
    games: dict[str, dict[str, Any]] = {}
    for r in _read_jsonl(repo_data / "research" / "nba_v10" / "games" / "scoreboard_index.jsonl"):
        day = str(r.get("scoreboard_date") or "")
        if not (HOLDOUT_START <= day < before) or r.get("status") != "completed":
            continue
        if r.get("season_type") in (None, "preseason"):
            continue
        games[str(r["event_id"])] = {
            "event_id": str(r["event_id"]),
            "date": day,
            "start_utc": str(r["event_start_utc"]),
            "season_year": int(r["season_year"]),
            "season_type": str(r["season_type"]),
            "home_id": str(r["home_team_id"]),
            "away_id": str(r["away_team_id"]),
            "home_name": str(r["home_team"]),
            "away_name": str(r["away_team"]),
            "home_score": int(r["home_score"]),
            "away_score": int(r["away_score"]),
        }
    for r in _read_jsonl(repo_data / "historical" / "nba_games_all.jsonl"):
        eid = str(r.get("event_id") or "")
        if not eid or eid in games or r.get("status") != "completed":
            continue
        if r.get("season_type") in (None, "preseason") or r.get("home_score") is None:
            continue
        try:
            start = parse_utc(str(r["event_start_utc"]))
        except (KeyError, ValueError):
            continue
        day = start.astimezone(EASTERN).date().isoformat()
        if not (HOLDOUT_START <= day < before):
            continue
        games[eid] = {
            "event_id": eid,
            "date": day,
            "start_utc": str(r["event_start_utc"]),
            "season_year": int(r.get("season_year") or season_year_of(day)),
            "season_type": str(r["season_type"]),
            "home_id": str(r["home_team_id"]),
            "away_id": str(r["away_team_id"]),
            "home_name": str(r["home_team"]),
            "away_name": str(r["away_team"]),
            "home_score": int(r["home_score"]),
            "away_score": int(r["away_score"]),
        }
    return [g for g in games.values() if int(g["home_id"]) <= 30 and int(g["away_id"]) <= 30]


def _capture_boxes(repo_data: Path, out_root: Path, schedule: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Player rows per event from the ESPN summary captures, through a parse cache.

    The cache (``box_cache.jsonl``) is keyed on the capture's size and mtime, so a re-captured file
    is parsed again; later lines win.
    """
    captures = repo_data / "availability" / "nba" / "espn_boxscores"
    cache_path = out_root / "box_cache.jsonl"
    cache = {str(r["event_id"]): r for r in _read_jsonl(cache_path) if r.get("event_id")}
    out: dict[str, Any] = {}
    fresh: list[dict[str, Any]] = []
    for g in schedule:
        eid = g["event_id"]
        path = captures / f"{eid}.json"
        try:
            st = path.stat()
        except OSError:
            continue
        key = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "home": g["home_name"], "away": g["away_name"]}
        hit = cache.get(eid)
        if hit is not None and all(hit.get(k) == v for k, v in key.items()):
            out[eid] = hit.get("players")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8")).get("payload")
            rows = (
                summary_box_rows(payload, g["home_name"], g["away_name"])
                if isinstance(payload, dict)
                else None
            )
        except (OSError, ValueError, AttributeError, KeyError, TypeError, IndexError):
            rows = None
        rows = rows if rows and team_minutes_ok(rows) else None
        out[eid] = rows
        fresh.append({"event_id": eid, **key, "players": rows})
    _append(cache_path, fresh)
    return out


def _game_box(row: Mapping[str, Any], players: Sequence[Sequence[Any]] | None) -> GameBox:
    return GameBox(
        event_id=row["event_id"],
        date=row["date"],
        start_utc=row["start_utc"],
        season_year=row["season_year"],
        season_type=row["season_type"],
        home_id=row["home_id"],
        away_id=row["away_id"],
        home_name=row["home_name"],
        away_name=row["away_name"],
        home_score=row["home_score"],
        away_score=row["away_score"],
        players=None
        if not players
        else tuple(
            PlayerLine(
                str(p[0]), str(p[1]), bool(p[2]), float(p[3]), bool(p[4]), tuple(float(x) for x in p[5:])
            )
            for p in players
        ),
    )


# --------------------------------------------------------------------------- state
@dataclass
class ForwardState:
    """Both rating states frozen at ``day`` (games on earlier ET dates only) plus PIT side tables."""

    day: str
    states: dict[str, PlayerLayerState]  # "margin" (h=730) and "total" (h=365), as cmd_walk
    coefs: dict[str, PlayerV10Coefficients]
    artifact_hash: str
    team_ids: dict[str, str]  # ESPN display name -> team id
    last_game: dict[str, tuple[str, bool]]  # team id -> (ET date of latest completed game, has box)
    recent_missing: dict[str, int]  # team id -> current-season games without box among its last 5
    minutes_last10: dict[str, tuple[float, int]]  # player id -> (mean raw minutes, played games)
    fingerprint: dict[str, Any] = field(default_factory=dict)


def load_artifact(repo_root: Path) -> tuple[dict[str, Any], bool]:
    """The research artifact and whether its embedded hash verifies."""
    from .production_registry import compute_artifact_hash

    artifact = json.loads((repo_root / PLAYER_ARTIFACT).read_text(encoding="utf-8"))
    return artifact, compute_artifact_hash(artifact) == artifact.get("artifact_hash")


def anchor_sigmas(repo_root: Path) -> dict[str, float]:
    payload = json.loads((repo_root / ANCHOR_ARTIFACT).read_text(encoding="utf-8"))
    return {m: float(payload["markets"][m]["sigma"]) for m in ("spread", "total")}


def input_fingerprint(cfg: ForwardConfig, day: str) -> str:
    """Hash of everything ``build_state`` reads: artifact, research box file, schedule files and the
    size/mtime of every box capture. A changed input (a new capture, a daily history refresh)
    changes it, so the cached state is rebuilt."""
    import hashlib

    digest = hashlib.sha256(f"{SCHEMA_VERSION}|{day}".encode())
    files = [
        cfg.repo_root / PLAYER_ARTIFACT,
        cfg.repo_data / "research" / "nba_v10" / "player" / "box_games.jsonl",
        cfg.repo_data / "research" / "nba_v10" / "games" / "scoreboard_index.jsonl",
        cfg.repo_data / "historical" / "nba_games_all.jsonl",
    ]
    for path in files:
        try:
            st = path.stat()
            digest.update(f"{path.name}|{st.st_size}|{st.st_mtime_ns}".encode())
        except OSError:
            digest.update(f"{path.name}|missing".encode())
    captures = cfg.repo_data / "availability" / "nba" / "espn_boxscores"
    try:
        entries = sorted(
            (e.name, e.stat().st_size, e.stat().st_mtime_ns) for e in os.scandir(captures) if e.is_file()
        )
    except OSError:
        entries = []
    digest.update(json.dumps(entries).encode())
    return digest.hexdigest()


def _freeze_for_cache(fs: ForwardState) -> ForwardState:
    """Drop what a frozen day never reads again, so the state pickles small and at all.

    After ``begin_day(day)`` no game is added, so the ridge sufficient statistics and warm starts are
    dead weight, and ``team_pace`` (a defaultdict with a lambda factory) becomes a plain dict;
    ``pace_dev`` only reads it with ``.get``.
    """
    for state in fs.states.values():
        state._S = {}
        state._b = {}
        state._warm = {}
        state.team_pace = dict(state.team_pace)  # type: ignore[assignment]
    return fs


def load_or_build_state(
    cfg: ForwardConfig, day: str, *, seconds_left: Callable[[], float] | None = None
) -> tuple[ForwardState | None, bool]:
    """The day's frozen state from the research cache when its inputs are unchanged, else rebuilt.

    Returns (state, cache_hit); state is None when a rebuild is needed and ``seconds_left()`` is
    under ``COLD_BUILD_MIN_SECONDS``. The cache is our own file under ``out_root``; any read problem
    rebuilds.
    """
    import pickle

    fingerprint = input_fingerprint(cfg, day)
    path = cfg.out_root / "state_cache" / f"{day}.pkl"
    try:
        with path.open("rb") as handle:
            cached = pickle.load(handle)
        if cached.get("fingerprint") == fingerprint:
            return cached["state"], True
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError, KeyError, TypeError):
        pass
    if seconds_left is not None and seconds_left() < COLD_BUILD_MIN_SECONDS:
        # A rebuild takes 15-25 s and cannot stop half way; leave it to a run with time to spare.
        return None, False
    fs = _freeze_for_cache(build_state(cfg, day))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        for old in path.parent.glob("*.pkl"):
            if old.name != path.name:
                old.unlink(missing_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("wb") as handle:
            pickle.dump({"fingerprint": fingerprint, "state": fs}, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except OSError:
        _logger.warning("nba v10 forward state cache write failed", exc_info=True)
    return fs, False


def build_state(cfg: ForwardConfig, day: str) -> ForwardState:
    """Advance both rating states through every game on ET dates before ``day``, then freeze at it.

    Research rows (< HOLDOUT_START) come through ``load_box_games``, whose holdout guard is kept;
    later games are rating inputs only (never scored), joined from the schedule and the captures.
    """
    artifact, ok = load_artifact(cfg.repo_root)
    if not ok:
        raise ValueError(NO_CALL_ARTIFACT_HASH)
    games = load_box_games(cfg.repo_data / "research" / "nba_v10" / "player" / "box_games.jsonl")
    schedule = _schedule_after_holdout(cfg.repo_data, day)
    boxes = _capture_boxes(cfg.repo_data, cfg.out_root, schedule)
    games.extend(_game_box(row, boxes.get(row["event_id"])) for row in schedule)
    games.sort(key=lambda g: (g.date, g.start_utc, g.event_id))

    minutes_model = MinutesModel.from_dict(artifact["minutes_model"])
    rp = artifact["rating_params"]
    specs = {
        "margin": (float(rp["margin"]["half_life_days"]), float(rp["margin"]["ridge"]), math.inf),
        "total": (float(rp["total"]["half_life_days"]), math.inf, float(rp["total"]["ridge"])),
    }
    states: dict[str, PlayerLayerState] = {}
    for market, (h, ridge_m, ridge_t) in specs.items():
        prior = BoxPrior.from_dict(artifact["box_prior"][str(int(h))])
        state = PlayerLayerState(RatingParams(h, (ridge_m,), (ridge_t,)), prior, minutes_model)
        for d, day_games in by_date(games):
            if d >= day:
                break
            state.begin_day(d, solve=False)
            state.add_games(day_games)
        state.begin_day(day, solve=True)
        states[market] = state

    team_ids: dict[str, str] = {}
    last_game: dict[str, tuple[str, bool]] = {}
    per_team: dict[str, list[GameBox]] = defaultdict(list)
    for g in games:
        if g.date >= day:
            break
        team_ids[g.home_name] = g.home_id
        team_ids[g.away_name] = g.away_id
        for team in (g.home_id, g.away_id):
            last_game[team] = (g.date, g.players is not None)
            per_team[team].append(g)
    season = season_year_of(day)
    recent_missing = {
        team: sum(1 for g in log[-5:] if g.season_year == season and g.players is None)
        for team, log in per_team.items()
    }
    return ForwardState(
        day=day,
        states=states,
        coefs={v: PlayerV10Coefficients.from_dict(artifact["coefficients"][v]) for v in VARIANTS},
        artifact_hash=str(artifact["artifact_hash"]),
        team_ids=team_ids,
        last_game=last_game,
        recent_missing=recent_missing,
        minutes_last10=last10_minutes(games, day),
        fingerprint={
            "games_loaded": len([g for g in games if g.date < day]),
            "schedule_after_holdout": len(schedule),
            "with_box_after_holdout": sum(1 for r in schedule if boxes.get(r["event_id"])),
            "latest_box_date": max((r["date"] for r in schedule if boxes.get(r["event_id"])), default=None),
            "latest_schedule_date": max((r["date"] for r in schedule), default=None),
        },
    )


def last10_minutes(games: Iterable[GameBox], day: str) -> dict[str, tuple[float, int]]:
    """Player id -> (mean raw box minutes over the last 10 played games, played games), using only
    games on ET dates before ``day`` (PIT: the report's own game date is never included)."""
    played: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=TC2_MPG_WINDOW))
    for g in sorted(games, key=lambda g: (g.date, g.start_utc, g.event_id)):
        if g.date >= day:
            break
        for p in g.players or ():
            if p.minutes > 0:
                played[p.player_id].append(p.minutes)
    return {pid: (sum(d) / len(d), len(d)) for pid, d in played.items() if d}


# --------------------------------------------------------------------------- injury index
def _valid_reports(reports: Iterable[Mapping[str, Any]], decision: datetime) -> dict[tuple[str, str], dict]:
    """Parsed reports fetched at or before ``decision``, keyed (filename, fetched_at_utc).

    The index orders reports by fetch time, but one run can fetch several slots (newest first),
    and a slot can arrive a run after a newer one. A report published before one we had ALREADY
    fetched is dropped: it was stale when we got it. That depends only on earlier fetches, so it
    is point-in-time safe, and it keeps "latest fetched" equal to "latest published" for every
    decision time. A filename fetched twice (a run killed between writes) keeps its first fetch.
    """
    candidates = []
    for r in reports:
        if r.get("parse_error") or not r.get("fetched_at_utc") or not r.get("published_at_utc"):
            continue
        try:
            fetched = parse_utc(str(r["fetched_at_utc"]))
            published = parse_utc(str(r["published_at_utc"]))
        except ValueError:
            continue
        if fetched <= decision:
            candidates.append((fetched, published, dict(r)))
    out: dict[tuple[str, str], dict] = {}
    seen: set[str] = set()
    newest: datetime | None = None
    for fetched, published, r in sorted(candidates, key=lambda c: (c[0], c[1])):
        if str(r["filename"]) in seen or (newest is not None and published < newest):
            continue
        seen.add(str(r["filename"]))
        newest = published if newest is None else max(newest, published)
        out[(str(r["filename"]), str(r["fetched_at_utc"]))] = r
    return out


def live_injury_index(
    reports: Iterable[Mapping[str, Any]], rows: Iterable[Mapping[str, Any]], decision: datetime
) -> InjuryReportIndex:
    """``InjuryReportIndex`` over the live store, observed at FETCH time, keyed by team tricode.

    Same aggregation as ``player_build_data.py injuries`` (content per team each time it changes,
    ``None`` = NOT YET SUBMITTED), except that the observed time is ``fetched_at_utc`` (when we knew
    it) and teams / matchups are tricodes, so ESPN and report team names never need to agree.
    """
    valid = _valid_reports(reports, decision)
    dates: dict[str, dict[str, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
    content: dict[tuple[str, str, str], list[list[str]] | None] = {}
    for r in rows:
        key = (str(r.get("report_filename")), str(r.get("fetched_at_utc")))
        if key not in valid or not r.get("team_tricode") or not r.get("away_tricode"):
            continue
        gd, obs, team = str(r["game_date"]), key[1], str(r["team_tricode"])
        dates[gd][obs].add((str(r["away_tricode"]), str(r["home_tricode"])))
        ckey = (gd, obs, team)
        if ckey not in content:
            content[ckey] = []
        if r["status"] == NOT_SUBMITTED:
            content[ckey] = None
        elif content[ckey] is not None:
            content[ckey].append([str(r["player_name"]), str(r["status"])])
    teams: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for (gd, obs, team), players in sorted(
        content.items(), key=lambda kv: (kv[0][0], parse_utc(kv[0][1]), kv[0][2])
    ):
        entry = None if players is None else sorted(players)
        history = teams[gd][team]
        if history and history[-1][1] == entry:
            continue
        history.append([obs, entry])
    return InjuryReportIndex(
        {
            "game_date": gd,
            "reports": [[obs, sorted(f"{a}@{h}" for a, h in m)] for obs, m in by_obs.items()],
            "teams": dict(teams[gd]),
        }
        for gd, by_obs in dates.items()
    )


class _AtTime:
    """Injury index view that answers at a fixed decision time (``game_features`` uses tip - 60)."""

    def __init__(self, index: Any, decision: datetime) -> None:
        self.index = index
        self.decision = decision

    def covers(self, game_date: str) -> bool:
        return bool(self.index.covers(game_date))

    def team_report(self, game_date: str, away: str, home: str, team: str, _when: datetime) -> TeamReport:
        return self.index.team_report(game_date, away, home, team, self.decision)


class _StaticReports:
    """Fixed TeamReports per team tricode (T-C2: the same game under two consecutive reports)."""

    def __init__(self, reports: Mapping[str, TeamReport]) -> None:
        self.reports = reports

    def covers(self, game_date: str) -> bool:
        return True

    def team_report(self, game_date: str, away: str, home: str, team: str, _when: datetime) -> TeamReport:
        return self.reports[team]


# --------------------------------------------------------------------------- prediction
def predict_game(fs: ForwardState, game: GameBox, injuries: Any) -> dict[str, Any]:
    """Margin/total per variant, merging the two half-life states exactly as ``player_fit walk``."""
    merged: dict[str, Any] | None = None
    for market, keys in (("margin", ("S_home", "S_away")), ("total", ("U_home", "U_away"))):
        row = game_features(fs.states[market], game, injuries)
        if merged is None:
            merged = row
        if market == "total":
            merged["total_const"] = row["total_const"]
        for name, variant in row["variants"].items():
            target = merged["variants"].setdefault(name, dict(variant))
            if variant.get("reason") is not None:
                target["reason"] = variant["reason"]
            for k in keys:
                if k in variant:
                    target[k] = variant[k]
    assert merged is not None
    out: dict[str, Any] = {}
    for variant in VARIANTS:
        v = merged["variants"].get(variant)
        if v is None:
            out[variant] = {"reason": "NO_CALL_INJURY_REPORT_MISSING", "margin": None, "total": None}
            continue
        margin, total = predict(merged, fs.coefs[variant]) if v.get("reason") is None else (None, None)
        out[variant] = {
            "reason": v.get("reason"),
            "margin": None if margin is None else round(margin, 4),
            "total": None if total is None else round(total, 4),
            "unmatched_report_rows": v.get("unmatched"),
        }
    out["_features"] = {
        k: merged.get(k) for k in ("home_b2b", "away_b2b", "home_3in4", "away_3in4", "poss_hat", "eff_base")
    }
    return out


def box_feed_reason(fs: ForwardState, home_id: str, away_id: str) -> str | None:
    for team in (home_id, away_id):
        last = fs.last_game.get(team)
        if last is not None and not last[1]:
            return NO_CALL_BOX_FEED_STALE
    return None


# --------------------------------------------------------------------------- ESPN window
def _nickname(display: str) -> str:
    return display.split()[-1].casefold() if display else ""


def espn_records(runtime_root: Path, now: datetime) -> list[dict[str, Any]]:
    """ESPN capture records of the ET dates the 12-hour window touches (ingest order)."""
    days = sorted(
        {
            now.astimezone(EASTERN).date().isoformat(),
            (now + ESPN_WINDOW).astimezone(EASTERN).date().isoformat(),
        }
    )
    out: list[dict[str, Any]] = []
    for day in days:
        out.extend(_read_jsonl(espn_odds_path(runtime_root, day)))
    return out


def window_games(records: Iterable[Mapping[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Latest ESPN capture (observed <= now) of every game tipping in (now, now + 12 h] (max-scan)."""
    latest: dict[str, dict[str, Any]] = {}
    for rec in records:
        try:
            obs = parse_utc(str(rec["observed_at_utc"]))
            tip = parse_utc(str(rec["event_start_utc"]))
        except (KeyError, ValueError):
            continue
        if obs > now or not (now < tip <= now + ESPN_WINDOW):
            continue
        prev = latest.get(str(rec["event_id"]))
        if prev is None or obs > prev["_obs"]:
            latest[str(rec["event_id"])] = {**rec, "_obs": obs, "_tip": tip}
    return sorted(latest.values(), key=lambda r: (r["_tip"], str(r["event_id"])))


def consensus_at(
    records: Iterable[Mapping[str, Any]], event_id: str, t: datetime, sigmas: Mapping[str, float]
) -> dict[str, Any]:
    """PREREG 2: the latest ESPN consensus with observed <= t, at most 15 min old, per market."""
    from .nba_v10_g2 import parse_espn_record

    best: dict[str, Any] = {}
    for rec in records:
        if str(rec.get("event_id")) != event_id:
            continue
        for cap in parse_espn_record(rec):
            if not (t - CONSENSUS_MAX_AGE <= cap.observed_at_utc <= t):
                continue
            cur = best.get(cap.market)
            if cur is None or cap.observed_at_utc > cur.observed_at_utc:
                best[cap.market] = cap
    return {
        market: {
            "observed_at_utc": _iso(cap.observed_at_utc),
            "line": cap.line,
            "p_low": cap.p_low,
            "n_books": cap.n_books,
            "mean": round(anchor_mean(cap.line, cap.p_low, sigmas[market]), 4),
        }
        for market, cap in best.items()
    }


# --------------------------------------------------------------------------- Polymarket mids
def market_quote(
    market: Mapping[str, Any], raw: Mapping[str, Any] | None, home_team: str, away_team: str
) -> dict[str, Any] | None:
    """One normalized Polymarket market as a long-side mid plus the home/over view.

    ``contract_line`` is in the home-margin convention for spreads (home covers when margin > C),
    the points line for totals. ``p_high`` is the mid probability of home covering / over / home
    winning. None unless the event-detail quote is open and two-sided no wider than 10c, and unless
    the long side names exactly one of the two teams (spread, moneyline) or says Over or Under
    (total): a side that cannot be placed is declined, never guessed, since a wrong guess flips it.
    """
    from .data_sources.polymarket_us import _amount, _event_detail_quote_book

    if raw is None:
        return None
    long = next((s for s in market.get("sides") or [] if s.get("is_long")), None)
    if long is None:
        return None
    book = _event_detail_quote_book(dict(raw), market.get("line"))
    if book is None:
        return None
    bid, ask = _amount(book["bestBid"]), _amount(book["bestAsk"])
    if bid is None or ask is None or not 0.0 <= ask - bid <= MID_MAX_WIDTH + 1e-9:
        return None
    mid = (bid + ask) / 2.0
    kind = market.get("market_type")
    long_team = str(long.get("team") or "").casefold()
    is_home = bool(_nickname(home_team)) and long_team.endswith(_nickname(home_team))
    is_away = bool(_nickname(away_team)) and long_team.endswith(_nickname(away_team))
    home_long = is_home
    if kind in ("moneyline", "spread") and is_home == is_away:
        return None
    if kind == "moneyline":
        contract, p_high = None, mid if home_long else 1.0 - mid
    elif kind == "spread":
        line = _finite(long.get("line"))
        if line is None:
            return None
        contract, p_high = (-line, mid) if home_long else (line, 1.0 - mid)
    elif kind == "total":
        line = _finite(market.get("line"))
        if line is None:
            return None
        desc = str(long.get("description") or "").strip().casefold()
        if desc not in ("over", "under"):
            return None
        contract, p_high = line, mid if desc == "over" else 1.0 - mid
    else:
        return None
    return {
        "slug": str(market.get("market_slug")),
        "type": kind,
        "long_team": long.get("team"),
        "long_desc": long.get("description"),
        "bid": bid,
        "ask": ask,
        "mid": round(mid, 6),
        "contract_line": contract,
        "p_high": round(p_high, 6),
    }


def poll_mids(
    games: Sequence[Mapping[str, Any]],
    *,
    client: Any,
    clock: Clock,
    deadline: float | None,
    keep_slugs: frozenset[str] = frozenset(),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One event-detail request per window game; returns (records, summary). Stops on a 429."""
    import httpx

    from .data_sources.polymarket_us import PolymarketUSClient

    summary: dict[str, Any] = {"requests": 0, "matched": 0, "records": 0, "errors": []}
    if not games:
        return [], summary
    events = client.events("NBA")
    summary["requests"] += 1
    records: list[dict[str, Any]] = []
    last: float | None = None
    for g in games:
        if deadline is not None and time.monotonic() >= deadline:
            summary["errors"].append("deadline")
            break
        probe = {
            "event_start_utc": g["event_start_utc"],
            "home_nickname": str(g.get("home_team") or "").split()[-1] if g.get("home_team") else "",
            "away_nickname": str(g.get("away_team") or "").split()[-1] if g.get("away_team") else "",
        }
        event, _reason = match_polymarket_event(probe, events)
        if event is None:
            continue
        summary["matched"] += 1
        if last is not None:
            wait = POLL_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        last = time.monotonic()
        summary["requests"] += 1
        try:
            detail = client.event_detail(str(event.get("slug")))
        except httpx.HTTPStatusError as exc:
            summary["errors"].append(f"HTTP {exc.response.status_code}")
            if exc.response.status_code == 429:
                break  # the gateway limit is IP-wide: leave the rest for the next run
            continue
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            summary["errors"].append(type(exc).__name__)
            continue
        observed = clock()  # after the response
        try:
            normalized = PolymarketUSClient._normalize_event(detail.event, "NBA", "America/New_York")
        except (KeyError, TypeError, ValueError) as exc:
            summary["errors"].append(type(exc).__name__)
            continue
        raw_by_slug = {
            str(m.get("slug")): m for m in detail.event.get("markets") or [] if isinstance(m, dict)
        }
        quotes = []
        for market in normalized.get("markets") or []:
            q = market_quote(
                market,
                raw_by_slug.get(str(market.get("market_slug"))),
                str(g.get("home_team") or ""),
                str(g.get("away_team") or ""),
            )
            if q is None:
                continue
            if (
                q["type"] == "moneyline"
                or q["slug"] in keep_slugs
                or POLL_KEEP_LOW <= q["mid"] <= POLL_KEEP_HIGH
            ):
                quotes.append(q)
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "observed_at_utc": _iso(observed),
                "espn_event_id": str(g["event_id"]),
                "event_slug": str(event.get("slug")),
                "event_start_utc": g["event_start_utc"],
                "home_team": g.get("home_team"),
                "away_team": g.get("away_team"),
                "cache_age_seconds": getattr(detail, "cache_age_seconds", None),
                "markets": quotes,
            }
        )
    summary["records"] = len(records)
    summary["errors"] = summary["errors"][:10]
    return records, summary


def mids_path(out_root: Path, game_date: str) -> Path:
    return out_root / "pm_mids" / f"{game_date}.jsonl"


def balanced(quotes: Iterable[Mapping[str, Any]], kind: str) -> dict[str, Any] | None:
    """The rung whose mid is closest to 50c (ties: smaller absolute line)."""
    pool = [q for q in quotes if q.get("type") == kind and q.get("p_high") is not None]
    if not pool:
        return None
    return dict(
        min(pool, key=lambda q: (abs(q["p_high"] - 0.5), abs(q.get("contract_line") or 0.0), q["slug"]))
    )


def _pm_view(record: Mapping[str, Any] | None, sigmas: Mapping[str, float]) -> dict[str, Any] | None:
    if record is None:
        return None
    out: dict[str, Any] = {"observed_at_utc": record["observed_at_utc"]}
    for kind in ("spread", "total", "moneyline"):
        q = balanced(record.get("markets") or [], kind)
        if q is None:
            continue
        view = {
            "slug": q["slug"],
            "contract_line": q["contract_line"],
            "p_high": q["p_high"],
            "bid": q["bid"],
            "ask": q["ask"],
        }
        if kind != "moneyline" and 0.0 < q["p_high"] < 1.0:
            view["mean"] = round(anchor_mean(q["contract_line"], 1.0 - q["p_high"], sigmas[kind]), 4)
        out[kind] = view
    return out


# --------------------------------------------------------------------------- T-C2
def report_entries(
    rows: Iterable[Mapping[str, Any]], report: Mapping[str, Any], game_date: str, away: str, home: str
) -> dict[str, Any]:
    """Per team tricode of one game in one report: None (game not listed), NOT_SUBMITTED, or
    ``{normalized name: (raw name, status, consistent)}``; plus ``_all_consistent``."""
    key = (str(report["filename"]), str(report["fetched_at_utc"]))
    out: dict[str, Any] = {away: None, home: None, "_all_consistent": True}
    listed = False
    for r in rows:
        if (str(r.get("report_filename")), str(r.get("fetched_at_utc"))) != key:
            continue
        if r.get("game_date") != game_date or r.get("away_tricode") != away or r.get("home_tricode") != home:
            continue
        listed = True
        team = r.get("team_tricode")
        if team not in (away, home):
            out["_all_consistent"] = False
            continue
        if not r.get("consistent", True):
            out["_all_consistent"] = False
        if r.get("status") == NOT_SUBMITTED:
            out[team] = NOT_SUBMITTED
            continue
        if out[team] == NOT_SUBMITTED:
            continue
        if out[team] is None:
            out[team] = {}
        if r.get("player_name"):
            out[team][normalize_name(str(r["player_name"]))] = (
                str(r["player_name"]),
                str(r["status"]),
                bool(r.get("consistent", True)),
            )
    if listed:
        # The game is on the report: a team with no row has submitted with nobody listed (the
        # research index reads it the same way), which is not the same as "game not listed".
        for team in (away, home):
            if out[team] is None:
                out[team] = {}
    return out


def _team_report(entry: Any, observed: str) -> TeamReport:
    if entry is None or entry == NOT_SUBMITTED:
        return TeamReport(None, "NO_CALL_INJURY_NOT_SUBMITTED", observed)
    return TeamReport(
        tuple((raw, normalize_status(status)) for raw, status, _ in entry.values()), None, observed
    )


def status_changes(
    prev: Mapping[str, Any],
    new: Mapping[str, Any],
    team: str,
    *,
    clean_parse: bool,
) -> list[dict[str, Any]]:
    """Status changes of one team between two reports (PREREG 3), before qualification."""
    a, b = prev.get(team), new.get(team)
    if not isinstance(a, dict) or not isinstance(b, dict):
        return []
    out = []
    for norm in sorted(set(a) | set(b)):
        raw = (b.get(norm) or a.get(norm))[0]
        s_prev = normalize_status(a[norm][1]) if norm in a else "unlisted"
        s_new = normalize_status(b[norm][1]) if norm in b else "unlisted"
        if s_prev == s_new:
            continue
        if "unlisted" in (s_prev, s_new) and not clean_parse:
            continue
        if norm in a and norm in b and not (a[norm][2] and b[norm][2]):
            continue
        out.append(
            {
                "team": team,
                "player": raw,
                "from": s_prev,
                "to": s_new,
                "crosses_group": (s_prev in LISTED_GROUP) != (s_new in LISTED_GROUP),
            }
        )
    return out


def qualify(fs: ForwardState, team_id: str, season_year: int, change: dict[str, Any]) -> dict[str, Any]:
    """Attach player id and the last-10 played-game raw minutes; ``qualifying`` per PREREG 3."""
    cands = fs.states["margin"].candidates(team_id, season_year)
    matched, _ = match_report({c.player_id: c.name for c in cands}, [(change["player"], change["to"])])
    pid = next(iter(matched), None) if matched else None
    mpg, n = fs.minutes_last10.get(pid, (None, 0)) if pid else (None, 0)
    return {
        **change,
        "player_id": pid,
        "mpg_last10": None if mpg is None else round(mpg, 2),
        "played_last10": n,
        "qualifying": bool(pid and mpg is not None and n >= TC2_MIN_PLAYED and mpg >= TC2_MIN_MPG),
    }


def _poll_lookup(
    polls: Sequence[Mapping[str, Any]],
    slug: str,
    *,
    after: datetime | None,
    upto: datetime,
    not_before: datetime | None = None,
) -> dict[str, Any] | None:
    """The latest poll quote of ``slug`` with ``after < observed <= upto`` (and >= ``not_before``)."""
    best: tuple[datetime, dict[str, Any]] | None = None
    for rec in polls:
        try:
            obs = parse_utc(str(rec["observed_at_utc"]))
        except (KeyError, ValueError):
            continue
        if (
            obs > upto
            or (after is not None and obs <= after)
            or (not_before is not None and obs < not_before)
        ):
            continue
        q = next((m for m in rec.get("markets") or [] if m.get("slug") == slug), None)
        if q is not None and (best is None or obs > best[0]):
            best = (obs, {**q, "observed_at_utc": _iso(obs)})
    return None if best is None else best[1]


def _game_polls(polls: Sequence[Mapping[str, Any]], event_id: str) -> list[dict[str, Any]]:
    return [dict(rec) for rec in polls if str(rec.get("espn_event_id")) == event_id]


def detect_tc2(
    fs: ForwardState,
    games: Sequence[Mapping[str, Any]],
    reports: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    polls: Sequence[Mapping[str, Any]],
    now: datetime,
    processed: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """New T-C2 event records for reports fetched by ``now`` and not processed yet."""
    usable = sorted(
        (
            r
            for r in reports
            if not r.get("parse_error")
            and r.get("fetched_at_utc")
            and parse_utc(str(r["fetched_at_utc"])) <= now
        ),
        key=lambda r: (parse_utc(str(r["published_at_utc"])), str(r["filename"])),
    )
    # One row per filename (a re-fetch after a killed run): the earliest fetch.
    first: dict[str, dict[str, Any]] = {}
    for r in sorted(usable, key=lambda r: parse_utc(str(r["fetched_at_utc"]))):
        first.setdefault(str(r["filename"]), dict(r))
    ordered = sorted(first.values(), key=lambda r: parse_utc(str(r["published_at_utc"])))
    events: list[dict[str, Any]] = []
    scanned: list[str] = []
    for i, report in enumerate(ordered):
        name = str(report["filename"])
        published = parse_utc(str(report["published_at_utc"]))
        if name in processed or published < now - TC2_SCAN_LOOKBACK:
            continue
        scanned.append(name)
        if i == 0:
            continue
        prev = ordered[i - 1]
        prev_published = parse_utc(str(prev["published_at_utc"]))
        if published - prev_published > TC2_MAX_REPORT_GAP:
            continue
        clean = int(report.get("n_unplaced") or 0) == 0 and int(prev.get("n_unplaced") or 0) == 0
        for g in games:
            event = _tc2_for_game(fs, g, prev, report, rows, polls, clean)
            if event is not None:
                events.append(event)
    return events, scanned


def _tc2_for_game(
    fs: ForwardState,
    g: Mapping[str, Any],
    prev: Mapping[str, Any],
    report: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    polls: Sequence[Mapping[str, Any]],
    clean: bool,
) -> dict[str, Any] | None:
    tip = parse_utc(str(g["event_start_utc"]))
    game_date = tip.astimezone(EASTERN).date().isoformat()
    home, away = TEAM_TRICODES.get(str(g.get("home_team"))), TEAM_TRICODES.get(str(g.get("away_team")))
    home_id, away_id = fs.team_ids.get(str(g.get("home_team"))), fs.team_ids.get(str(g.get("away_team")))
    if not (home and away and home_id and away_id) or game_date != fs.day:
        return None
    a = report_entries(rows, prev, game_date, away, home)
    b = report_entries(rows, report, game_date, away, home)
    clean_game = clean and a["_all_consistent"] and b["_all_consistent"]
    season = season_year_of(game_date)
    changes = []
    for team, team_id in ((home, home_id), (away, away_id)):
        for change in status_changes(a, b, team, clean_parse=clean_game):
            changes.append(qualify(fs, team_id, season, change))
    if not any(c["qualifying"] for c in changes):
        return None
    published = parse_utc(str(report["published_at_utc"]))
    game = GameBox(
        str(g["event_id"]),
        game_date,
        str(g["event_start_utc"]),
        season,
        "regular-season",
        home_id,
        away_id,
        home,
        away,
        0,
        0,
        None,
    )
    before = predict_game(
        fs, game, _StaticReports({t: _team_report(a[t], str(prev["fetched_at_utc"])) for t in (home, away)})
    )
    after = predict_game(
        fs, game, _StaticReports({t: _team_report(b[t], str(report["fetched_at_utc"])) for t in (home, away)})
    )
    deltas = {}
    for m in ("margin", "total"):
        x, y = before["avail"][m], after["avail"][m]
        deltas[m] = None if x is None or y is None else round(y - x, 4)
    game_polls = _game_polls(polls, str(g["event_id"]))
    before_rec = None
    for rec in game_polls:
        obs = parse_utc(str(rec["observed_at_utc"]))
        if published - TC2_BEFORE_MAX_AGE <= obs < published and (
            before_rec is None or obs > parse_utc(str(before_rec["observed_at_utc"]))
        ):
            before_rec = rec
    fixed: dict[str, Any] = {}
    for kind in ("spread", "total", "moneyline"):
        q = balanced((before_rec or {}).get("markets") or [], kind)
        if q is not None:
            fixed[kind] = {**q, "observed_at_utc": before_rec["observed_at_utc"]}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "event",
        "event_key": f"{g['event_id']}|{report['filename']}",
        "espn_event_id": str(g["event_id"]),
        "game_date": game_date,
        "matchup": f"{away}@{home}",
        "event_start_utc": str(g["event_start_utc"]),
        "report_filename": report["filename"],
        "published_at_utc": report["published_at_utc"],
        "fetched_at_utc": report["fetched_at_utc"],
        "prev_report_filename": prev["filename"],
        "prev_published_at_utc": prev["published_at_utc"],
        "prev_fetched_at_utc": prev["fetched_at_utc"],
        "clean_parse": clean_game,
        "changes": changes,
        "model_version": MODEL_VERSION,
        "artifact_hash": fs.artifact_hash,
        "model_before": {v: before[v] for v in VARIANTS},
        "model_after": {v: after[v] for v in VARIANTS},
        "delta": deltas,
        "fixed_rungs_before": fixed,
        "before_reason": None if fixed else "no_before_quote",
        # PREREG amendment: a stale box feed makes the model delta untrustworthy; logged, never scored.
        "reason": box_feed_reason(fs, home_id, away_id),
    }


def followups(
    open_events: Sequence[Mapping[str, Any]], polls_now: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """This run's quotes of every fixed slug of the still-open events."""
    out = []
    for ev in open_events:
        for rec in polls_now:
            if str(rec.get("espn_event_id")) != ev["espn_event_id"]:
                continue
            quotes = {}
            for kind, q in (ev.get("fixed_rungs_before") or {}).items():
                hit = next((m for m in rec.get("markets") or [] if m.get("slug") == q["slug"]), None)
                if hit is not None:
                    quotes[kind] = hit
            out.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "followup",
                    "event_key": ev["event_key"],
                    "observed_at_utc": rec["observed_at_utc"],
                    "minutes_after_published": round(
                        (
                            parse_utc(rec["observed_at_utc"]) - parse_utc(ev["published_at_utc"])
                        ).total_seconds()
                        / 60,
                        1,
                    ),
                    "quotes": quotes,
                }
            )
    return out


# --------------------------------------------------------------------------- orchestration
def in_blackout(now: datetime) -> bool:
    local = now.astimezone(EASTERN)
    minute = local.hour * 60 + local.minute
    return BLACKOUT_ET[0] <= minute <= BLACKOUT_ET[1]


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def run_forward(
    cfg: ForwardConfig,
    *,
    deadline: float,
    clock: Clock | None = None,
    http_get: Callable[[str], tuple[int, bytes]] | None = None,
    pm_client: Any | None = None,
) -> dict[str, Any]:
    """All forward steps; never raises. ``deadline`` is a ``time.monotonic()`` value."""
    clock = clock or (lambda: datetime.now(UTC))
    out: dict[str, Any] = {"status": "ok"}

    def _left() -> float:
        return deadline - time.monotonic()

    def _step(name: str, fn: Callable[[], Any]) -> Any:
        if _left() <= 0:
            out[name] = {"status": "skipped_deadline"}
            return None
        started = time.monotonic()
        try:
            result = fn()
        except Exception as exc:  # each research step is isolated from the next
            _logger.warning("nba v10 forward step %s failed", name, exc_info=True)
            out[name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:200]}
            result = None
        else:
            if not isinstance(out.get(name), dict):
                out[name] = {"status": "ok"}
        out[name]["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return result

    out["injury"] = None
    _step(
        "injury",
        lambda: out.__setitem__(
            "injury",
            nba_injury_live.capture_injury_reports(
                cfg.injury_root, now=clock, http_get=http_get, deadline=deadline
            ),
        ),
    )

    start_now = clock()
    espn = espn_records(cfg.runtime_root, start_now)
    games = window_games(espn, start_now)
    out["window_games"] = len(games)
    today = start_now.astimezone(EASTERN).date().isoformat()

    open_events: list[dict[str, Any]] = []
    tc2_path = cfg.out_root / "tc2_events.jsonl"
    for rec in _read_jsonl(tc2_path):
        if rec.get("kind") == "event":
            try:
                if parse_utc(str(rec["published_at_utc"])) >= start_now - TC2_FOLLOW:
                    open_events.append(rec)
            except (KeyError, ValueError):
                continue
    keep = frozenset(q["slug"] for ev in open_events for q in (ev.get("fixed_rungs_before") or {}).values())

    polls_now: list[dict[str, Any]] = []

    def _poll() -> None:
        nonlocal polls_now
        import httpx

        from .data_sources.polymarket_us import PolymarketUSClient

        client = pm_client or PolymarketUSClient(client=httpx.Client(timeout=10.0), max_attempts=1)
        polls_now, summary = poll_mids(games, client=client, clock=clock, deadline=deadline, keep_slugs=keep)
        for rec in polls_now:
            day = parse_utc(rec["event_start_utc"]).astimezone(EASTERN).date().isoformat()
            _append(mids_path(cfg.out_root, day), [rec])
        out["polymarket"] = {"status": "ok", **summary}

    if games or open_events:
        _step("polymarket", _poll)

    if in_blackout(start_now):
        out["player"] = {"status": "skipped_blackout"}
        out["tc2"] = {"status": "skipped_blackout"}
        return out

    fs_box: dict[str, ForwardState] = {}

    def _state() -> None:
        fs, hit = load_or_build_state(cfg, today, seconds_left=_left)
        if fs is None:
            out["state"] = {"status": "skipped_cold_build_budget"}
            return
        fs_box["fs"] = fs
        out["state"] = {"status": "ok", "cache_hit": hit, **fs.fingerprint}

    if games or open_events:
        _step("state", _state)
    fs = fs_box.get("fs")
    if fs is None:
        return out

    sigmas = anchor_sigmas(cfg.repo_root)
    report_dates = sorted({today, (date.fromisoformat(today) - timedelta(days=1)).isoformat()})

    def _player() -> None:
        reports = nba_injury_live.load_reports(cfg.injury_root, report_dates)
        rows = nba_injury_live.load_rows(cfg.injury_root, report_dates)
        espn_now = espn_records(cfg.runtime_root, start_now)
        # "Now" is stamped after every slow load above (state, polls, report store), so nothing
        # observed after it can be in the inputs, and every input observed before it can.
        t = clock()
        index = live_injury_index(reports, rows, t)
        valid = _valid_reports(reports, t)
        latest_report = max(valid.values(), key=lambda r: parse_utc(str(r["published_at_utc"])), default=None)
        records = []
        for g in games:
            records.append(_player_record(fs, g, t, index, latest_report, polls_now, sigmas, espn_now))
        out["player"] = {
            "status": "ok",
            "records": _append(cfg.out_root / "player_log.jsonl", records),
            "with_avail": sum(1 for r in records if r["variants"].get("avail", {}).get("margin") is not None),
            "reasons": sorted(
                {str(r["reason"] or r["variants"]["avail"]["reason"]) for r in records} - {"None"}
            ),
        }

    _step("player", _player)

    def _tc2() -> None:
        reports = nba_injury_live.load_reports(cfg.injury_root, report_dates)
        rows = nba_injury_live.load_rows(cfg.injury_root, report_dates)
        state_path = cfg.out_root / "tc2_state.json"
        processed = set(_load_json(state_path, {}).get("processed", []))
        now = clock()
        polls = []
        for day in sorted(
            {today, *(parse_utc(g["event_start_utc"]).astimezone(EASTERN).date().isoformat() for g in games)}
        ):
            polls.extend(_read_jsonl(mids_path(cfg.out_root, day)))
        events, scanned = detect_tc2(fs, games, reports, rows, polls, now, processed)
        for ev in events:
            ev["detected_at_utc"] = _iso(now)
            ev["detection_quotes"] = {
                kind: _poll_lookup(polls_now, q["slug"], after=None, upto=now)
                for kind, q in (ev.get("fixed_rungs_before") or {}).items()
            }
        written = _append(tc2_path, events)
        written_f = _append(tc2_path, followups(open_events, polls_now))
        keep_recent = sorted(processed | set(scanned))[-1000:]
        _write_json_atomic(state_path, {"processed": keep_recent, "updated_at_utc": _iso(now)})
        out["tc2"] = {
            "status": "ok",
            "reports_scanned": len(scanned),
            "events": written,
            "followups": written_f,
        }

    _step("tc2", _tc2)
    return out


def _player_record(
    fs: ForwardState,
    g: Mapping[str, Any],
    t: datetime,
    index: InjuryReportIndex,
    latest_report: Mapping[str, Any] | None,
    polls_now: Sequence[Mapping[str, Any]],
    sigmas: Mapping[str, float],
    espn: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    tip = parse_utc(str(g["event_start_utc"]))
    game_date = tip.astimezone(EASTERN).date().isoformat()
    home_name, away_name = str(g.get("home_team") or ""), str(g.get("away_team") or "")
    home_id, away_id = fs.team_ids.get(home_name), fs.team_ids.get(away_name)
    home_tc, away_tc = TEAM_TRICODES.get(home_name), TEAM_TRICODES.get(away_name)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": _iso(t),
        "event_id": str(g["event_id"]),
        "event_start_utc": str(g["event_start_utc"]),
        "minutes_to_tip": round((tip - t).total_seconds() / 60, 1),
        "home_team": home_name,
        "away_team": away_name,
        "model_version": MODEL_VERSION,
        "artifact_hash": fs.artifact_hash,
        "state_day": fs.day,
        "reason": None,
        "variants": {},
        "box_missing_last5": {
            "home": fs.recent_missing.get(home_id or "", None),
            "away": fs.recent_missing.get(away_id or "", None),
        },
        "consensus": consensus_at(espn, str(g["event_id"]), t, sigmas),
        "polymarket": _pm_view(
            next((r for r in polls_now if r["espn_event_id"] == str(g["event_id"])), None), sigmas
        ),
        "injury_report": None
        if latest_report is None
        else {k: latest_report.get(k) for k in ("filename", "published_at_utc", "fetched_at_utc", "sha256")},
    }
    if not (home_id and away_id and home_tc and away_tc):
        record["reason"] = NO_CALL_TEAM_UNMAPPED
    elif game_date != fs.day:
        record["reason"] = NO_CALL_NOT_TODAY
    else:
        record["reason"] = box_feed_reason(fs, home_id, away_id)
    if record["reason"] is not None:
        record["variants"] = {
            v: {"reason": record["reason"], "margin": None, "total": None} for v in VARIANTS
        }
        return record
    game = GameBox(
        str(g["event_id"]),
        game_date,
        str(g["event_start_utc"]),
        season_year_of(game_date),
        "regular-season",
        home_id,
        away_id,
        home_tc,
        away_tc,
        0,
        0,
        None,
    )
    pred = predict_game(fs, game, _AtTime(index, t))
    record["features"] = pred.pop("_features")
    record["variants"] = pred
    return record


# --------------------------------------------------------------------------- isolation
def run_isolated(fn: Callable[[], dict[str, Any]], timeout_seconds: float) -> dict[str, Any]:
    """Run ``fn`` in a daemon thread; return its result, or a timeout/error marker. Never raises.

    A daemon thread, so a step that hangs past the budget cannot hold the worker past its
    supervisor timeout: the caller prints and exits while it is still running.
    """
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 -- anything, including SystemExit, stays here
            box["error"] = f"{type(exc).__name__}: {exc}"[:200]

    started = time.monotonic()
    thread = threading.Thread(target=_target, name="nba-v10-forward", daemon=True)
    thread.start()
    thread.join(max(0.0, timeout_seconds))
    elapsed = round(time.monotonic() - started, 2)
    if thread.is_alive():
        return {"status": "timeout", "elapsed_seconds": elapsed, "budget_seconds": round(timeout_seconds, 1)}
    if "error" in box:
        return {"status": "error", "error": box["error"], "elapsed_seconds": elapsed}
    result = dict(box.get("result") or {})
    result["elapsed_seconds"] = elapsed
    return result


__all__ = [
    "ForwardConfig",
    "build_state",
    "detect_tc2",
    "live_injury_index",
    "market_quote",
    "poll_mids",
    "qualify",
    "run_forward",
    "run_isolated",
    "status_changes",
    "summary_box_rows",
    "window_games",
]
