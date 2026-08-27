"""MLB first-inning (NRFI/YRFI) model with genuinely first-inning features.

Research-only improvement over ``mlb_nrfi.py``. The v1 model's decomposed
component was driven by full-game starter FIP scaled against a league
first-inning run constant that was 2x too low (0.52 total vs 1.036 measured
across the 6,683 Stats API snapshots), which systematically deflated
``p_nrfi``; and its logit weights were hand-set, never fitted.

This module builds a chronological per-game ledger where every feature is a
point-in-time function of strictly-prior games only:

- starting pitcher's opponent first-inning runs allowed per start (the only
  truly first-inning pitcher signal available in the snapshots), shrunk;
- team first-inning runs scored / allowed split by home and away half (the
  snapshots show a real home-half advantage: 0.566 vs 0.470 runs/half);
- park first-inning run rate (venue, shrunk);
- starter full-game FIP / K% / BB% (rolling, shrunk);
- PA-share-weighted top-of-order offense composite (the recent-player-pool
  method from ``features/batter_offense.py`` -- no confirmed-lineup
  leakage), shrunk per-player.

The outcome is NRFI (zero runs in the first inning). A logistic regression
is fitted on the walk-forward train split only; the model object carries its
own standardized coefficients so predictions need no sklearn at call time.

Serving path is untouched: this module and its research script are
research-only. If the daily path ever wires it in, league priors must be
frozen at training time (see ``compute_first_inning_priors``).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..domain import parse_utc

DEFAULT_SNAPSHOT_PATH = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"

# Credibility-shrinkage prior masses (stabilization-point style constants,
# mirroring PITCHER_PRIOR_STARTS in features/yrfi_nrfi.py and the batter
# priors in features/batter_offense.py -- fixed, not tuned on holdout).
STARTER_PRIOR_STARTS = 15.0
TEAM_PRIOR_GAMES = 20.0
PARK_PRIOR_GAMES = 60.0
PRODUCTION_PRIOR_PA = 200.0
DISCIPLINE_PRIOR_PA = 120.0
POWER_PRIOR_PA = 160.0
TEAM_POOL_LOOKBACK_GAMES = 10
MIN_STARTER_IP_FOR_FIP = 3.0

FEATURE_NAMES = [
    "away_starter_opp_1st_runs",
    "home_starter_opp_1st_runs",
    "away_team_1st_scored_away",
    "home_team_1st_scored_home",
    "away_team_1st_allowed_away",
    "home_team_1st_allowed_home",
    "park_1st_runs",
    "away_starter_fip",
    "home_starter_fip",
    "away_starter_k_pct",
    "home_starter_k_pct",
    "away_starter_bb_pct",
    "home_starter_bb_pct",
    "away_top3_composite",
    "home_top3_composite",
    "away_starter_starts",
    "home_starter_starts",
]

_LEDGER_CACHE: dict[Path, list[FirstInningGameRow]] = {}


@dataclass(frozen=True)
class FirstInningGameRow:
    """One game's first-inning feature vector plus its realized outcome.

    ``features`` holds only point-in-time values: every entity statistic is
    computed from games strictly preceding ``game_start_utc``.
    """

    game_pk: int | None
    game_start_utc: str
    home_team: str
    away_team: str
    venue_name: str
    features: dict[str, float]
    nrfi: int
    runs_1st_total: float


@dataclass
class MLBFirstInningModel:
    """Fitted logistic-regression first-inning model (standardized inputs).

    Stores its own coefficients and scaler, so ``predict_p_nrfi`` needs no
    sklearn instance; ``fit`` requires it for training.
    """

    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))
    coef: list[float] = field(default_factory=list)
    intercept: float = 0.0
    scaler_mean: list[float] = field(default_factory=list)
    scaler_scale: list[float] = field(default_factory=list)
    C: float = 1.0
    random_state: int = 42
    fit_n_games: int = 0
    train_nrfi_rate: float = 0.5106

    def fit(self, rows: list[FirstInningGameRow]) -> MLBFirstInningModel:
        """Fit on the walk-forward train split only."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        x = _rows_to_matrix(rows, self.feature_names)
        y = [r.nrfi for r in rows]
        scaler = StandardScaler()
        x_std = scaler.fit_transform(x)
        lr = LogisticRegression(C=self.C, max_iter=2000, random_state=self.random_state)
        lr.fit(x_std, y)
        self.coef = [float(c) for c in lr.coef_[0]]
        self.intercept = float(lr.intercept_[0])
        self.scaler_mean = [float(v) for v in scaler.mean_]
        self.scaler_scale = [float(v) for v in scaler.scale_]
        self.fit_n_games = len(rows)
        self.train_nrfi_rate = sum(y) / len(y) if y else 0.5106
        return self

    def predict_p_nrfi(self, row: FirstInningGameRow) -> float:
        """Sigmoid over the standardized linear combination, self-contained."""
        logit = self.intercept
        for name, coef, mean, scale in zip(
            self.feature_names, self.coef, self.scaler_mean, self.scaler_scale, strict=True
        ):
            x = float(row.features.get(name, mean))
            logit += coef * ((x - mean) / scale)
        return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, logit))))

    def predict(self, row: FirstInningGameRow) -> dict[str, Any]:
        p_nrfi = self.predict_p_nrfi(row)
        return {
            "p_nrfi": round(p_nrfi, 4),
            "p_yrfi": round(1.0 - p_nrfi, 4),
            "features": dict(row.features),
            "model_version": "mlb-first-inning-v1",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": "mlb-first-inning-v1",
            "feature_names": self.feature_names,
            "coef": self.coef,
            "intercept": self.intercept,
            "scaler_mean": self.scaler_mean,
            "scaler_scale": self.scaler_scale,
            "C": self.C,
            "random_state": self.random_state,
            "fit_n_games": self.fit_n_games,
            "train_nrfi_rate": self.train_nrfi_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MLBFirstInningModel:
        return cls(
            feature_names=list(data.get("feature_names", FEATURE_NAMES)),
            coef=[float(v) for v in data.get("coef", [])],
            intercept=float(data.get("intercept", 0.0)),
            scaler_mean=[float(v) for v in data.get("scaler_mean", [])],
            scaler_scale=[float(v) for v in data.get("scaler_scale", [])],
            C=float(data.get("C", 1.0)),
            random_state=int(data.get("random_state", 42)),
            fit_n_games=int(data.get("fit_n_games", 0)),
            train_nrfi_rate=float(data.get("train_nrfi_rate", 0.5106)),
        )


def _rows_to_matrix(rows: list[FirstInningGameRow], feature_names: list[str]) -> list[list[float]]:
    return [[float(row.features.get(name, 0.0)) for name in feature_names] for row in rows]


def _shrunk_batter_rate(batter: list[float], idx: int, prior_pa: float, league: float) -> float:
    """Credibility-shrunk per-PA batter rate; pure league prior when no PAs."""
    if batter[0] <= 0:
        return league
    credibility = batter[0] / (batter[0] + prior_pa)
    return credibility * (batter[idx] / batter[0]) + (1.0 - credibility) * league


def _top3_composite(
    team: str,
    batters: dict[int, list[float]],
    pool: list[tuple[datetime, dict[int, float]]],
    priors: dict[str, float],
) -> float:
    """PA-share-weighted top-of-order offense from the team's recent pool.

    Same recent-player-pool method as ``batter_offense.team_offense_pit_profile``
    (no confirmed-lineup leakage); each pool member's rates are their
    career-to-date shrunk priors as of the decision time.
    """
    recent = pool[-TEAM_POOL_LOOKBACK_GAMES:]
    pa_by_pid: dict[int, float] = {}
    for _, participants in recent:
        for pid, pa in participants.items():
            pa_by_pid[pid] = pa_by_pid.get(pid, 0.0) + pa
    total_pa = sum(pa_by_pid.values())
    if total_pa <= 0:
        return (priors["prod"] + priors["disc"] + priors["pow"]) / 3.0
    prod = disc = powr = 0.0
    for pid, pa in pa_by_pid.items():
        weight = pa / total_pa
        b = batters.get(pid, [0.0] * 4)
        prod += weight * _shrunk_batter_rate(b, 1, PRODUCTION_PRIOR_PA, priors["prod"])
        disc += weight * _shrunk_batter_rate(b, 2, DISCIPLINE_PRIOR_PA, priors["disc"])
        powr += weight * _shrunk_batter_rate(b, 3, POWER_PRIOR_PA, priors["pow"])
    return (prod + disc + powr) / 3.0


def _shrink(raw: float, n: int, prior_n: float, league: float) -> float:
    if n <= 0:
        return league
    credibility = n / (n + prior_n)
    return credibility * raw + (1.0 - credibility) * league


def compute_first_inning_priors(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    *,
    end_utc: datetime | None = None,
) -> dict[str, float]:
    """League priors for shrinkage, optionally restricted to games before
    ``end_utc`` (the research script freezes them on the train window)."""
    priors: dict[str, float] = {
        "half_away": 0.4698,
        "half_home": 0.5662,
        "total": 1.036,
        "fip": 4.10,
        "k_pct": 0.22,
        "bb_pct": 0.08,
        "prod": 0.32,
        "disc": -0.10,
        "pow": 0.16,
        "n_games": 0,
    }
    sums = {
        "half_away": 0.0,
        "half_home": 0.0,
        "total": 0.0,
        "ip": 0.0,
        "so": 0.0,
        "bb": 0.0,
        "hr": 0.0,
        "bf": 0.0,
        "prod": 0.0,
        "disc": 0.0,
        "pow": 0.0,
        "pa": 0.0,
    }
    n_games = 0
    path = Path(snapshot_path)
    if not path.exists():
        return priors

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                start = parse_utc(str(snap["game_start_utc"]))
            except (KeyError, ValueError):
                continue
            if end_utc is not None and start >= end_utc:
                continue
            away = float(snap.get("first_inning_runs_away") or 0.0)
            home = float(snap.get("first_inning_runs_home") or 0.0)
            sums["half_away"] += away
            sums["half_home"] += home
            sums["total"] += away + home
            n_games += 1
            for side_key in ("home", "away"):
                side = snap.get(side_key) or {}
                for player in side.get("players", []):
                    pitching = player.get("pitching") or {}
                    ip = _parse_ip(pitching.get("inningsPitched"))
                    sums["ip"] += ip
                    sums["so"] += float(pitching.get("strikeOuts") or 0.0)
                    sums["bb"] += float(pitching.get("baseOnBalls") or 0.0)
                    sums["hr"] += float(pitching.get("homeRuns") or 0.0)
                    sums["bf"] += float(pitching.get("battersFaced") or 0.0)
                    batting = player.get("batting") or {}
                    pa = float(batting.get("plateAppearances") or 0.0)
                    if pa > 0:
                        hits = float(batting.get("hits") or 0.0)
                        walks = float(batting.get("baseOnBalls") or 0.0)
                        hbp = float(batting.get("hitByPitch") or 0.0)
                        so_b = float(batting.get("strikeOuts") or 0.0)
                        tb = float(batting.get("totalBases") or hits)
                        sums["pa"] += pa
                        sums["prod"] += hits + walks + hbp
                        sums["disc"] += walks - so_b
                        sums["pow"] += tb - hits

    if n_games:
        priors["half_away"] = sums["half_away"] / n_games
        priors["half_home"] = sums["half_home"] / n_games
        priors["total"] = sums["total"] / n_games
        priors["n_games"] = float(n_games)
    if sums["ip"] > 0:
        priors["fip"] = (13.0 * sums["hr"] + 3.0 * sums["bb"] - 2.0 * sums["so"]) / max(
            1.0, sums["ip"]
        ) + 3.10
    if sums["bf"] > 0:
        priors["k_pct"] = sums["so"] / sums["bf"]
        priors["bb_pct"] = sums["bb"] / sums["bf"]
    if sums["pa"] > 0:
        priors["prod"] = sums["prod"] / sums["pa"]
        priors["disc"] = sums["disc"] / sums["pa"]
        priors["pow"] = sums["pow"] / sums["pa"]
    return priors


def _parse_ip(innings_pitched: str | float | None) -> float:
    if innings_pitched is None:
        return 0.0
    if isinstance(innings_pitched, (int, float)):
        return float(innings_pitched)
    s = str(innings_pitched).strip()
    if not s:
        return 0.0
    whole, _, frac = s.partition(".")
    frac_map = {"0": 0.0, "1": 1.0 / 3.0, "2": 2.0 / 3.0}
    return float(whole or 0) + frac_map.get(frac, 0.0)


def _load_raw_snapshots(snapshot_path: str | Path) -> list[dict[str, Any]]:
    """Parse the snapshot file once, sorted chronologically."""
    path = Path(snapshot_path)
    snaps: list[dict[str, Any]] = []
    if not path.exists():
        return snaps
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                start = parse_utc(str(snap["game_start_utc"]))
            except (KeyError, ValueError):
                continue
            home = snap.get("home") or {}
            away = snap.get("away") or {}
            home_team = home.get("team_name")
            away_team = away.get("team_name")
            if not home_team or not away_team:
                continue
            snaps.append(
                {
                    "game_pk": snap.get("game_pk"),
                    "start": start,
                    "home_team": home_team,
                    "away_team": away_team,
                    "venue": snap.get("venue_name") or "",
                    "home_starter": (home.get("pitcher_order") or [None])[0],
                    "away_starter": (away.get("pitcher_order") or [None])[0],
                    "home_top3": (home.get("batting_order") or [])[:3],
                    "away_top3": (away.get("batting_order") or [])[:3],
                    "runs_1st_away": float(snap.get("first_inning_runs_away") or 0.0),
                    "runs_1st_home": float(snap.get("first_inning_runs_home") or 0.0),
                    "players": (home.get("players") or []) + (away.get("players") or []),
                }
            )
    snaps.sort(key=lambda s: s["start"])
    return snaps


def build_first_inning_ledger(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    *,
    priors: dict[str, float] | None = None,
) -> list[FirstInningGameRow]:
    """Build the PIT feature ledger with one chronological pass.

    Every feature for a game uses only games strictly before it; the game's
    own boxscore is appended to the entity accumulators only after its
    features are emitted. Cached by path.
    """
    path = Path(snapshot_path)
    if path in _LEDGER_CACHE and priors is None:
        return _LEDGER_CACHE[path]
    if priors is None:
        priors = compute_first_inning_priors(path)

    snaps = _load_raw_snapshots(path)
    rows: list[FirstInningGameRow] = []

    # Running accumulators (strictly-prior games only).
    starters: dict[int, list[float]] = {}  # pid -> [n, sum_opp_1st, sum_ip, sum_so, sum_bb, sum_bf, sum_hr]
    team_home: dict[str, list[float]] = {}  # team -> [n, sum_scored, sum_allowed]
    team_away: dict[str, list[float]] = {}  # team -> [n, sum_scored, sum_allowed]
    venues: dict[str, list[float]] = {}  # venue -> [n, sum_total_1st]
    batters: dict[int, list[float]] = {}  # pid -> [pa, prod, disc, pow]
    team_pool: dict[str, list[tuple[datetime, dict[int, float]]]] = {}  # team -> recent pools

    for snap in snaps:
        home_team = snap["home_team"]
        away_team = snap["away_team"]
        venue = snap["venue"]
        away_sid = snap["away_starter"]
        home_sid = snap["home_starter"]

        away_st = starters.get(int(away_sid), [0.0] * 7) if away_sid is not None else None
        home_st = starters.get(int(home_sid), [0.0] * 7) if home_sid is not None else None
        away_team_away = team_away.get(away_team, [0.0] * 3)
        home_team_home = team_home.get(home_team, [0.0] * 3)
        away_team_home_d = team_home.get(away_team, [0.0] * 3)
        home_team_away_d = team_away.get(home_team, [0.0] * 3)
        ven = venues.get(venue, [0.0] * 2)

        # First-inning starter suppression: opponent first-inning runs/start.
        away_st_opp_1st = (
            _shrink(away_st[1] / away_st[0], int(away_st[0]), STARTER_PRIOR_STARTS, priors["half_away"])
            if away_st and away_st[0] > 0
            else priors["half_away"]
        )
        home_st_opp_1st = (
            _shrink(home_st[1] / home_st[0], int(home_st[0]), STARTER_PRIOR_STARTS, priors["half_home"])
            if home_st and home_st[0] > 0
            else priors["half_home"]
        )

        # Team first-inning offense / defense, split by home and away half.
        away_scored_away = (
            _shrink(
                away_team_away[1] / away_team_away[0],
                int(away_team_away[0]),
                TEAM_PRIOR_GAMES,
                priors["half_away"],
            )
            if away_team_away[0] > 0
            else priors["half_away"]
        )
        home_scored_home = (
            _shrink(
                home_team_home[1] / home_team_home[0],
                int(home_team_home[0]),
                TEAM_PRIOR_GAMES,
                priors["half_home"],
            )
            if home_team_home[0] > 0
            else priors["half_home"]
        )
        away_allowed_away = (
            _shrink(
                away_team_home_d[2] / away_team_home_d[0],
                int(away_team_home_d[0]),
                TEAM_PRIOR_GAMES,
                priors["half_home"],
            )
            if away_team_home_d[0] > 0
            else priors["half_home"]
        )
        home_allowed_home = (
            _shrink(
                home_team_away_d[2] / home_team_away_d[0],
                int(home_team_away_d[0]),
                TEAM_PRIOR_GAMES,
                priors["half_away"],
            )
            if home_team_away_d[0] > 0
            else priors["half_away"]
        )
        park_1st = (
            _shrink(ven[1] / ven[0], int(ven[0]), PARK_PRIOR_GAMES, priors["total"])
            if ven[0] > 0
            else priors["total"]
        )

        # Starter full-game rolling FIP / K% / BB%.
        def _starter_rates(st: list[float] | None) -> tuple[float, float, float]:
            if st is None or st[0] <= 0 or st[2] <= MIN_STARTER_IP_FOR_FIP:
                return priors["fip"], priors["k_pct"], priors["bb_pct"]
            ip, so, bb, bf, hr = st[2], st[3], st[4], st[5], st[6]
            fip = (13.0 * hr + 3.0 * bb - 2.0 * so) / max(1.0, ip) + 3.10
            k_pct = so / bf if bf > 0 else so / max(1.0, 3.0 * ip)
            bb_pct = bb / bf if bf > 0 else bb / max(1.0, 3.0 * ip)
            return fip, k_pct, bb_pct

        away_fip, away_k, away_bb = _starter_rates(away_st)
        home_fip, home_k, home_bb = _starter_rates(home_st)

        # Top-of-order offense composite via the recent-player-pool method
        # (same PA-share weighting as batter_offense.team_offense_pit_profile).
        away_top3 = _top3_composite(away_team, batters, team_pool.get(away_team, []), priors)
        home_top3 = _top3_composite(home_team, batters, team_pool.get(home_team, []), priors)

        runs_total = snap["runs_1st_away"] + snap["runs_1st_home"]
        features: dict[str, float] = {
            "away_starter_opp_1st_runs": round(away_st_opp_1st, 4),
            "home_starter_opp_1st_runs": round(home_st_opp_1st, 4),
            "away_team_1st_scored_away": round(away_scored_away, 4),
            "home_team_1st_scored_home": round(home_scored_home, 4),
            "away_team_1st_allowed_away": round(away_allowed_away, 4),
            "home_team_1st_allowed_home": round(home_allowed_home, 4),
            "park_1st_runs": round(park_1st, 4),
            "away_starter_fip": round(away_fip, 3),
            "home_starter_fip": round(home_fip, 3),
            "away_starter_k_pct": round(away_k, 4),
            "home_starter_k_pct": round(home_k, 4),
            "away_starter_bb_pct": round(away_bb, 4),
            "home_starter_bb_pct": round(home_bb, 4),
            "away_top3_composite": round(away_top3, 5),
            "home_top3_composite": round(home_top3, 5),
            "away_starter_starts": round(math.log1p(away_st[0] if away_st else 0.0), 4),
            "home_starter_starts": round(math.log1p(home_st[0] if home_st else 0.0), 4),
        }

        rows.append(
            FirstInningGameRow(
                game_pk=snap["game_pk"],
                game_start_utc=snap["start"].isoformat(),
                home_team=home_team,
                away_team=away_team,
                venue_name=venue,
                features=features,
                nrfi=1 if runs_total == 0 else 0,
                runs_1st_total=runs_total,
            )
        )

        # --- Append this game to the accumulators (strictly-after discipline).
        for sid, opp_runs_key in (
            (away_sid, "runs_1st_away"),
            (home_sid, "runs_1st_home"),
        ):
            if sid is None:
                continue
            pid = int(sid)
            st = starters.setdefault(pid, [0.0] * 7)
            st[0] += 1.0
            st[1] += snap[opp_runs_key]
            for player in snap["players"]:
                if player.get("player_id") == pid:
                    p = player.get("pitching") or {}
                    st[2] += _parse_ip(p.get("inningsPitched"))
                    st[3] += float(p.get("strikeOuts") or 0.0)
                    st[4] += float(p.get("baseOnBalls") or 0.0)
                    st[5] += float(p.get("battersFaced") or 0.0)
                    st[6] += float(p.get("homeRuns") or 0.0)
                    break

        th = team_home.setdefault(home_team, [0.0] * 3)
        th[0] += 1.0
        th[1] += snap["runs_1st_home"]  # scored as home team
        th[2] += snap["runs_1st_away"]  # allowed as home team
        ta = team_away.setdefault(away_team, [0.0] * 3)
        ta[0] += 1.0
        ta[1] += snap["runs_1st_away"]  # scored as away team
        ta[2] += snap["runs_1st_home"]  # allowed as away team

        v = venues.setdefault(venue, [0.0] * 2)
        v[0] += 1.0
        v[1] += runs_total

        participants: dict[int, float] = {}
        for player in snap["players"]:
            batting = player.get("batting") or {}
            pa = float(batting.get("plateAppearances") or 0.0)
            pid = player.get("player_id")
            if pid is None or pa <= 0:
                continue
            pid_i = int(pid)
            hits = float(batting.get("hits") or 0.0)
            walks = float(batting.get("baseOnBalls") or 0.0)
            hbp = float(batting.get("hitByPitch") or 0.0)
            so = float(batting.get("strikeOuts") or 0.0)
            tb = float(batting.get("totalBases") or hits)
            b = batters.setdefault(pid_i, [0.0] * 4)
            b[0] += pa
            b[1] += hits + walks + hbp
            b[2] += walks - so
            b[3] += tb - hits
            participants[pid_i] = pa
        for team in (home_team, away_team):
            pool = team_pool.setdefault(team, [])
            pool.append((snap["start"], participants))

    if priors is None:
        _LEDGER_CACHE[path] = rows
    return rows


def market_proxy_probabilities(base_rate: float, *, vig: float = 0.04) -> tuple[float, float]:
    """Two-way no-vig fair prices around ``base_rate`` with a fixed overround.

    No real NRFI market quotes are captured anywhere in this repo (the
    ledger's ``market_probability`` column for mlb-nrfi-v1 is the model's own
    fair price, ``sportsbook="model_fair"``), so holdout CLV/P&L is measured
    against this explicit proxy: implied NRFI = (base + vig/2)/(1+vig).
    """
    if not 0.0 < base_rate < 1.0:
        raise ValueError("base_rate must be in (0, 1)")
    if not 0.0 <= vig < 1.0:
        raise ValueError("vig must be in [0, 1)")
    p_nrfi = (base_rate + vig / 2.0) / (1.0 + vig)
    p_yrfi = (1.0 - base_rate + vig / 2.0) / (1.0 + vig)
    return p_nrfi, p_yrfi
