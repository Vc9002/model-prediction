"""Pure, port-ready arithmetic for cfb-structural-v6 (quick-261114-srb Task 1; ported verbatim to src in Task 2).

Design (see the plan's D-* decisions):

* D-RES-BUFFER   a game feeds ratings only if kickoff + RESULT_BUFFER_HOURS <= decision time.
* D-RATINGS      one weighted ridge solve per distinct eligible-game set. Per team-game row
                 ``points = mu + off[team] + def[opponent] + h_home * [home row] + h_away * [away row]``
                 with the home terms zero on neutral sites; ``mu`` and the home term are unpenalised, the
                 offense / defence ratings carry a ridge penalty ``lam``. Identifiability: with only
                 non-neutral games ``mu``, ``h_home`` and ``h_away`` are collinear (home rows and away rows
                 are the only two intercepts), so the fit constrains ``h_away = -h_home`` (one fitted home
                 term, ``mu`` = the league average points per team-game). Window = previous season plus the
                 current season to date, previous-season rows weighted ``w_prev``.
* Item 1         additive expected points: ``mu + off[team] + def[opponent] + home term`` -- never the
                 half-sum of an offence number and a defence number.
* D-NO-HANDSET-TERMS / D-NO-LINE-WIDENING: no altitude, travel, bye, weather, QB, conference-tier or
                 line-dependent term exists here.

No I/O beyond reading the files handed to the loader. numpy is used for linear algebra only.
Accumulation order is fixed (games in kickoff order, one accumulator per season) so a from-scratch build and
the incremental build of the research cache produce bit-identical matrices.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from ..domain import parse_utc

RESULT_BUFFER_HOURS = (
    5.0  # D-RES-BUFFER: > a 3.5 h game plus overtime / weather delay; stated in the artifact
)
POOL_KEY = "__POOL_OTHER__"  # prefix of the per-season pseudo-team that pools lower-division singletons


def pool_key(season: int) -> str:
    return f"{POOL_KEY}:{season}"


def is_pool(key: str) -> bool:
    return key.startswith(POOL_KEY)


NO_CALL_UNRATED = "NO_CALL_CFB_V6_UNRATED_TEAM"
NO_CALL_NO_HISTORY = "NO_CALL_CFB_V6_NO_HISTORY"
_JITTER = 1e-8  # numerical-only diagonal on the unpenalised parameters (mu, hfa)


class V6NoCall(Exception):
    """Fail-closed decline carrying a NO_CALL_* reason code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def season_of(start: datetime) -> int:
    return start.year - 1 if start.month in (1, 2) else start.year


# ------------------------------------------------------------------ team identity
def _norm(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode("utf-8").casefold()
    return re.sub(
        r"\s+", " ", folded.replace("'", "").replace("’", "").replace(".", "").replace("-", " ")
    ).strip()


@dataclass(frozen=True)
class TeamIdentity:
    key: str  # key used by the solve (POOL_KEY when the team is pooled that season)
    pooled: bool
    fcs: bool  # label used ONLY for the FBS-vs-FCS dispersion slice
    resolved: bool


class TeamResolver:
    """name -> rating key.

    Order: (1) resolve_team's canonical name (its alias table merges Hawai'i / Hawaii, App State /
    Appalachian State, San José); (2) an unresolved name that shares an ESPN team id with a resolvable name
    (ESPN's older display names such as "Southern Mississippi Golden Eagles") takes that canonical key;
    (3) otherwise ``ID:<espn id>``; (4) otherwise ``NAME:<normalised name>``. Legacy history rows carry no id,
    so ids come from the newer parser rows and the backfill. Label ``fcs``, per season: a team is FBS in a
    season when it plays at least one FBS-FBS event (ESPN feed class, a schedule fact) that season; with no class
    information at all, resolve_team says tier != FCS. Everything else is FCS-labelled. The label feeds only the
    FBS-vs-FCS dispersion slice (it does not move a rating).
    """

    def __init__(
        self,
        name_key: dict[str, str],
        resolved_keys: set[str],
        fbs_seasons: set[tuple[str, int]],
        fallback: Any,
    ) -> None:
        self._name_key = name_key
        self._resolved = resolved_keys
        self._fbs = fbs_seasons
        self._fallback = fallback

    @classmethod
    def from_games(cls, games: list[V6Game], resolve: Any = None) -> TeamResolver:
        if resolve is None:
            from ..data_sources.cfb_data import resolve_team as resolve
        names: dict[str, Counter] = {}
        id_names: dict[str, set[str]] = {}
        for g in games:
            for name, tid in ((g.home, g.home_id), (g.away, g.away_id)):
                names.setdefault(name, Counter())
                if tid:
                    names[name][tid] += 1
                    id_names.setdefault(tid, set()).add(name)
        canon_by_id: dict[str, str] = {}
        tier_by_key: dict[str, str] = {}
        for tid, ns in id_names.items():
            for n in sorted(ns):
                t = resolve(n)
                if t is not None:
                    canon_by_id[tid] = t.canonical_name
                    break
        name_key: dict[str, str] = {}
        resolved: set[str] = set()
        for name, ids in names.items():
            t = resolve(name)
            if t is not None:
                name_key[name] = t.canonical_name
                tier_by_key[t.canonical_name] = t.tier
                resolved.add(t.canonical_name)
                continue
            best_id = ids.most_common(1)[0][0] if ids else None
            if best_id is not None and best_id in canon_by_id:
                name_key[name] = canon_by_id[best_id]
                resolved.add(canon_by_id[best_id])
            elif best_id is not None:
                name_key[name] = f"ID:{best_id}"
            else:
                name_key[name] = f"NAME:{_norm(name)}"
        for key in resolved:
            if key not in tier_by_key:
                t = resolve(key)
                tier_by_key[key] = t.tier if t is not None else "G6"
        classes_available = any(g.game_class for g in games)
        fbs: set[tuple[str, int]] = set()
        if classes_available:
            for g in games:
                if g.game_class == "FBS-FBS":
                    fbs.add((name_key[g.home], g.season))
                    fbs.add((name_key[g.away], g.season))
        else:
            seasons = {g.season for g in games}
            fbs = {(k, s) for k in resolved if tier_by_key.get(k) != "FCS" for s in seasons}
        return cls(name_key, resolved, fbs, resolve)

    def base_key(self, name: str) -> str:
        hit = self._name_key.get(name)
        if hit is not None:
            return hit
        t = self._fallback(name)
        return t.canonical_name if t is not None else f"NAME:{_norm(name)}"

    def is_resolved(self, key: str) -> bool:
        return key in self._resolved

    def is_known(self, name: str) -> bool:
        """True when ``name`` is a history name or ``resolve_team`` can alias it.

        An unknown name would otherwise fall through ``base_key`` to a ``NAME:`` key and pool into the
        season's shared lower-division identity, projecting a made-up team as an average pooled one.
        """
        return name in self._name_key or self._fallback(name) is not None

    def is_fcs(self, key: str, season: int) -> bool:
        return is_pool(key) or (key, season) not in self._fbs


# ------------------------------------------------------------------ games and the history loader
@dataclass(frozen=True)
class V6Game:
    event_id: str
    start: datetime
    season: int
    home: str  # raw name as stored
    away: str
    home_score: int
    away_score: int
    neutral: bool | None  # None = unknown (treated as a home game, counted)
    week: int | None
    source: str  # "existing" | "backfill"
    home_id: str | None = None
    away_id: str | None = None
    game_class: str | None = None  # ESPN feed class from the backfill: FBS-FBS | FBS-FCS | FCS-FCS
    is_forfeit: bool = False  # forfeit rows carry a 1-0 placeholder score: never a rating input or a target


def _clean_int(v: Any) -> int:
    return int(v)


def _truthy(v: Any) -> bool | None:
    if v is None:
        return None
    return bool(v)


def load_history(
    processed_path: Path | None,
    historical_path: Path | None,
    backfill_path: Path | None = None,
    *,
    expected_backfill_sha256: str | None = None,
) -> tuple[list[V6Game], dict[str, Any]]:
    """Existing sources first (same precedence and parse rules as _load_cfb_history), then backfill rows.

    Existing sources win on score; neutral flag from the raw existing rows else backfill; unknown neutral
    is counted and treated as a home game. ESPN team ids and the ESPN feed class (FBS-FBS / FBS-FCS / FCS-FCS)
    come from the newer parser rows and the backfill. A declared backfill sha256 that does not match raises.
    """
    games: dict[str, V6Game] = {}
    facts: dict[str, dict[str, Any]] = {}
    stats: dict[str, Any] = {"existing_rows_read": 0, "existing_malformed": 0, "backfill_rows_read": 0}

    paths = [p for p in (processed_path, historical_path) if p is not None]
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                stats["existing_rows_read"] += 1
                try:
                    g = json.loads(line)
                    eid = str(g.get("event_id"))
                    fact = facts.setdefault(eid, {})
                    if g.get("is_neutral_site") is not None and "neutral" not in fact:
                        fact["neutral"] = bool(g["is_neutral_site"])
                    if g.get("week") is not None and "week" not in fact:
                        fact["week"] = int(g["week"])
                    for side in ("home", "away"):
                        if g.get(f"{side}_team_id") and f"{side}_id" not in fact:
                            fact[f"{side}_id"] = str(g[f"{side}_team_id"])
                    if eid in games:
                        continue
                    start_str = g.get("event_start_utc") or g.get("game_start_utc") or ""
                    start = parse_utc(start_str)
                    away_score = _clean_int(
                        g.get("away_score")
                        if g.get("away_score") is not None
                        else (g.get("away") or {}).get("score", 0)
                    )
                    home_score = _clean_int(
                        g.get("home_score")
                        if g.get("home_score") is not None
                        else (g.get("home") or {}).get("score", 0)
                    )
                    away = g.get("away_team") or (g.get("away") or {}).get("team_name") or ""
                    home = g.get("home_team") or (g.get("home") or {}).get("team_name") or ""
                    if not away or not home:
                        stats["existing_malformed"] += 1
                        continue
                    games[eid] = V6Game(
                        eid,
                        start,
                        season_of(start),
                        home,
                        away,
                        home_score,
                        away_score,
                        None,
                        None,
                        "existing",
                    )
                except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                    stats["existing_malformed"] += 1
    for eid, g in list(games.items()):
        fact = facts.get(eid, {})
        games[eid] = replace(
            g,
            neutral=fact.get("neutral"),
            week=fact.get("week"),
            home_id=fact.get("home_id"),
            away_id=fact.get("away_id"),
        )
    stats["existing_games"] = len(games)

    backfill_added = 0
    backfill_neutral_filled = 0
    backfill_score_disagree = 0
    if backfill_path is not None:
        sha = hashlib.sha256(backfill_path.read_bytes()).hexdigest()
        if expected_backfill_sha256 is not None and sha != expected_backfill_sha256:
            raise ValueError(f"backfill sha256 mismatch: file {sha} != declared {expected_backfill_sha256}")
        stats["backfill_sha256"] = sha
        with backfill_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                stats["backfill_rows_read"] += 1
                try:
                    g = json.loads(line)
                    eid = str(g["event_id"])
                    start = parse_utc(str(g.get("event_start_utc") or ""))
                    hs, as_ = _clean_int(g["home_score"]), _clean_int(g["away_score"])
                    home, away = str(g.get("home_team") or ""), str(g.get("away_team") or "")
                    if not home or not away:
                        continue
                    neutral = _truthy(g.get("neutral_site"))
                    week = int(g["week"]) if g.get("week") is not None else None
                    hid = str(g["home_team_id"]) if g.get("home_team_id") else None
                    aid = str(g["away_team_id"]) if g.get("away_team_id") else None
                    gclass = str(g["game_class"]) if g.get("game_class") else None
                    forfeit = bool(g.get("is_forfeit")) or str(g.get("status_name") or "") == "STATUS_FORFEIT"
                except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                    stats["backfill_malformed"] = stats.get("backfill_malformed", 0) + 1
                    continue
                cur = games.get(eid)
                if cur is None:
                    games[eid] = V6Game(
                        eid,
                        start,
                        season_of(start),
                        home,
                        away,
                        hs,
                        as_,
                        neutral,
                        week,
                        "backfill",
                        hid,
                        aid,
                        gclass,
                        forfeit,
                    )
                    backfill_added += 1
                    continue
                if (cur.home_score, cur.away_score) != (hs, as_):
                    backfill_score_disagree += 1
                fill_neutral = cur.neutral is None and neutral is not None
                if fill_neutral:
                    backfill_neutral_filled += 1
                games[eid] = replace(
                    cur,
                    neutral=neutral if fill_neutral else cur.neutral,
                    week=cur.week if cur.week is not None else week,
                    home_id=cur.home_id or hid,
                    away_id=cur.away_id or aid,
                    game_class=gclass,
                    is_forfeit=forfeit,
                )
    stats["backfill_games_added"] = backfill_added
    stats["backfill_neutral_filled"] = backfill_neutral_filled
    stats["backfill_score_disagreements_existing_wins"] = backfill_score_disagree
    ordered = sorted(games.values(), key=lambda g: (g.start, g.event_id))
    stats["games"] = len(ordered)
    stats["unknown_neutral"] = sum(1 for g in ordered if g.neutral is None)
    return ordered, stats


def snapshot_rows(games: list[V6Game]) -> list[str]:
    out = []
    for g in games:
        out.append(
            json.dumps(
                {
                    "event_id": g.event_id,
                    "start_utc": g.start.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ"),
                    "home": g.home,
                    "away": g.away,
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "neutral": g.neutral,
                    "week": g.week,
                    "source": g.source,
                    "home_id": g.home_id,
                    "away_id": g.away_id,
                    "game_class": g.game_class,
                    "is_forfeit": g.is_forfeit,
                },
                sort_keys=True,
            )
        )
    return out


def load_snapshot(path: Path) -> list[V6Game]:
    games = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            g = json.loads(line)
            start = parse_utc(g["start_utc"])
            games.append(
                V6Game(
                    g["event_id"],
                    start,
                    season_of(start),
                    g["home"],
                    g["away"],
                    int(g["home_score"]),
                    int(g["away_score"]),
                    g["neutral"],
                    g["week"],
                    g["source"],
                    g.get("home_id"),
                    g.get("away_id"),
                    g.get("game_class"),
                    bool(g.get("is_forfeit")),
                )
            )
    return sorted(games, key=lambda g: (g.start, g.event_id))


# ------------------------------------------------------------------ rating accumulation and solve
@dataclass
class _SeasonAcc:
    a: np.ndarray
    b: np.ndarray
    games_by_team: Counter = field(default_factory=Counter)
    points_sum: float = 0.0
    rows: int = 0


@dataclass(frozen=True)
class RatingSolution:
    mu: float
    h_home: float
    h_away: float
    off: np.ndarray
    dfn: np.ndarray
    cur_games: Counter
    prev_games: Counter
    eligible_games: int
    cutoff: datetime


class RatingBook:
    """Accumulates eligible games per season and answers ``solve`` queries at non-decreasing cutoffs.

    ``team_index`` maps rating key -> column; the parameter vector is [mu, hfa, off[0..T), def[0..T)] with
    h_home = +hfa and h_away = -hfa (see the module docstring).

    Identity rule: a team resolve_team (or a shared ESPN id) places is always its own key; any other team is its
    own key in a season only if it plays at least two events that season (a schedule fact, known in advance),
    otherwise it shares that season's pooled pseudo-team. A game whose two sides are both pooled carries no
    information about the rated teams, and a forfeit (1-0 placeholder score) is no result; both are left out.
    """

    def __init__(
        self, games: list[V6Game], resolver: TeamResolver, buffer_hours: float = RESULT_BUFFER_HOURS
    ) -> None:
        self.games = games  # sorted by kickoff
        self.resolver = resolver
        self.buffer = timedelta(hours=buffer_hours)
        self._starts = [g.start for g in games]
        counts: Counter = Counter()
        for g in games:
            counts[(resolver.base_key(g.home), g.season)] += 1
            counts[(resolver.base_key(g.away), g.season)] += 1
        self._season_counts = counts
        self._row_keys = [(self._key(g.home, g.season), self._key(g.away, g.season)) for g in games]
        self.forfeits_skipped = sum(1 for g in games if g.is_forfeit)
        keys = sorted({k for pair in self._row_keys for k in pair})
        self.team_index = {k: i for i, k in enumerate(keys)}
        self.n_teams = len(keys)
        self.p = 2 + 2 * self.n_teams
        self._seasons: dict[int, _SeasonAcc] = {}
        self._pointer = 0
        self._added = 0
        self._last_cutoff: datetime | None = None
        pen = np.zeros(self.p)
        pen[2:] = 1.0
        self._pen = pen
        self.pool_pool_skipped = sum(
            1
            for (h, a), g in zip(self._row_keys, games, strict=True)
            if is_pool(h) and is_pool(a) and not g.is_forfeit
        )

    def _key(self, name: str, season: int) -> str:
        base = self.resolver.base_key(name)
        if self.resolver.is_resolved(base) or self._season_counts[(base, season)] >= 2:
            return base
        return pool_key(season)

    def identity(self, name: str, season: int) -> TeamIdentity:
        """Identity of a team for a target game in ``season`` (unseen names resolve through the fallback)."""
        base = self.resolver.base_key(name)
        resolved = self.resolver.is_resolved(base)
        seen = self._season_counts.get((base, season), 0)
        key = base if (resolved or seen >= 2) else pool_key(season)
        return TeamIdentity(key, is_pool(key), self.resolver.is_fcs(key, season), resolved)

    def reset(self) -> None:
        self._seasons = {}
        self._pointer = 0
        self._added = 0
        self._last_cutoff = None

    def _acc(self, season: int) -> _SeasonAcc:
        acc = self._seasons.get(season)
        if acc is None:
            acc = _SeasonAcc(np.zeros((self.p, self.p)), np.zeros(self.p))
            self._seasons[season] = acc
        return acc

    def _add(self, i: int) -> bool:
        g = self.games[i]
        h, a = self._row_keys[i]
        if g.is_forfeit or (is_pool(h) and is_pool(a)):
            return False
        acc = self._acc(g.season)
        ih, ia = self.team_index[h], self.team_index[a]
        t = self.n_teams
        hsign = 0.0 if g.neutral else 1.0
        # home row: team = home, opponent = away; away row: the home term enters with the opposite sign
        for team_i, opp_i, pts, sign in (
            (ih, ia, float(g.home_score), hsign),
            (ia, ih, float(g.away_score), -hsign),
        ):
            idx = [0, 1, 2 + team_i, 2 + t + opp_i]
            vec = np.array([1.0, sign, 1.0, 1.0])
            acc.a[np.ix_(idx, idx)] += np.outer(vec, vec)
            acc.b[idx] += vec * pts
        acc.games_by_team[h] += 1
        acc.games_by_team[a] += 1
        acc.points_sum += float(g.home_score + g.away_score)
        acc.rows += 2
        return True

    def advance(self, cutoff: datetime) -> int:
        """Admit every game with start + buffer <= cutoff. Cutoffs must not decrease.

        Returns the number of games that have entered the solve so far (a version counter: equal values mean an
        identical rating problem within a season).
        """
        if self._last_cutoff is not None and cutoff < self._last_cutoff:
            self.reset()
        limit = cutoff - self.buffer
        end = bisect.bisect_right(self._starts, limit)
        while self._pointer < end:
            if self._add(self._pointer):
                self._added += 1
            self._pointer += 1
        self._last_cutoff = cutoff
        return self._added

    def season_games(self, season: int) -> Counter:
        acc = self._seasons.get(season)
        return acc.games_by_team if acc is not None else Counter()

    def solve(self, cutoff: datetime, *, lam: float, w_prev: float) -> RatingSolution:
        """Ratings from every game with start + buffer <= cutoff (advance() must have been called for it)."""
        season = season_of(cutoff)
        cur = self._seasons.get(season)
        prev = self._seasons.get(season - 1)
        a = np.zeros((self.p, self.p))
        b = np.zeros(self.p)
        if cur is not None:
            a += cur.a
            b += cur.b
        if prev is not None:
            a += w_prev * prev.a
            b += w_prev * prev.b
        if cur is None and prev is None:
            raise V6NoCall(NO_CALL_NO_HISTORY, f"no eligible game in seasons {season - 1}-{season}")
        diag = lam * self._pen + _JITTER * (1.0 - self._pen)
        a[np.diag_indices(self.p)] += diag
        x = np.linalg.solve(a, b)
        t = self.n_teams
        return RatingSolution(
            mu=float(x[0]),
            h_home=float(x[1]),
            h_away=-float(x[1]),
            off=x[2 : 2 + t].copy(),
            dfn=x[2 + t :].copy(),
            cur_games=cur.games_by_team if cur is not None else Counter(),
            prev_games=prev.games_by_team if prev is not None else Counter(),
            eligible_games=self._added,
            cutoff=cutoff,
        )


# ------------------------------------------------------------------ projection
@dataclass(frozen=True)
class V6Projection:
    home_points: float
    away_points: float
    margin: float
    total: float
    mu: float
    h_home: float
    h_away: float
    off_home: float
    def_home: float
    off_away: float
    def_away: float
    home_games_window: int
    away_games_window: int
    home_games_current: int
    away_games_current: int
    sample_games: int
    is_fbs_vs_fcs: bool
    eligible_games: int


def additive_points(mu: float, off_team: float, def_opponent: float, home_term: float) -> float:
    """Item 1: league average + offence deviation + opponent defence deviation + home term."""
    return mu + off_team + def_opponent + home_term


def project(
    sol: RatingSolution,
    book: RatingBook,
    home_name: str,
    away_name: str,
    neutral: bool | None,
) -> V6Projection:
    """Raw (uncalibrated) additive projection. Fails closed with NO_CALL_CFB_V6_UNRATED_TEAM."""
    season = season_of(sol.cutoff)
    for name in (home_name, away_name):
        # Before identity(): a name the history never saw and resolve_team cannot alias must decline,
        # not pool. Real pooled lower-division teams are history names, so they still project.
        if not book.resolver.is_known(name):
            raise V6NoCall(NO_CALL_UNRATED, f"{name} is not a team in the rating history")
    hi = book.identity(home_name, season)
    ai = book.identity(away_name, season)
    for ident, name in ((hi, home_name), (ai, away_name)):
        if (
            ident.key not in book.team_index
            or sol.cur_games.get(ident.key, 0) + sol.prev_games.get(ident.key, 0) == 0
        ):
            raise V6NoCall(NO_CALL_UNRATED, f"{name} has no game in the rating window")
    ih, ia = book.team_index[hi.key], book.team_index[ai.key]
    is_neutral = bool(neutral)
    hh = 0.0 if is_neutral else sol.h_home
    ha = 0.0 if is_neutral else sol.h_away
    home_pts = additive_points(sol.mu, float(sol.off[ih]), float(sol.dfn[ia]), hh)
    away_pts = additive_points(sol.mu, float(sol.off[ia]), float(sol.dfn[ih]), ha)
    hc = int(sol.cur_games.get(hi.key, 0))
    ac = int(sol.cur_games.get(ai.key, 0))
    return V6Projection(
        home_points=home_pts,
        away_points=away_pts,
        margin=home_pts - away_pts,
        total=home_pts + away_pts,
        mu=sol.mu,
        h_home=sol.h_home,
        h_away=sol.h_away,
        off_home=float(sol.off[ih]),
        def_home=float(sol.dfn[ih]),
        off_away=float(sol.off[ia]),
        def_away=float(sol.dfn[ia]),
        home_games_window=hc + int(sol.prev_games.get(hi.key, 0)),
        away_games_window=ac + int(sol.prev_games.get(ai.key, 0)),
        home_games_current=hc,
        away_games_current=ac,
        sample_games=min(hc, ac),
        is_fbs_vs_fcs=hi.fcs or ai.fcs,
        eligible_games=sol.eligible_games,
    )


def project_at(
    book: RatingBook,
    cutoff: datetime,
    home_name: str,
    away_name: str,
    neutral: bool | None,
    *,
    lam: float,
    w_prev: float,
) -> V6Projection:
    """Convenience for tests and one-off queries: advance, solve, project."""
    book.advance(cutoff)
    return project(book.solve(cutoff, lam=lam, w_prev=w_prev), book, home_name, away_name, neutral)


# ------------------------------------------------------------------ extractor and feature payload
@dataclass(frozen=True)
class CFBV6MatchupFeatures:
    """Point-in-time v6 feature vector for one game.

    ``raw_wind_mph`` / ``raw_temperature_f`` / ``raw_precipitation_in`` and the two QB starter probabilities are
    recorded exactly as passed (None stays None) so the weather and QB audit is possible going forward; no weather
    or QB term moves the projection (D-NO-HANDSET-TERMS).
    """

    event_id: str
    game_start_utc: str
    home_team: str
    away_team: str
    is_neutral_site: bool
    mu: float
    h_home: float
    h_away: float
    home_offense_rating: float
    home_defense_rating: float
    away_offense_rating: float
    away_defense_rating: float
    projected_home_points: float
    projected_away_points: float
    projected_margin_home: float
    projected_total: float
    home_games_window: int
    away_games_window: int
    home_games_current: int
    away_games_current: int
    sample_games: int
    is_fbs_vs_fcs: bool
    eligible_games: int
    history_cutoff_utc: str
    backfill_sha256: str | None
    backfill_present: bool
    raw_wind_mph: float | None = None
    raw_temperature_f: float | None = None
    raw_precipitation_in: float | None = None
    qb_starter_prob_home: float | None = None
    qb_starter_prob_away: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


class CFBV6FeatureExtractor:
    """Builds ``CFBV6MatchupFeatures`` from a completed-game history; solver config comes from the artifact."""

    def __init__(
        self,
        games: list[V6Game],
        *,
        lam: float,
        w_prev: float,
        buffer_hours: float = RESULT_BUFFER_HOURS,
        resolver: TeamResolver | None = None,
        backfill_sha256: str | None = None,
    ) -> None:
        ordered = sorted(games, key=lambda g: (g.start, g.event_id))
        self.lam = float(lam)
        self.w_prev = float(w_prev)
        self.backfill_sha256 = backfill_sha256
        self.book = RatingBook(ordered, resolver or TeamResolver.from_games(ordered), buffer_hours)
        # Equal (admitted-game count, season) means an identical rating problem: reuse the solve.
        self._solved: tuple[tuple[int, int], RatingSolution] | None = None

    def extract_features(
        self,
        *,
        event_id: str,
        game_start_utc: str | datetime,
        home_team: str,
        away_team: str,
        is_neutral_site: bool | None = False,
        decision_time: datetime | None = None,
        wind_mph: float | None = None,
        temperature_f: float | None = None,
        precipitation_in: float | None = None,
        qb_starter_prob_home: float | None = None,
        qb_starter_prob_away: float | None = None,
    ) -> CFBV6MatchupFeatures:
        start = parse_utc(game_start_utc) if isinstance(game_start_utc, str) else game_start_utc
        # A decision can never see past kickoff: the cutoff is the earlier of the two.
        cutoff = start if decision_time is None else min(decision_time, start)
        admitted = self.book.advance(cutoff)
        key = (admitted, season_of(cutoff))
        if self._solved is not None and self._solved[0] == key:
            sol = replace(self._solved[1], cutoff=cutoff)
        else:
            sol = self.book.solve(cutoff, lam=self.lam, w_prev=self.w_prev)
            self._solved = (key, sol)
        proj = project(
            sol,
            self.book,
            home_team,
            away_team,
            is_neutral_site,
        )
        return CFBV6MatchupFeatures(
            event_id=event_id,
            game_start_utc=start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            home_team=home_team,
            away_team=away_team,
            is_neutral_site=bool(is_neutral_site),
            mu=proj.mu,
            h_home=proj.h_home,
            h_away=proj.h_away,
            home_offense_rating=proj.off_home,
            home_defense_rating=proj.def_home,
            away_offense_rating=proj.off_away,
            away_defense_rating=proj.def_away,
            projected_home_points=proj.home_points,
            projected_away_points=proj.away_points,
            projected_margin_home=proj.margin,
            projected_total=proj.total,
            home_games_window=proj.home_games_window,
            away_games_window=proj.away_games_window,
            home_games_current=proj.home_games_current,
            away_games_current=proj.away_games_current,
            sample_games=proj.sample_games,
            is_fbs_vs_fcs=proj.is_fbs_vs_fcs,
            eligible_games=proj.eligible_games,
            history_cutoff_utc=cutoff.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            backfill_sha256=self.backfill_sha256,
            backfill_present=self.backfill_sha256 is not None,
            raw_wind_mph=wind_mph,
            raw_temperature_f=temperature_f,
            raw_precipitation_in=precipitation_in,
            qb_starter_prob_home=qb_starter_prob_home,
            qb_starter_prob_away=qb_starter_prob_away,
        )
