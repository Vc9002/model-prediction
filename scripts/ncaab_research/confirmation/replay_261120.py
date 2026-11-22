"""Point-in-time replay of v6 and the served v7 ridge for the protocol-261120 confirmation window.

Research only (quick 261120-nip, confirmation lane). Reuses the development replay unchanged: the per-day
component computation is ``NCAABV7MultiSeasonModel.components`` driven exactly as
``scripts/ncaab_v7_multiseason.py::_worker`` drives it (history = every completed game that tipped strictly
before Eastern midnight of the game day, ``len(history) >= 20``, the as-of-season conference map). The only
differences are parameters: which event list, which stats root, which target seasons, which days. The
reproduction gate runs this same function on the development inputs and must match ``v7ms_components.jsonl``.

History for the confirmation replay (season years 2014..2022; 2014 burn-in, 2021 history only):
    2016..2022  ESPN backfill scoreboard (data/research/ncaab_market/backfill_261120/scoreboard.jsonl)
    2014, 2015  CBBD cache only (data/research/ncaab_external/cbbd/, zero API calls), converted to the ESPN
                scoreboard event shape keyed on ESPN ids (CBBD ``sourceId``), team display names from the
                backfill by ESPN team id, conference ids through a CBBD -> ESPN crosswalk derived on the
                2016..2021 overlap (unanimous, fail closed), box stats from games__teams. CBBD carries no AP
                rank, so the rank prior is absent in 2014-15 (disclosed in the pin).

Outcome-free: this module never joins a result to a prediction; scores enter only as rating history, exactly
as in the served replay. Writes only under outputs/ncaab/research_261120/confirmation/.

    OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/confirmation/replay_261120.py audit
    OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/confirmation/replay_261120.py gate
    OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/confirmation/replay_261120.py run
"""

from __future__ import annotations

import argparse
import bisect
import glob
import json
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
REPO = Path(__file__).resolve().parents[3]
for _p in (REPO / "src", REPO / "scripts", REPO / "scripts" / "ncaab_research"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ncaab_residual as nr

BACKFILL_SCOREBOARD = REPO / "data/research/ncaab_market/backfill_261120/scoreboard.jsonl"
CBBD_DIR = REPO / "data/research/ncaab_external/cbbd"
OUT = REPO / "outputs/ncaab/research_261120/confirmation"
CACHE = OUT / "cache"
STATS_ROOT_CONF = CACHE / "raw_stats"
COMPONENTS_DIR = OUT / "components"
AUDIT_JSON = OUT / "history_audit.json"
GATE_JSON_FMT = "reproduction_gate_{season}.json"  # one file per gated development season

CBBD_SEASONS = (2014, 2015)
BACKFILL_SEASONS = (2016, 2017, 2018, 2019, 2020, 2021, 2022)
CONFIRMATION_SEASONS = (2015, 2016, 2017, 2018, 2019, 2020, 2022)
# Each day is computed from the raw history alone (no state carries between replayed days), so only the
# scored seasons need replaying; 2014 and 2021 enter purely as history.
REPLAY_SEASONS = CONFIRMATION_SEASONS
MIN_HISTORY = 20  # as _worker
STUB_MAX_GAMES = 3  # a team-season with a conference id in <= 3 scoreboard games is a non-D1 opponent
# CBBD conference labels for D1 independents: ESPN carries them under id 43 (UNMAPPED_D1_IDS, non-D1 prior)
CBBD_INDEPENDENT_LABELS = frozenset({"Indep."})
ESPN_INDEPENDENT_ID = "43"
CBBD_SEASON_TYPE = {"regular": "regular-season", "postseason": "post-season"}
BOX_FIELDS = {  # ESPN scoreboard stat name -> path in CBBD teamStats
    "fieldGoalsAttempted": ("fieldGoals", "attempted"),
    "fieldGoalsMade": ("fieldGoals", "made"),
    "threePointFieldGoalsAttempted": ("threePointFieldGoals", "attempted"),
    "threePointFieldGoalsMade": ("threePointFieldGoals", "made"),
    "freeThrowsAttempted": ("freeThrows", "attempted"),
    "freeThrowsMade": ("freeThrows", "made"),
    "rebounds": ("rebounds", "total"),
    "assists": ("assists",),
}


# ------------------------------------------------------------------------------------------ pure helpers
def cbbd_start_utc(start: str) -> str:
    """CBBD '2014-11-14T16:00:00.000Z' -> the scoreboard's '2014-11-14T16:00Z'."""
    dt = datetime.strptime(start[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def in_scoreboard_window(season_year: int, et_day: str) -> bool:
    """The backfill fetched Nov 1 .. Apr 10 of each season; CBBD seasons are cut to the same window."""
    return f"{season_year - 1}-11-01" <= et_day <= f"{season_year}-04-10"


def first_by_event(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """First occurrence per event id, sorted by (start_utc, event_id) as nr.load_scoreboard_events."""
    seen: dict[str, dict[str, Any]] = {}
    for e in events:
        seen.setdefault(str(e["event_id"]), e)
    return sorted(seen.values(), key=lambda e: (e["start_utc"], e["event_id"]))


def unanimous_crosswalk(pairs: Iterable[tuple[str, str | None]]) -> tuple[dict[str, str], dict[str, Any]]:
    """CBBD label -> ESPN id from observed (label, id) pairs; a label seen with two ids is a conflict."""
    seen: dict[str, Counter[str | None]] = defaultdict(Counter)
    for label, cid in pairs:
        seen[label][cid] += 1
    out: dict[str, str] = {}
    conflicts: dict[str, Any] = {}
    for label, c in seen.items():
        ids = {k for k in c if k is not None}
        if len(ids) == 1:
            out[label] = next(iter(ids))
        elif len(ids) > 1:
            conflicts[label] = {str(k): v for k, v in c.items()}
    return out, conflicts


def _dig(d: dict[str, Any], path: tuple[str, ...]) -> Any:
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)  # type: ignore[assignment]
    return d


def _scoreboard_records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("http_status") != 200:
                continue
            for ev in rec["events"]:
                yield {**ev, "sb_date": rec["date"]}


# ------------------------------------------------------------------------------------------ history sources
def load_backfill_events() -> list[dict[str, Any]]:
    """Completed backfill events, first occurrence per id (the nr.load_scoreboard_events rule)."""
    out = []
    for ev in _scoreboard_records(BACKFILL_SCOREBOARD):
        if not ev["completed"] or ev["home_score"] is None or ev["away_score"] is None:
            continue
        out.append(ev)
    return first_by_event(out)


def _cbbd_rows(kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(glob.glob(str(CBBD_DIR / kind / "*.json"))):
        if p.endswith(".meta.json"):
            continue
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        rows.extend(data if isinstance(data, list) else data.get("data", []))
    return rows


def espn_team_names(backfill: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """ESPN team id -> display name from the backfill. An id ESPN renamed inside 2016-22 takes its EARLIEST
    name (chronologically adjacent to 2014-15); such ids are returned apart for the audit."""
    names: dict[str, set[str]] = defaultdict(set)
    earliest: dict[str, tuple[str, str]] = {}
    for ev in backfill:
        for side in ("home", "away"):
            tid = ev.get(f"{side}_team_id")
            if tid is not None:
                name = str(ev[f"{side}_team"])
                names[str(tid)].add(name)
                if str(tid) not in earliest or ev["start_utc"] < earliest[str(tid)][0]:
                    earliest[str(tid)] = (ev["start_utc"], name)
    multi = {t: sorted(n) for t, n in names.items() if len(n) > 1}
    return {t: v[1] for t, v in earliest.items()}, multi


def cbbd_conference_crosswalk(
    backfill: list[dict[str, Any]], games: list[dict[str, Any]]
) -> tuple[dict[str, str], dict[str, Any]]:
    """CBBD per-game conference label -> ESPN conference id, from events present in both (2016..2021)."""
    by_id = {str(e["event_id"]): e for e in backfill}
    pairs: list[tuple[str, str | None]] = []
    for g in games:
        ev = by_id.get(str(g.get("sourceId")))
        if ev is None:
            continue
        for cside, eside in (("home", "home"), ("away", "away")):
            label = g.get(f"{cside}Conference")
            if label is not None:
                pairs.append((str(label), ev.get(f"{eside}_conference_id")))
    xw, conflicts = unanimous_crosswalk(pairs)
    for label in CBBD_INDEPENDENT_LABELS:
        xw.setdefault(label, ESPN_INDEPENDENT_ID)
    return xw, conflicts


def convert_cbbd(
    games: list[dict[str, Any]],
    team_rows: list[dict[str, Any]],
    box_rows: list[dict[str, Any]],
    espn_names: dict[str, str],
    conf_xw: dict[str, str],
    seasons: Iterable[int] = CBBD_SEASONS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """CBBD games of ``seasons`` -> ESPN scoreboard-shaped events. Fails closed (drops and counts) on a game
    without an ESPN id, without a resolvable team, or with a conference label that has no ESPN id."""
    seasons = set(seasons)
    team_src = {int(t["id"]): t for t in team_rows}
    box: dict[tuple[int, bool], dict[str, Any]] = {}
    for r in box_rows:
        box[(int(r["gameId"]), bool(r["isHome"]))] = r.get("teamStats") or {}
    drops: Counter[str] = Counter()
    name_source: Counter[str] = Counter()
    out: list[dict[str, Any]] = []
    for g in games:
        if int(g["season"]) not in seasons:
            continue
        if g.get("status") != "final" or g.get("homePoints") is None or g.get("awayPoints") is None:
            drops["not_final"] += 1
            continue
        start = cbbd_start_utc(g["startDate"])
        day = nr.et_day(start)
        if not in_scoreboard_window(int(g["season"]), day):
            drops["outside_nov1_apr10_window"] += 1
            continue
        if not g.get("sourceId"):
            drops["no_espn_event_id"] += 1
            continue
        ev: dict[str, Any] = {
            "event_id": str(g["sourceId"]),
            "start_utc": start,
            "sb_date": day,
            "season_year": int(g["season"]),
            "season_type": CBBD_SEASON_TYPE.get(str(g["seasonType"]), str(g["seasonType"])),
            "competition_type": str(g["gameType"]),
            "neutral": bool(g["neutralSite"]),
            "conference_competition": bool(g.get("conferenceGame")),
            "completed": True,
            "home_score": int(g["homePoints"]),
            "away_score": int(g["awayPoints"]),
        }
        ok = True
        for side in ("home", "away"):
            t = team_src.get(int(g[f"{side}TeamId"]))
            tid = str(t["sourceId"]) if t and t.get("sourceId") else None
            if tid is None:
                drops["team_without_espn_id"] += 1
                ok = False
                break
            if tid in espn_names:
                name = espn_names[tid]
                name_source["espn_backfill"] += 1
            else:
                name = str(t["displayName"])  # type: ignore[index]
                name_source["cbbd_display_name"] += 1
            label = g.get(f"{side}Conference")
            if label is None:
                cid = None  # non-D1 opponent
            elif str(label) in conf_xw:
                cid = conf_xw[str(label)]
            else:
                drops[f"unknown_conference:{label}"] += 1
                ok = False
                break
            ev[f"{side}_team"] = name
            ev[f"{side}_team_id"] = tid
            ev[f"{side}_conference_id"] = cid
            ev[f"{side}_ap_rank"] = None  # CBBD has no AP rank (disclosed)
            stats = box.get((int(g["id"]), side == "home"), {})
            for espn_name, path in BOX_FIELDS.items():
                v = _dig(stats, path)
                ev[f"{side}_{espn_name}"] = float(v) if isinstance(v, int | float) else None
        if ok:
            out.append(ev)
    return first_by_event(out), {"dropped": dict(drops), "team_name_source": dict(name_source)}


def demote_non_d1_stubs(
    events: list[dict[str, Any]], max_games: int = STUB_MAX_GAMES
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Clear the conference id of a team-season that carries one in at most ``max_games`` scoreboard games.

    ESPN stamps a non-D1 opponent with its CURRENT conference (Lindenwood 2018-19 as OVC, Queens 2020-21 as
    ASUN), so the scoreboard alone would make it D1 in a season it was Division II. A real D1 team plays
    15+ games in every non-COVID season (and 5+ in 2020-21); the stubs carry 1-3. CBBD (as-of per game)
    agrees each stub was non-D1 where it overlaps. Returns (events, demoted team-seasons).
    """
    cnt: Counter[tuple[int, str]] = Counter()
    for e in events:
        for side in ("home", "away"):
            if e.get(f"{side}_conference_id") is not None:
                cnt[(int(e["season_year"]), str(e[f"{side}_team"]))] += 1
    stubs = {k for k, n in cnt.items() if n <= max_games}
    out = []
    for e in events:
        e2 = e
        for side in ("home", "away"):
            if (int(e["season_year"]), str(e[f"{side}_team"])) in stubs:
                e2 = {**e2, f"{side}_conference_id": None}
        out.append(e2)
    demoted = [{"season_year": s, "team": t, "games": cnt[(s, t)]} for s, t in sorted(stubs)]
    return out, demoted


def load_confirmation_history() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Season years 2014..2022: CBBD-converted 2014-15 + ESPN backfill 2016-22, sorted, one row per id."""
    backfill = [e for e in load_backfill_events() if int(e["season_year"]) in BACKFILL_SEASONS]
    games = _cbbd_rows("games")
    names, multi = espn_team_names(backfill)
    xw, conflicts = cbbd_conference_crosswalk(backfill, games)
    if conflicts:
        raise ValueError(f"CBBD conference labels map to more than one ESPN id: {conflicts}")
    cbbd, info = convert_cbbd(games, _cbbd_rows("teams"), _cbbd_rows("games__teams"), names, xw)
    overlap = {e["event_id"] for e in cbbd} & {e["event_id"] for e in backfill}
    if overlap:
        raise ValueError(f"{len(overlap)} CBBD 2014-15 events also in the 2016+ backfill")
    events, demoted = demote_non_d1_stubs(first_by_event([*cbbd, *backfill]))
    meta = {
        "non_d1_stubs_demoted": demoted,
        "cbbd_conversion": info,
        "conference_crosswalk": xw,
        "espn_team_ids_with_several_names": multi,
        "events_by_season": dict(sorted(Counter(int(e["season_year"]) for e in events).items())),
    }
    return events, meta


# ------------------------------------------------------------------------------------------ audit (outcome-free)
def audit() -> dict[str, Any]:
    """Outcome-free checks of the CBBD conversion on the 2016..2021 overlap and of the as-of map."""
    from model_prediction.models.ncaab_v7_multiseason import (
        CONFERENCE_ID_NAMES,
        UNMAPPED_D1_IDS,
        build_asof_conferences,
    )

    backfill = [e for e in load_backfill_events() if int(e["season_year"]) in BACKFILL_SEASONS]
    games = _cbbd_rows("games")
    names, multi = espn_team_names(backfill)
    xw, conflicts = cbbd_conference_crosswalk(backfill, games)
    overlap_seasons = (2016, 2017, 2018, 2019, 2020, 2021)
    conv, conv_info = convert_cbbd(
        games, _cbbd_rows("teams"), _cbbd_rows("games__teams"), names, xw, overlap_seasons
    )
    by_id = {e["event_id"]: e for e in backfill}
    agree: dict[str, Counter[str]] = defaultdict(Counter)
    identity = ("home_team", "away_team", "neutral", "start_utc", "home_conference_id", "away_conference_id")
    identity += ("season_type", "competition_type", "conference_competition", "season_year")
    for c in conv:
        e = by_id.get(c["event_id"])
        if e is None:
            agree["_"]["cbbd_only"] += 1
            continue
        agree["_"]["both"] += 1
        for f in identity:
            agree[f]["same" if c[f] == e[f] else "diff"] += 1
        # box stats compared on the history-only season 2021 only (no confirmation-season field is compared)
        if int(c["season_year"]) == 2021:
            for side in ("home", "away"):
                for b in BOX_FIELDS:
                    cv, ev = c.get(f"{side}_{b}"), e.get(f"{side}_{b}")
                    if cv is None or ev is None:
                        agree["box_2021"]["missing"] += 1
                    else:
                        agree["box_2021"]["same" if abs(cv - ev) < 1e-9 else "diff"] += 1
    conv_ids = {c["event_id"] for c in conv}
    espn_only = Counter(int(e["season_year"]) for e in backfill if e["event_id"] not in conv_ids)
    events, meta = load_confirmation_history()
    asof = build_asof_conferences(events)  # raises on a team with two ids in one season
    unknown = {
        s: sorted({c for c in teams.values() if c not in CONFERENCE_ID_NAMES and c not in UNMAPPED_D1_IDS})
        for s, teams in asof.items()
    }
    movers = {}
    for team_part in ("Louisville", "Maryland", "Rutgers", "Wichita State", "UConn", "NJIT", "Creighton"):
        movers[team_part] = {
            s: sorted({cid for t, cid in teams.items() if t.startswith(team_part + " ")})
            for s, teams in sorted(asof.items())
        }
    d1_by_season = {s: len(t) for s, t in sorted(asof.items())}
    # each demoted stub inside the CBBD overlap must be non-D1 (null conference) in CBBD's as-of record
    team_src = {int(t["id"]): t for t in _cbbd_rows("teams")}
    cbbd_label: dict[tuple[int, str], set[str | None]] = defaultdict(set)
    for g in games:
        for side in ("home", "away"):
            t = team_src.get(int(g[f"{side}TeamId"]))
            if t and t.get("sourceId") and str(t["sourceId"]) in names:
                cbbd_label[(int(g["season"]), names[str(t["sourceId"])])].add(g.get(f"{side}Conference"))
    stub_check = [
        {**d, "cbbd_labels": sorted(map(str, cbbd_label.get((d["season_year"], d["team"]), set())))}
        for d in meta["non_d1_stubs_demoted"]
    ]
    out = {
        "conference_crosswalk": xw,
        "crosswalk_conflicts": conflicts,
        "espn_team_ids_with_several_names": multi,
        "overlap_identity_agreement": {k: dict(v) for k, v in agree.items()},
        "espn_backfill_events_without_cbbd_counterpart_by_season": dict(sorted(espn_only.items())),
        "overlap_conversion_info": conv_info,
        "confirmation_history": meta,
        "asof_unknown_conference_ids_by_season": {s: u for s, u in unknown.items() if u},
        "asof_d1_teams_by_season": d1_by_season,
        "non_d1_stubs_demoted_with_cbbd_labels": stub_check,
        "conference_movers_spot_check": movers,
        "ap_rank_present_by_season": dict(
            sorted(
                Counter(
                    int(e["season_year"])
                    for e in events
                    if e.get("home_ap_rank") is not None or e.get("away_ap_rank") is not None
                ).items()
            )
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    AUDIT_JSON.write_text(json.dumps(out, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return out


# ------------------------------------------------------------------------------------------ the replay
class Replay:
    """``_worker``'s loop, parameterised by event list, stats root and target seasons (code otherwise equal)."""

    def __init__(self, events: list[dict[str, Any]], stats_root: Path) -> None:
        from model_prediction.domain import EASTERN
        from model_prediction.models.ncaab_v7_multiseason import (
            NCAABV7MultiSeasonModel,
            build_asof_conferences,
        )

        self.eastern = EASTERN
        games = nr.to_game_records(events)
        order = sorted(range(len(games)), key=lambda i: games[i].start)
        self.games = [games[i] for i in order]
        self.starts = [g.start for g in self.games]
        self.neutral = {e["event_id"]: bool(e["neutral"]) for e in events}
        self.by_day: dict[str, list[Any]] = defaultdict(list)
        for g in self.games:
            self.by_day[g.start.astimezone(EASTERN).date().isoformat()].append(g)
        self.model = NCAABV7MultiSeasonModel(
            build_asof_conferences(events), stats_root=stats_root, asof_map=True
        )

    def days(self, seasons: Iterable[int]) -> list[str]:
        s = set(seasons)
        return sorted(d for d, gs in self.by_day.items() if any(int(g.season_year or 0) in s for g in gs))

    def day_rows(self, day: str, seasons: Iterable[int]) -> list[dict[str, Any]]:
        from model_prediction.models.basketball import UpcomingGame

        s = set(seasons)
        cutoff = datetime.combine(date.fromisoformat(day), dtime.min, tzinfo=self.eastern)
        hist = self.games[: bisect.bisect_left(self.starts, cutoff)]
        todays = [g for g in self.by_day.get(day, []) if int(g.season_year or 0) in s]
        if len(hist) < MIN_HISTORY or not todays:
            return []
        up = [
            UpcomingGame(
                g.event_id,
                g.event_start_utc,
                g.away_team,
                g.home_team,
                league="NCAAB",
                neutral_site=self.neutral[g.event_id],
            )
            for g in todays
        ]
        by_id = {g.event_id: g for g in todays}
        rows = []
        for row in self.model.components(hist, up):
            g = by_id[row["event_id"]]
            rows.append(
                {**row, "day": day, "season_year": int(g.season_year or 0), "start": g.event_start_utc}
            )
        return rows


def ensure_stats_root(events: list[dict[str, Any]], root: Path) -> int:
    """Scoreboard-shaped stats files for ``events`` under ``root`` (nr.build_stats_root, redirected)."""
    marker = root / "_built.json"
    if marker.exists() and json.loads(marker.read_text())["n_events"] == len(events):
        return 0
    old = nr.STATS_ROOT
    nr.STATS_ROOT = root
    try:
        n = nr.build_stats_root(events)
    finally:
        nr.STATS_ROOT = old
    marker.write_text(json.dumps({"n_events": len(events), "n_days": n}), encoding="utf-8")
    return n


def run_replay(
    replay: Replay, seasons: Iterable[int], out_path: Path, days: list[str] | None = None, log_every: int = 10
) -> int:
    """Append rows per day to ``out_path`` with a done-days sidecar, so an interrupted run resumes."""
    done_path = out_path.with_suffix(".done.json")
    done = set(json.loads(done_path.read_text())) if done_path.exists() else set()
    todo = [d for d in (days or replay.days(seasons)) if d not in done]
    t0, n = time.time(), 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for i, day in enumerate(todo, 1):
        rows = replay.day_rows(day, seasons)
        with out_path.open("a", encoding="utf-8") as fh:
            for r in sorted(rows, key=lambda r: (r["start"], r["event_id"])):
                fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
        done.add(day)
        done_path.write_text(json.dumps(sorted(done)), encoding="utf-8")
        n += len(rows)
        if i % log_every == 0 or i == len(todo):
            print(f"{out_path.name}: {i}/{len(todo)} days, {n} rows, {time.time() - t0:.0f}s", flush=True)
    return n


def read_components(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out.setdefault(str(r["event_id"]), r)
    return out


# ------------------------------------------------------------------------------------------ reproduction gate
GATE_FIELDS = ("v6_mean", "v6_scale", "v6_p_home", "sample", "d1_home", "d1_away", "rg_c010_l02_k14")


def cmd_gate(args: argparse.Namespace) -> int:
    """Replay development days with the development inputs; compare to v7ms_components and the v7 export."""
    import grader
    import numpy as np

    from model_prediction.models.ncaab_v7_serving import combine_f1

    events = [
        e
        for e in nr.load_scoreboard_events()
        if not grader.is_locked(e["season_year"], nr.et_day(e["start_utc"]))
    ]
    replay = Replay(events, nr.STATS_ROOT)
    season = int(args.season)
    days = replay.days([season])[:: args.stride]
    out_path = CACHE / f"gate_components_{season}_stride{args.stride}.jsonl"
    run_replay(replay, [season], out_path, days)
    got = read_components(out_path)
    ref = {}
    for rec in grader._iter_jsonl(grader.COMPONENTS):
        if str(rec["event_id"]) in got:
            ref[str(rec["event_id"])] = rec
    diffs: dict[str, float] = {}
    mism: Counter[str] = Counter()
    for eid, r in got.items():
        q = ref.get(eid)
        if q is None:
            mism["missing_in_reference"] += 1
            continue
        for f in GATE_FIELDS:
            a, b = r.get(f), q.get(f)
            if a is None or b is None:
                if (a is None) != (b is None):
                    mism[f"{f}_none_mismatch"] += 1
                continue
            d = abs(float(a) - float(b))
            diffs[f] = max(diffs.get(f, 0.0), d)
    # v7 served mean / scale / moneyline against the grader export definition
    exp_p = Path(args.export).resolve()
    exp = {}
    for line in exp_p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            exp[str(e["event_id"])] = e
    vmax = {"mu": 0.0, "scale": 0.0, "p_ml": 0.0}
    n_v7 = 0
    for eid, r in got.items():
        e = exp.get(eid)
        if e is None or r.get("rg_c010_l02_k14") is None:
            continue
        mu, sc = combine_f1(float(r["rg_c010_l02_k14"]), float(r["v6_mean"]), int(r["sample"]))
        elo = grader.reconstruct_elo_win(
            np.array([r["v6_p_home"]]), np.array([r["v6_mean"]]), np.array([r["v6_scale"]])
        )
        pm = grader.p_home_from_margin(np.array([mu]), np.array([sc]), np.array(["t8"]))
        p = float(np.clip(0.5 * elo + 0.5 * pm, 0.0001, 0.9999)[0])
        vmax["mu"] = max(vmax["mu"], abs(mu - e["mu_margin"]))
        vmax["scale"] = max(vmax["scale"], abs(sc - e["scale_margin"]))
        vmax["p_ml"] = max(vmax["p_ml"], abs(p - e["p_home_ml"]))
        n_v7 += 1
    tol = {"v6_mean": 1e-9, "v6_scale": 1e-9, "v6_p_home": 1e-9, "sample": 0, "rg_c010_l02_k14": 1e-9}
    tol.update({"d1_home": 0, "d1_away": 0})
    comp_ok = not mism and all(diffs.get(f, 0.0) <= tol[f] for f in GATE_FIELDS)
    v7_ok = n_v7 > 0 and vmax["mu"] < 1e-6 and vmax["scale"] < 1e-9 and vmax["p_ml"] < 1e-6
    res = {
        "season": season,
        "stride": args.stride,
        "days": len(days),
        "rows_replayed": len(got),
        "rows_in_reference": len(ref),
        "component_max_abs_diff": diffs,
        "component_mismatch_counts": dict(mism),
        "v7_rows_compared_to_export": n_v7,
        "v7_max_abs_diff_vs_export": vmax,
        "export": str(exp_p.relative_to(REPO)).replace("\\", "/"),
        "locked_events_excluded_from_history_and_map": True,
        "passed": bool(comp_ok and v7_ok),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / GATE_JSON_FMT.format(season=season)).write_text(
        json.dumps(res, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res["passed"] else 1


# ------------------------------------------------------------------------------------------ CLI
def cmd_audit(_args: argparse.Namespace) -> int:
    res = audit()
    print(
        json.dumps(
            {k: v for k, v in res.items() if k != "conference_movers_spot_check"}, indent=1, default=str
        )[:6000]
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    events, meta = load_confirmation_history()
    ensure_stats_root(events, STATS_ROOT_CONF)
    replay = Replay(events, STATS_ROOT_CONF)
    seasons = [int(s) for s in args.seasons.split(",")] if args.seasons else list(REPLAY_SEASONS)
    for s in seasons:
        days = replay.days([s])
        if args.limit_days:
            days = days[: args.limit_days]
        run_replay(replay, [s], COMPONENTS_DIR / f"components_{s}.jsonl", days)
    (OUT / "history_meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("audit")
    g = sub.add_parser("gate")
    g.add_argument("--season", default="2025")
    g.add_argument("--stride", type=int, default=10)
    g.add_argument("--export", required=True, help="grader.py export --model v7_served output")
    r = sub.add_parser("run")
    r.add_argument("--seasons", default="")
    r.add_argument("--limit-days", type=int, default=0)
    args = ap.parse_args(argv)
    return {"audit": cmd_audit, "gate": cmd_gate, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
