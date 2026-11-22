"""NBA v10 player layer: point-in-time player ratings, minutes and availability (research).

Shadow feature for ``nba-player-v10`` (docs/NBA_REVIEW_AND_V10_PLAN_2026-11-20.md, Phase 3).
Nothing here serves. The research fit and any future serving path call the same
``PlayerLayerState`` so the features stay in literal train/serve parity.

Point-in-time contract
----------------------
* State is advanced one ET game date at a time. For date ``D`` the caller reads features
  BEFORE ``add_games`` is called with ``D``'s games, so a rating, a minutes projection or a
  league baseline used for a game on ``D`` only contains games on ET dates strictly before
  ``D`` (the production ``FeatureStore.games_before`` cut).
* The box score of the game being priced is never an input. ``did_not_play`` in a PAST box is
  a historical fact and feeds minutes; it never feeds availability.
* Availability comes only from official injury report rows observed at or before the decision
  time (tip minus ``DECISION_LEAD``). Missing, stale, unsubmitted or ambiguous report data is a
  ``NO_CALL_*`` reason, never a guess.

Ratings
-------
Per player, two ratings in points per game for a full 48-minute share of the game:
``r`` (margin) and ``t`` (game total, per 100 possessions). Each is a box-score prior plus a
ridge residual:

* box prior ``p = z . gamma`` (margin) and ``q = z . delta`` (total), where ``z`` is the
  player's exponentially decayed per-48 box rates shrunk toward fringe-player rates with
  ``PRIOR_MINUTES`` pseudo-minutes, and ``gamma``/``delta`` are fitted on train by regressing
  game margin / game efficiency on minute-weighted pre-game rates;
* ridge residual ``u`` from a game-level adjusted plus-minus regression of what the prior did
  not explain, with exponentially decayed sufficient statistics and penalty ``lambda``.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import unicodedata
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

HOLDOUT_START = "2026-02-23"  # NBA registry holdout; never loaded by this module
STAT_KEYS = ("pts", "fg3m", "fga", "fta", "ast", "tov", "oreb", "dreb", "stl", "blk", "pf")
N_STATS = len(STAT_KEYS)
POSTSEASON_TYPES = frozenset({"post-season", "play-in-season"})

# Pre-registered constants (outputs/nba/v10_player/PREREG.md). Not tuned on validation.
PRIOR_MINUTES = 250.0  # pseudo-minutes of fringe-player rates in every player's box rates
ACTIVE_DIAG = 0.05  # decayed design weight below which a player's ridge residual is 0
BLOWOUT_START = 10.0  # final margin beyond which minutes are blowout-adjusted
MPG_WINDOW = 10  # played games in the minutes-per-game average
MPG_DECAY = 0.85  # recency weight per played game
MIN_ROLE_GAMES = 3  # games in the current role (starter/bench) needed to use the role average
SHARE_WINDOW = 5  # team games in the recent played-share
ROSTER_WINDOW = 10  # a candidate's last box row must be inside the team's last N games
SCALE_CAP = 1.25  # max upscale of available players' expected minutes
MIN_CANDIDATES = 8
BASELINE_MIN_GAMES = 150  # league baselines: current season, padded to at least this many games
PACE_WINDOW = 20
PACE_DECAY = 0.9
PACE_PSEUDO = 5.0
BOX_GAP_MAX = 1  # more than this many of the team's last 5 current-season games without box -> NO_CALL
DECISION_LEAD = timedelta(minutes=60)
REPORT_MAX_AGE = timedelta(hours=18)
TEAM_MINUTES = 240.0

NO_CALL_HISTORY_SHORT = "NO_CALL_HISTORY_SHORT"
NO_CALL_BOX_GAP = "NO_CALL_BOX_GAP"
NO_CALL_ROSTER_THIN = "NO_CALL_ROSTER_THIN"
NO_CALL_INJURY_REPORT_MISSING = "NO_CALL_INJURY_REPORT_MISSING"
NO_CALL_INJURY_GAME_ABSENT = "NO_CALL_INJURY_GAME_ABSENT"
NO_CALL_INJURY_NOT_SUBMITTED = "NO_CALL_INJURY_NOT_SUBMITTED"
NO_CALL_INJURY_NAME_AMBIGUOUS = "NO_CALL_INJURY_NAME_AMBIGUOUS"

SHARE_BUCKETS = ("zero", "low", "mid", "full")
STATUSES = ("unknown", "unlisted", "out", "doubtful", "questionable", "probable", "available")
MIN_TABLE_CELL = 30


# ---------------------------------------------------------------- data


@dataclass(frozen=True)
class PlayerLine:
    player_id: str
    name: str
    home: bool
    minutes: float
    starter: bool
    stats: tuple[float, ...]  # STAT_KEYS order, game totals

    @property
    def played(self) -> bool:
        return self.minutes > 0.0


@dataclass(frozen=True)
class GameBox:
    event_id: str
    date: str  # ET game date
    start_utc: str
    season_year: int
    season_type: str
    home_id: str
    away_id: str
    home_name: str
    away_name: str
    home_score: int
    away_score: int
    players: tuple[PlayerLine, ...] | None  # None: box score missing

    @property
    def playoff(self) -> bool:
        return self.season_type in POSTSEASON_TYPES

    @property
    def start(self) -> datetime:
        return parse_utc(self.start_utc)

    def side(self, home: bool) -> tuple[PlayerLine, ...]:
        return tuple(p for p in self.players or () if p.home == home)

    def team_minutes(self, home: bool) -> float:
        return sum(p.minutes for p in self.side(home))

    def possessions(self) -> float | None:
        """Average of both teams' FGA - OREB + TOV + 0.44 FTA (player sums)."""
        if not self.players:
            return None
        idx = {k: i for i, k in enumerate(STAT_KEYS)}
        per_side = []
        for home in (True, False):
            s = [sum(p.stats[idx[k]] for p in self.side(home)) for k in ("fga", "oreb", "tov", "fta")]
            per_side.append(s[0] - s[1] + s[2] + 0.44 * s[3])
        poss = 0.5 * (per_side[0] + per_side[1])
        return poss if poss > 50.0 else None


def parse_utc(text: str) -> datetime:
    value = text.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def game_from_row(row: Mapping[str, Any]) -> GameBox:
    players = None
    if row.get("players"):
        players = tuple(
            PlayerLine(
                player_id=str(p[0]),
                name=str(p[1]),
                home=bool(p[2]),
                minutes=float(p[3]),
                starter=bool(p[4]),
                stats=tuple(float(x) for x in p[5 : 5 + N_STATS]),
            )
            for p in row["players"]
        )
    return GameBox(
        event_id=str(row["event_id"]),
        date=str(row["date"]),
        start_utc=str(row["start_utc"]),
        season_year=int(row["season_year"]),
        season_type=str(row["season_type"]),
        home_id=str(row["home_id"]),
        away_id=str(row["away_id"]),
        home_name=str(row["home_name"]),
        away_name=str(row["away_name"]),
        home_score=int(row["home_score"]),
        away_score=int(row["away_score"]),
        players=players,
    )


def load_box_games(path: Path, end_date: str = HOLDOUT_START) -> list[GameBox]:
    """Stream ``box_games.jsonl`` (scripts/nba_v10/player_build_data.py) for dates < ``end_date``."""
    if end_date > HOLDOUT_START:
        raise ValueError(f"end_date {end_date} reaches the locked holdout ({HOLDOUT_START})")
    games: list[GameBox] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            row = json.loads(raw)
            if row["date"] < end_date:
                games.append(game_from_row(row))
    games.sort(key=lambda g: (g.date, g.start_utc, g.event_id))
    return games


# ---------------------------------------------------------------- fitted inputs


@dataclass(frozen=True)
class BoxPrior:
    """Train-fitted box-score prior (``gamma``/``delta`` per per-48 stat in STAT_KEYS order)."""

    gamma: tuple[float, ...]
    delta: tuple[float, ...]
    hca: float
    total_const: float
    fringe_rates: tuple[float, ...]  # per-48 rates of fringe players (new-player prior)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gamma": list(self.gamma),
            "delta": list(self.delta),
            "hca": self.hca,
            "total_const": self.total_const,
            "fringe_rates": list(self.fringe_rates),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> BoxPrior:
        return cls(
            tuple(d["gamma"]),
            tuple(d["delta"]),
            float(d["hca"]),
            float(d["total_const"]),
            tuple(d["fringe_rates"]),
        )


@dataclass(frozen=True)
class MinutesModel:
    """Train-fitted minutes inputs: blowout slopes and P(play | status, recent share) table."""

    blowout_starter: float
    blowout_bench: float
    p_play: Mapping[str, float]  # "<status>|<share bucket>" -> probability

    def to_dict(self) -> dict[str, Any]:
        return {
            "blowout_starter": self.blowout_starter,
            "blowout_bench": self.blowout_bench,
            "p_play": dict(self.p_play),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> MinutesModel:
        return cls(float(d["blowout_starter"]), float(d["blowout_bench"]), dict(d["p_play"]))

    def blowout_factor(self, starter: bool, abs_margin: float) -> float:
        slope = self.blowout_starter if starter else self.blowout_bench
        return min(2.0, max(0.5, 1.0 + slope * max(0.0, abs_margin - BLOWOUT_START)))

    def play_probability(self, status: str, bucket: str) -> float:
        for key in (f"{status}|{bucket}", f"{status}|*", f"unknown|{bucket}"):
            if key in self.p_play:
                return self.p_play[key]
        raise KeyError(f"no P(play) for {status}|{bucket}")


@dataclass(frozen=True)
class RatingParams:
    half_life_days: float
    ridge_margin: tuple[float, ...] = (math.inf,)
    ridge_total: tuple[float, ...] = (math.inf,)
    prior_minutes: float = PRIOR_MINUTES


def share_bucket(share: float) -> str:
    if share <= 0.0:
        return "zero"
    if share < 0.5:
        return "low"
    if share < 1.0:
        return "mid"
    return "full"


def fit_play_table(observations: Iterable[tuple[str, str, bool]]) -> dict[str, float]:
    """P(play) per (status, share bucket) cell, with status-only and bucket-only fallbacks.

    Cells with fewer than ``MIN_TABLE_CELL`` observations are dropped so the lookup falls back.
    """
    cells: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for status, bucket, played in observations:
        for key in (f"{status}|{bucket}", f"{status}|*"):
            cells[key][0] += 1
            cells[key][1] += 1 if played else 0
    return {k: round(v[1] / v[0], 4) for k, v in sorted(cells.items()) if v[0] >= MIN_TABLE_CELL}


# ---------------------------------------------------------------- injury reports


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """'Oubre Jr., Kelly' and 'Kelly Oubre Jr.' -> 'kelly oubre'."""
    text = name.strip()
    if "," in text:
        last, _, first = text.partition(",")
        text = f"{first.strip()} {last.strip()}"
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[.'`]", "", text)
    tokens = [t for t in re.split(r"[\s\-]+", text) if t and t not in _SUFFIXES]
    return " ".join(tokens)


def normalize_status(status: str) -> str:
    s = status.strip().lower()
    return s if s in STATUSES else "questionable"


@dataclass(frozen=True)
class TeamReport:
    players: tuple[tuple[str, str], ...] | None  # (raw name, normalized status); None = NO_CALL
    reason: str | None
    observed_at_utc: str | None


class InjuryReportIndex:
    """Official injury report snapshots by game date (``injury_index.jsonl``).

    ``team_report`` returns the team's listed players from the latest report observed at or
    before ``decision_utc`` that lists the game; anything missing or stale is a NO_CALL.
    """

    def __init__(self, rows: Iterable[Mapping[str, Any]]) -> None:
        self._dates: dict[str, Mapping[str, Any]] = {str(r["game_date"]): r for r in rows}

    @classmethod
    def from_jsonl(cls, path: Path) -> InjuryReportIndex:
        opener: Callable[..., Any] = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            return cls(json.loads(raw) for raw in handle)

    def covers(self, game_date: str) -> bool:
        return game_date in self._dates

    def team_report(
        self, game_date: str, away_name: str, home_name: str, team_name: str, decision_utc: datetime
    ) -> TeamReport:
        row = self._dates.get(game_date)
        if row is None:
            return TeamReport(None, NO_CALL_INJURY_REPORT_MISSING, None)
        eligible = [
            (parse_utc(obs), matchups) for obs, matchups in row["reports"] if parse_utc(obs) <= decision_utc
        ]
        if not eligible:
            return TeamReport(None, NO_CALL_INJURY_REPORT_MISSING, None)
        latest_obs, matchups = max(eligible, key=lambda item: item[0])
        if decision_utc - latest_obs > REPORT_MAX_AGE:
            return TeamReport(None, NO_CALL_INJURY_REPORT_MISSING, latest_obs.isoformat())
        if f"{away_name}@{home_name}" not in matchups:
            return TeamReport(None, NO_CALL_INJURY_GAME_ABSENT, latest_obs.isoformat())
        history = [(parse_utc(obs), entry) for obs, entry in row["teams"].get(team_name, [])]
        history = [h for h in history if h[0] <= latest_obs]
        for obs, entry in sorted(history, key=lambda item: item[0], reverse=True):
            if entry is not None:
                players = tuple((str(name), normalize_status(str(status))) for name, status in entry)
                return TeamReport(players, None, obs.isoformat())
        if not history:
            # Game listed but the team has no rows at all: nobody listed.
            return TeamReport((), None, latest_obs.isoformat())
        return TeamReport(None, NO_CALL_INJURY_NOT_SUBMITTED, latest_obs.isoformat())


def match_report(
    candidates: Mapping[str, str], report: Sequence[tuple[str, str]]
) -> tuple[dict[str, str] | None, int]:
    """Map report rows onto candidate player ids by normalized name.

    ``candidates`` is player_id -> display name. Returns (player_id -> status, unmatched rows),
    or (None, n) when a report name matches more than one candidate (ambiguous: NO_CALL).
    Unmatched rows are players outside the projected rotation (two-way, long-term absent).
    """
    by_name: dict[str, list[str]] = defaultdict(list)
    by_short: dict[str, list[str]] = defaultdict(list)
    for pid, name in candidates.items():
        norm = normalize_name(name)
        by_name[norm].append(pid)
        parts = norm.split()
        if parts:
            by_short[f"{parts[0][:1]} {parts[-1]}"].append(pid)
    out: dict[str, str] = {}
    unmatched = 0
    for raw, status in report:
        norm = normalize_name(raw)
        hits = by_name.get(norm, [])
        if not hits:
            parts = norm.split()
            hits = by_short.get(f"{parts[0][:1]} {parts[-1]}", []) if parts else []
        if len(hits) > 1:
            return None, unmatched
        if not hits:
            unmatched += 1
            continue
        out[hits[0]] = status
    return out, unmatched


# ---------------------------------------------------------------- state


@dataclass
class _PlayerTrack:
    name: str
    team: str = ""
    team_index: int = -1  # team-log index of the last box row (any row, DNP included)
    season_year: int = 0
    stint_start: int = 0
    played_idx: deque[int] | None = None
    recent: deque[tuple[float, bool]] | None = None  # (blowout-adjusted minutes, started), played games


@dataclass(frozen=True)
class TeamLogEntry:
    date: str
    event_id: str
    season_year: int
    has_box: bool


@dataclass(frozen=True)
class Candidate:
    player_id: str
    name: str
    mpg: float
    share: float

    @property
    def bucket(self) -> str:
        return share_bucket(self.share)


@dataclass(frozen=True)
class TeamProjection:
    reason: str | None
    strength: float = 0.0  # sum(e/48 * r) + replacement: expected margin contribution
    total_impact: float = 0.0  # sum(e/48 * t) + replacement
    replacement_minutes: float = 0.0
    n_candidates: int = 0
    unmatched_report_rows: int = 0
    minutes: Mapping[str, float] | None = None


class PlayerLayerState:
    """Incremental point-in-time state. Call ``begin_day`` then read features, then
    ``add_games`` with that day's completed games."""

    def __init__(
        self,
        params: RatingParams,
        prior: BoxPrior | None = None,
        minutes_model: MinutesModel | None = None,
        *,
        fringe_rates: Sequence[float] | None = None,
    ) -> None:
        self.params = params
        self.prior = prior
        self.minutes_model = minutes_model
        rates = prior.fringe_rates if prior is not None else fringe_rates
        self.fringe = np.asarray(rates if rates is not None else np.zeros(N_STATS), float)
        self.index: dict[str, int] = {}
        self.tracks: dict[str, _PlayerTrack] = {}
        cap = 256
        self._box_raw = np.zeros((cap, N_STATS))
        self._min_raw = np.zeros(cap)
        self._S = {"m": np.zeros((cap, cap)), "t": np.zeros((cap, cap))}
        self._b = {"m": np.zeros(cap), "t": np.zeros(cap)}
        self._t0: date | None = None
        self.today: str | None = None
        self._scale = 1.0
        self.team_log: dict[str, list[TeamLogEntry]] = defaultdict(list)
        self.team_members: dict[str, set[str]] = defaultdict(set)
        self.team_pace: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=PACE_WINDOW))
        self.league: list[
            tuple[int, float, float]
        ] = []  # (season_year, possessions, points per 100 per team)
        self.ratings_m: list[np.ndarray] = []
        self.ratings_t: list[np.ndarray] = []
        self._z_today: np.ndarray | None = None
        self.eff_base = 0.0
        self.pace_base = 0.0
        self._warm: dict[tuple[str, float], np.ndarray] = {}

    # ---------------------------------------------------------------- bookkeeping

    @property
    def n_players(self) -> int:
        return len(self.index)

    def _grow(self, need: int) -> None:
        cap = self._min_raw.shape[0]
        if need <= cap:
            return
        new = max(need, cap * 2)
        box = np.zeros((new, N_STATS))
        box[:cap] = self._box_raw
        self._box_raw = box
        mins = np.zeros(new)
        mins[:cap] = self._min_raw
        self._min_raw = mins
        for key in ("m", "t"):
            s = np.zeros((new, new))
            s[:cap, :cap] = self._S[key]
            self._S[key] = s
            b = np.zeros(new)
            b[:cap] = self._b[key]
            self._b[key] = b

    def _pid(self, player_id: str, name: str) -> int:
        idx = self.index.get(player_id)
        if idx is None:
            idx = len(self.index)
            self._grow(idx + 1)
            self.index[player_id] = idx
            self.tracks[player_id] = _PlayerTrack(
                name=name, played_idx=deque(maxlen=ROSTER_WINDOW), recent=deque(maxlen=MPG_WINDOW)
            )
        return idx

    def _days(self, day: str) -> float:
        d = date.fromisoformat(day)
        if self._t0 is None:
            self._t0 = d
        return float((d - self._t0).days)

    def _growth(self, day: str) -> float:
        return float(2.0 ** (self._days(day) / self.params.half_life_days))

    # ---------------------------------------------------------------- per-day reads

    def begin_day(self, day: str, *, solve: bool = True) -> None:
        """Freeze the state as of ET date ``day`` (games strictly before it)."""
        if self.today is not None and day <= self.today:
            raise ValueError(f"days must increase: {day} after {self.today}")
        self.today = day
        self._scale = 1.0 / self._growth(day)
        n = self.n_players
        self._z_today = self._box_rates(np.arange(n))
        self.eff_base, self.pace_base = self._baselines(day)
        self.ratings_m, self.ratings_t = [], []
        if solve and self.prior is not None:
            gamma = np.asarray(self.prior.gamma)
            delta = np.asarray(self.prior.delta)
            p = self._z_today @ gamma
            q = self._z_today @ delta
            for lam in self.params.ridge_margin:
                self.ratings_m.append(p + self._ridge("m", lam))
            for lam in self.params.ridge_total:
                self.ratings_t.append(q + self._ridge("t", lam))

    def _box_rates(self, idx: np.ndarray) -> np.ndarray:
        k = self.params.prior_minutes
        mins = self._scale * self._min_raw[idx]
        box = self._scale * self._box_raw[idx]
        return (48.0 * box + k * self.fringe) / (mins + k)[:, None]

    def _ridge(self, key: str, lam: float) -> np.ndarray:
        n = self.n_players
        out = np.zeros(n)
        if not math.isfinite(lam) or n == 0:
            return out
        S = self._S[key]
        diag = self._scale * np.diag(S)[:n]
        active = np.flatnonzero(diag > ACTIVE_DIAG)
        if active.size == 0:
            return out
        from scipy.linalg import solve
        from scipy.sparse.linalg import cg

        A = self._scale * S[np.ix_(active, active)]
        A[np.diag_indices_from(A)] += lam
        rhs = self._scale * self._b[key][active]
        # Conjugate gradient warm-started from the previous day's solution: the system moves
        # little day to day, and a dense solve of ~1,000 players costs ~0.3 s per day per
        # penalty on this host. Tolerance 1e-10 makes it the same solution; direct solve if
        # CG does not converge.
        warm = self._warm.setdefault((key, lam), np.zeros(0))
        x0 = np.zeros(active.size)
        known = active < warm.size
        x0[known] = warm[active[known]]
        sol, info = cg(A, rhs, x0=x0, rtol=1e-10, atol=0.0, maxiter=2000)
        if info != 0:
            sol = solve(A, rhs, assume_a="pos")
        out[active] = sol
        self._warm[(key, lam)] = out.copy()
        return out

    def _baselines(self, day: str) -> tuple[float, float]:
        if not self.league:
            return 0.0, 0.0
        season = season_year_of(day)
        current = sum(1 for s, _, _ in reversed(self.league) if s == season)
        window = self.league[-max(current, BASELINE_MIN_GAMES) :]
        pace = float(np.mean([w[1] for w in window]))
        eff = float(np.mean([w[2] for w in window]))
        return eff, pace

    def box_rates_of(self, player_id: str) -> np.ndarray:
        idx = self.index.get(player_id)
        if idx is None or self._z_today is None or idx >= len(self._z_today):
            return self.fringe.copy()
        return self._z_today[idx]

    def rating(self, player_id: str, market: str, which: int = 0) -> float:
        """Rating as of ``today`` (a player first seen today gets the fringe prior)."""
        assert self.prior is not None
        ratings = self.ratings_m if market == "m" else self.ratings_t
        idx = self.index.get(player_id)
        if idx is not None and idx < len(ratings[which]):
            return float(ratings[which][idx])
        coef = self.prior.gamma if market == "m" else self.prior.delta
        return float(self.fringe @ np.asarray(coef))

    def replacement_rating(self, market: str) -> float:
        assert self.prior is not None
        coef = self.prior.gamma if market == "m" else self.prior.delta
        return float(self.fringe @ np.asarray(coef))

    def rest_flags(self, team_id: str, day: str) -> tuple[bool, bool]:
        """(back-to-back, third game in four days excluding back-to-back) from past schedule."""
        d = date.fromisoformat(day)
        past = [date.fromisoformat(e.date) for e in self.team_log.get(team_id, [])[-3:]]
        b2b = any((d - p).days == 1 for p in past)
        recent = sum(1 for p in past if 1 <= (d - p).days <= 3)
        return b2b, (recent >= 2 and not b2b)

    def pace_dev(self, team_id: str) -> float:
        hist = list(self.team_pace.get(team_id, ()))
        if not hist:
            return 0.0
        w = np.array([PACE_DECAY**k for k in range(len(hist))])[::-1]
        return float(np.sum(w * np.array(hist)) / (np.sum(w) + PACE_PSEUDO))

    def oracle_strength(self, game: GameBox, home: bool, market: str, which: int = 0) -> float:
        """HINDSIGHT diagnostic: rating sum with the game's ACTUAL minutes (never a decision input)."""
        side = game.side(home)
        total = sum(p.minutes for p in side)
        return sum(
            5.0 * p.minutes / total * self.rating(p.player_id, market, which) for p in side if total > 0
        )

    def box_aggregate(self, game: GameBox, home: bool) -> np.ndarray:
        """sum over the side of (actual minute share x 5) * pre-game box rates; for the prior fit."""
        side = game.side(home)
        total = sum(p.minutes for p in side)
        agg = np.zeros(N_STATS)
        for p in side:
            if p.minutes > 0:
                agg += 5.0 * p.minutes / total * self.box_rates_of(p.player_id)
        return agg

    # ---------------------------------------------------------------- minutes

    def team_status_reason(self, team_id: str, season_year: int) -> str | None:
        log = self.team_log.get(team_id, [])
        if sum(1 for e in log if e.has_box) < SHARE_WINDOW:
            return NO_CALL_HISTORY_SHORT
        current = [e for e in log[-SHARE_WINDOW:] if e.season_year == season_year]
        if sum(1 for e in current if not e.has_box) > BOX_GAP_MAX:
            return NO_CALL_BOX_GAP
        return None

    def candidates(self, team_id: str, season_year: int) -> list[Candidate]:
        assert self.minutes_model is not None
        log = self.team_log.get(team_id, [])
        n = len(log)
        current_games = sum(1 for e in log if e.season_year == season_year)
        out: list[Candidate] = []
        for pid in sorted(self.team_members.get(team_id, ())):
            tr = self.tracks[pid]
            if tr.team_index < n - ROSTER_WINDOW:
                continue
            if current_games >= 3 and tr.season_year != season_year:
                continue
            window = range(max(n - SHARE_WINDOW, tr.stint_start), n)
            if len(window) == 0:
                continue
            played = sum(1 for i in (tr.played_idx or ()) if i in window)
            share = played / len(window)
            mpg = _role_mpg(list(tr.recent or ()))
            out.append(Candidate(pid, tr.name, mpg, share))
        return out

    def project_team(
        self,
        team_id: str,
        season_year: int,
        report: TeamReport | None = None,
        *,
        which_m: int = 0,
        which_t: int = 0,
    ) -> TeamProjection:
        """Expected minutes and rating sums for one team.

        ``report`` None = ex-availability (status 'unknown' for everyone). With a report, listed
        players take their status; everyone else is 'unlisted'. Expected minutes e = P(play) x
        mpg; if the sum is under 240 the available players scale up to ``SCALE_CAP`` and the rest
        goes to a replacement-level player; over 240 scales down.
        """
        assert self.minutes_model is not None
        reason = self.team_status_reason(team_id, season_year)
        if reason is not None:
            return TeamProjection(reason)
        cands = self.candidates(team_id, season_year)
        if sum(1 for c in cands if c.mpg > 0) < MIN_CANDIDATES:
            return TeamProjection(NO_CALL_ROSTER_THIN)
        statuses: dict[str, str] = {}
        unmatched = 0
        if report is not None:
            if report.players is None:
                return TeamProjection(report.reason)
            matched, unmatched = match_report({c.player_id: c.name for c in cands}, report.players)
            if matched is None:
                return TeamProjection(NO_CALL_INJURY_NAME_AMBIGUOUS)
            statuses = matched
        default = "unknown" if report is None else "unlisted"
        expected = {
            c.player_id: self.minutes_model.play_probability(statuses.get(c.player_id, default), c.bucket)
            * c.mpg
            for c in cands
        }
        total = sum(expected.values())
        replacement = 0.0
        if total > TEAM_MINUTES:
            factor = TEAM_MINUTES / total
        else:
            factor = min(SCALE_CAP, TEAM_MINUTES / total) if total > 0 else 0.0
            replacement = TEAM_MINUTES - factor * total
        minutes = {pid: e * factor for pid, e in expected.items()}
        strength = sum(m / 48.0 * self.rating(pid, "m", which_m) for pid, m in minutes.items())
        impact = sum(m / 48.0 * self.rating(pid, "t", which_t) for pid, m in minutes.items())
        strength += replacement / 48.0 * self.replacement_rating("m")
        impact += replacement / 48.0 * self.replacement_rating("t")
        return TeamProjection(None, strength, impact, replacement, len(cands), unmatched, minutes)

    # ---------------------------------------------------------------- updates

    def add_games(self, games: Sequence[GameBox]) -> None:
        """Append completed games of ``today``. Their rows only affect later days."""
        if self.today is None:
            raise ValueError("begin_day must be called before add_games")
        for g in games:
            if g.date != self.today:
                raise ValueError(f"game {g.event_id} dated {g.date} added on {self.today}")
        growth = self._growth(self.today)
        prior_z = self._z_today
        for g in games:
            self._add_game(g, growth, prior_z)

    def _add_game(self, g: GameBox, growth: float, prior_z: np.ndarray | None) -> None:
        for team in (g.home_id, g.away_id):
            self.team_log[team].append(TeamLogEntry(g.date, g.event_id, g.season_year, g.players is not None))
        if not g.players:
            return
        poss = g.possessions()
        margin = float(g.home_score - g.away_score)
        total = float(g.home_score + g.away_score)
        idx_list: list[int] = []
        x_list: list[float] = []
        sign: list[float] = []
        for home in (True, False):
            team = g.home_id if home else g.away_id
            team_idx = len(self.team_log[team]) - 1
            side = g.side(home)
            team_minutes = sum(p.minutes for p in side)
            for p in side:
                i = self._pid(p.player_id, p.name)
                self._update_track(p, team, team_idx, g, abs(margin))
                if p.minutes <= 0 or team_minutes <= 0:
                    continue
                self._box_raw[i] += growth * np.asarray(p.stats)
                self._min_raw[i] += growth * p.minutes
                idx_list.append(i)
                x_list.append(5.0 * p.minutes / team_minutes)
                sign.append(1.0 if home else -1.0)
        if poss is not None:
            self.league.append((g.season_year, poss, 100.0 * total / poss / 2.0))
            for team in (g.home_id, g.away_id):
                self.team_pace[team].append(poss - self.pace_base)
        if self.prior is None or not idx_list:
            return
        idx = np.array(idx_list)
        x = np.array(x_list)
        xm = x * np.array(sign)
        z = self._rates_at_game(idx, prior_z)
        p = z @ np.asarray(self.prior.gamma)
        q = z @ np.asarray(self.prior.delta)
        y_m = margin - self.prior.hca - float(xm @ p)
        self._S["m"][np.ix_(idx, idx)] += growth * np.outer(xm, xm)
        self._b["m"][idx] += growth * xm * y_m
        if poss is not None:
            y_t = 100.0 * total / poss - 2.0 * self.eff_base - self.prior.total_const - float(x @ q)
            self._S["t"][np.ix_(idx, idx)] += growth * np.outer(x, x)
            self._b["t"][idx] += growth * x * y_t

    def _rates_at_game(self, idx: np.ndarray, prior_z: np.ndarray | None) -> np.ndarray:
        out = np.tile(self.fringe, (len(idx), 1))
        if prior_z is not None:
            known = idx < len(prior_z)
            out[known] = prior_z[idx[known]]
        return out

    def _update_track(self, p: PlayerLine, team: str, team_idx: int, g: GameBox, abs_margin: float) -> None:
        tr = self.tracks[p.player_id]
        if tr.team != team:
            if tr.team:
                self.team_members[tr.team].discard(p.player_id)
            tr.team = team
            tr.stint_start = team_idx
            assert tr.played_idx is not None
            tr.played_idx.clear()
            self.team_members[team].add(p.player_id)
        tr.team_index = team_idx
        tr.season_year = g.season_year
        if p.played:
            assert tr.played_idx is not None and tr.recent is not None
            tr.played_idx.append(team_idx)
            factor = self.minutes_model.blowout_factor(p.starter, abs_margin) if self.minutes_model else 1.0
            tr.recent.append((p.minutes / factor, p.starter))


def _role_mpg(recent: list[tuple[float, bool]]) -> float:
    """Recency-weighted minutes over played games; restricted to the current role when the
    last game's role (starter/bench) has at least ``MIN_ROLE_GAMES`` games in the window."""
    if not recent:
        return 0.0
    role = recent[-1][1]
    same = [r for r in recent if r[1] == role]
    use = same if len(same) >= MIN_ROLE_GAMES else recent
    weights = np.array([MPG_DECAY**k for k in range(len(use))])[::-1]
    return float(np.sum(weights * np.array([m for m, _ in use])) / np.sum(weights))


def season_year_of(day: str) -> int:
    """ESPN season_year (2026 = the 2025-26 season) of an ET date."""
    d = date.fromisoformat(day)
    return d.year + 1 if d.month >= 8 else d.year


def by_date(games: Sequence[GameBox]) -> Iterator[tuple[str, list[GameBox]]]:
    groups: dict[str, list[GameBox]] = defaultdict(list)
    for g in games:
        groups[g.date].append(g)
    for day in sorted(groups):
        yield day, groups[day]


def walk(
    games: Sequence[GameBox],
    state: PlayerLayerState,
    *,
    start: str,
    end: str,
    on_day: Callable[[str, list[GameBox], PlayerLayerState], None],
) -> None:
    """Advance ``state`` through every game date < ``end``; ``on_day`` sees the state as of the
    day (before that day's games are added) for dates >= ``start``. Earlier dates only warm up."""
    if end > HOLDOUT_START:
        raise ValueError(f"walk end {end} reaches the locked holdout ({HOLDOUT_START})")
    for day, day_games in by_date(games):
        if day >= end:
            break
        live = day >= start
        state.begin_day(day, solve=live)
        if live:
            on_day(day, day_games, state)
        state.add_games(day_games)


def decision_time(game: GameBox) -> datetime:
    return game.start - DECISION_LEAD


def game_features(
    state: PlayerLayerState,
    game: GameBox,
    injuries: InjuryReportIndex | None = None,
    *,
    with_oracle: bool = False,
) -> dict[str, Any]:
    """Feature row for one game as of ``state.today`` (must equal the game's ET date).

    Variants: ``exavail`` (no availability input), ``avail`` (official report at tip - 60 min;
    only when ``injuries`` covers the date) and, for research only, ``oracle`` (HINDSIGHT actual
    minutes; never a decision input).
    """
    if state.today != game.date:
        raise ValueError(f"state is at {state.today}, game {game.event_id} is on {game.date}")
    assert state.prior is not None
    h_b2b, h_34 = state.rest_flags(game.home_id, game.date)
    a_b2b, a_34 = state.rest_flags(game.away_id, game.date)
    row: dict[str, Any] = {
        "event_id": game.event_id,
        "date": game.date,
        "season_year": game.season_year,
        "season_type": game.season_type,
        "playoff": game.playoff,
        "home_id": game.home_id,
        "away_id": game.away_id,
        "home_b2b": h_b2b,
        "away_b2b": a_b2b,
        "home_3in4": h_34,
        "away_3in4": a_34,
        "eff_base": state.eff_base,
        "pace_base": state.pace_base,
        "poss_hat": state.pace_base + state.pace_dev(game.home_id) + state.pace_dev(game.away_id),
        "total_const": state.prior.total_const,
        "variants": {},
    }
    variants: dict[str, Any] = row["variants"]
    variants["exavail"] = _variant(
        state.project_team(game.home_id, game.season_year), state.project_team(game.away_id, game.season_year)
    )
    if injuries is not None and injuries.covers(game.date):
        when = decision_time(game)
        reports = [
            injuries.team_report(game.date, game.away_name, game.home_name, name, when)
            for name in (game.home_name, game.away_name)
        ]
        variants["avail"] = _variant(
            state.project_team(game.home_id, game.season_year, reports[0]),
            state.project_team(game.away_id, game.season_year, reports[1]),
        )
    if with_oracle and game.players:
        variants["oracle"] = {
            "reason": None,
            "S_home": state.oracle_strength(game, True, "m"),
            "S_away": state.oracle_strength(game, False, "m"),
            "U_home": state.oracle_strength(game, True, "t"),
            "U_away": state.oracle_strength(game, False, "t"),
        }
    return row


def _variant(home: TeamProjection, away: TeamProjection) -> dict[str, Any]:
    reason = home.reason or away.reason
    if reason is not None:
        return {"reason": reason}
    return {
        "reason": None,
        "S_home": home.strength,
        "S_away": away.strength,
        "U_home": home.total_impact,
        "U_away": away.total_impact,
        "repl_home": home.replacement_minutes,
        "repl_away": away.replacement_minutes,
        "unmatched": home.unmatched_report_rows + away.unmatched_report_rows,
    }
