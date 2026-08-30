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
  leakage), shrunk per-player;
- starter rest in days since the starter's own last start (PIT from
  strictly-prior starts; the only 2026-08-26 candidate lever that
  transferred to the locked holdout).

The 2026-08-26 improvement session also emits into the ledger (but
deliberately excludes from ``FEATURE_NAMES``) the PA-weighted same-hand
platoon share of each team's recent pool vs the opposing starter's recorded
hand, and each team's elapsed rest since its last prior game. Both were
val-rejected (noise-level or wrong-sign on train-side selection), as were
the L2-C sweep, train-side Platt calibration, time-decay weighting, and
logit interactions; the full lever matrix with numbers lives in
``scripts/mlb_nrfi_first_inning_research.py``.

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
# Plate-umpire first-inning tendencies: the snapshots carry officials with
# roles, so a shrunk per-umpire first-inning run rate and YRFI rate are
# derivable PIT. Heavy shrinkage — an umpire's inning-1 sample is tiny.
UMPIRE_PRIOR_GAMES = 40.0
UMPIRE_FEATURE_NAMES = ("plate_ump_1st_runs", "plate_ump_yrfi_rate")

# Handedness is a fixed player attribute, so the platoon share has a stable
# league expectation: P(batter R)*P(starter R) + P(batter L)*P(starter L)
# over the snapshot history (batter R share 0.629, starter R share 0.724).
# The train-window value is recomputed into the frozen priors; this is the
# fallback when no train window is available.
LEAGUE_SAME_HAND_SHARE = 0.5580
# Modal MLB rest is one day between games (including 8pm->7pm back-to-backs,
# whose elapsed time rounds to 1.0 days). A fixed constant, not tuned.
REST_DEFAULT_DAYS = 1.0
REST_MAX_DAYS = 6.0

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
    "away_starter_days_rest",
    "home_starter_days_rest",
]

_LEDGER_CACHE: dict[tuple[Path, bool], list[FirstInningGameRow]] = {}


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
    # Half-inning outcomes (for the two-half-inning model); defaults keep
    # cached ledgers and existing tests valid.
    runs_1st_away: float = 0.0
    runs_1st_home: float = 0.0


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


@dataclass
class MLBHalfInningModel:
    """Two-half-inning zero-run model (the plan's challenger 2).

    Fits two logistic regressions on the same standardized feature block —
    P(away half zero) and P(home half zero) — and combines them as
    P(NRFI) = P(away=0) × P(home=0). The independence approximation is the
    plan's stated starting point; shared environmental factors (park,
    weather regime) mean the halves are not truly independent, so this is
    a deliberately simple structural challenger to the single classifier.
    """

    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))
    coef_away: list[float] = field(default_factory=list)
    intercept_away: float = 0.0
    coef_home: list[float] = field(default_factory=list)
    intercept_home: float = 0.0
    scaler_mean: list[float] = field(default_factory=list)
    scaler_scale: list[float] = field(default_factory=list)
    C: float = 1.0
    random_state: int = 42
    fit_n_games: int = 0

    def fit(self, rows: list[FirstInningGameRow]) -> MLBHalfInningModel:
        """Fit both half regressions on the walk-forward train split only."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        x = _rows_to_matrix(rows, self.feature_names)
        y_away = [1 if r.runs_1st_away == 0 else 0 for r in rows]
        y_home = [1 if r.runs_1st_home == 0 else 0 for r in rows]
        scaler = StandardScaler()
        x_std = scaler.fit_transform(x)
        lr_away = LogisticRegression(C=self.C, max_iter=2000, random_state=self.random_state)
        lr_home = LogisticRegression(C=self.C, max_iter=2000, random_state=self.random_state)
        lr_away.fit(x_std, y_away)
        lr_home.fit(x_std, y_home)
        self.coef_away = [float(c) for c in lr_away.coef_[0]]
        self.intercept_away = float(lr_away.intercept_[0])
        self.coef_home = [float(c) for c in lr_home.coef_[0]]
        self.intercept_home = float(lr_home.intercept_[0])
        self.scaler_mean = [float(v) for v in scaler.mean_]
        self.scaler_scale = [float(v) for v in scaler.scale_]
        self.fit_n_games = len(rows)
        return self

    def _half_logit(self, row: FirstInningGameRow, coef: list[float], intercept: float) -> float:
        logit = intercept
        for name, c, mean, scale in zip(
            self.feature_names, coef, self.scaler_mean, self.scaler_scale, strict=True
        ):
            x = float(row.features.get(name, mean))
            logit += c * ((x - mean) / scale)
        return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, logit))))

    def predict_p_nrfi(self, row: FirstInningGameRow) -> float:
        p_away0 = self._half_logit(row, self.coef_away, self.intercept_away)
        p_home0 = self._half_logit(row, self.coef_home, self.intercept_home)
        return p_away0 * p_home0

    def predict_p_yrfi(self, row: FirstInningGameRow) -> float:
        return 1.0 - self.predict_p_nrfi(row)


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


def _top3_same_hand_share(
    team: str,
    pool: list[tuple[datetime, dict[int, float]]],
    player_bat_side: dict[int, str],
    starter_hand: str | None,
    prior_share: float,
) -> float:
    """PA-weighted share of the team's recent-pool batters who bat from the
    same side as the opposing starter (platoon disadvantage for the hitters).

    Switch hitters (``bat_side == "S"``) can always flip to the favorable
    side, so they never count toward the pitcher's edge; batters with no
    recorded side are dropped from both numerator and denominator. When the
    starter's hand or any hitter side is unknown, the expected share under
    the league R/L distributions is returned (no information -> prior).
    """
    if starter_hand not in ("R", "L"):
        return prior_share
    recent = pool[-TEAM_POOL_LOOKBACK_GAMES:]
    pa_by_pid: dict[int, float] = {}
    for _, participants in recent:
        for pid, pa in participants.items():
            pa_by_pid[pid] = pa_by_pid.get(pid, 0.0) + pa
    known_pa = 0.0
    match_pa = 0.0
    for pid, pa in pa_by_pid.items():
        side = player_bat_side.get(pid)
        if side not in ("R", "L"):
            continue
        known_pa += pa
        if side == starter_hand:
            match_pa += pa
    if known_pa <= 0:
        return prior_share
    return match_pa / known_pa


def _days_rest(last_date: datetime | None, start: datetime, default: float) -> float:
    """Elapsed days since the team's last strictly-prior game, rounded so an
    8pm-to-7pm back-to-back (23h) reads as 1 day while a doubleheader (5h)
    reads as 0; capped for long breaks (ASG, off weeks)."""
    if last_date is None:
        return default
    days = round((start - last_date).total_seconds() / 86400.0)
    return min(REST_MAX_DAYS, max(0.0, float(days)))


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
        "yrfi_rate": 1.0 - 0.5106,  # league NRFI rate complement
        "fip": 4.10,
        "k_pct": 0.22,
        "bb_pct": 0.08,
        "prod": 0.32,
        "disc": -0.10,
        "pow": 0.16,
        "same_hand": LEAGUE_SAME_HAND_SHARE,
        "n_games": 0,
    }
    sums = {
        "half_away": 0.0,
        "half_home": 0.0,
        "total": 0.0,
        "yrfi": 0.0,
        "yrfi_n": 0.0,
        "ip": 0.0,
        "so": 0.0,
        "bb": 0.0,
        "hr": 0.0,
        "bf": 0.0,
        "prod": 0.0,
        "disc": 0.0,
        "pow": 0.0,
        "pa": 0.0,
        "bat_r": 0.0,
        "bat_l": 0.0,
        "st_r": 0.0,
        "st_l": 0.0,
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
            yrfi_flag = snap.get("yrfi")
            if yrfi_flag is not None:
                sums["yrfi"] += float(yrfi_flag)
                sums["yrfi_n"] += 1.0
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
                    if player.get("pitching_order") == 1:
                        hand = player.get("pitch_hand")
                        if hand == "R":
                            sums["st_r"] += 1.0
                        elif hand == "L":
                            sums["st_l"] += 1.0
                    batting = player.get("batting") or {}
                    pa = float(batting.get("plateAppearances") or 0.0)
                    if pa > 0:
                        b_side = player.get("bat_side")
                        if b_side == "R":
                            sums["bat_r"] += pa
                        elif b_side == "L":
                            sums["bat_l"] += pa
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
        # YRFI rate from the snapshot's own flag when present; the loop
        # counts it below via sums["yrfi"]/sums["yrfi_n"].
        if sums["yrfi_n"] > 0:
            priors["yrfi_rate"] = sums["yrfi"] / sums["yrfi_n"]
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
    bat_total = sums["bat_r"] + sums["bat_l"]
    st_total = sums["st_r"] + sums["st_l"]
    if bat_total > 0 and st_total > 0:
        p_bat_r = sums["bat_r"] / bat_total
        p_st_r = sums["st_r"] / st_total
        priors["same_hand"] = p_bat_r * p_st_r + (1.0 - p_bat_r) * (1.0 - p_st_r)
    return priors


def _plate_umpire(officials: list[dict[str, Any]]) -> str | None:
    """Home-plate umpire name from the snapshot's officials list (role-typed)."""
    for official in officials:
        if str(official.get("type") or "").casefold() == "home plate":
            name = official.get("name")
            return str(name) if name else None
    return None


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
                    # None when the field is absent — the accumulator must
                    # not record missing data as a 0 outcome.
                    "yrfi_flag": int(snap.get("yrfi")) if snap.get("yrfi") is not None else None,
                    "plate_umpire": _plate_umpire(snap.get("officials") or []),
                    "players": (home.get("players") or []) + (away.get("players") or []),
                }
            )
    snaps.sort(key=lambda s: s["start"])
    return snaps


def build_first_inning_ledger(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    *,
    priors: dict[str, float] | None = None,
    include_umpires: bool = False,
) -> list[FirstInningGameRow]:
    """Build the PIT feature ledger with one chronological pass.

    Every feature for a game uses only games strictly before it; the game's
    own boxscore is appended to the entity accumulators only after its
    features are emitted. Cached by (path, include_umpires) — the umpire
    features append to the vector, so the two ledgers must never share a
    cache slot.
    """
    path = Path(snapshot_path)
    if (path, include_umpires) in _LEDGER_CACHE and priors is None:
        return _LEDGER_CACHE[(path, include_umpires)]
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
    player_bat_side: dict[int, str] = {}  # pid -> "R"/"L"/"S", from prior games only
    player_pitch_hand: dict[int, str] = {}  # pid -> "R"/"L", from prior games only
    team_last_date: dict[str, datetime] = {}  # team -> start of last prior game
    starter_last_date: dict[int, datetime] = {}  # starter pid -> start of last prior start
    umpires: dict[str, list[float]] = {}  # plate ump name -> [n, sum_1st_runs, sum_yrfi]

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

        # First-inning starter suppression: opponent first-inning runs/start.
        # NOTE (shrinkage review, 2026-08-26): the away starter's runs are
        # scored in the home half, so the strict league target would be
        # priors["half_home"] rather than half_away; an opponent-conditional
        # target was tested and measured neutral on val AND test, so the
        # incumbent target is left unchanged to keep the candidate
        # byte-identical to the baseline that beat the incumbent.
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

        # Platoon interaction: PA-weighted share of each team's recent pool
        # batting from the same side as the opposing starter (a fixed player
        # attribute read from strictly-prior games only).
        # The away team's batters face the home starter (and vice versa).
        away_hand_share = _top3_same_hand_share(
            away_team,
            team_pool.get(away_team, []),
            player_bat_side,
            player_pitch_hand.get(int(home_sid)) if home_sid is not None else None,
            priors["same_hand"],
        )
        home_hand_share = _top3_same_hand_share(
            home_team,
            team_pool.get(home_team, []),
            player_bat_side,
            player_pitch_hand.get(int(away_sid)) if away_sid is not None else None,
            priors["same_hand"],
        )

        # Team rest (elapsed days since last strictly-prior game).
        away_rest = _days_rest(team_last_date.get(away_team), snap["start"], REST_DEFAULT_DAYS)
        home_rest = _days_rest(team_last_date.get(home_team), snap["start"], REST_DEFAULT_DAYS)

        # Starter rest (days since the starter's own last start) -- the
        # rotation-specific version of rest; short-rest starts (3 days or a
        # bullpen game) are the signal here, PIT from strictly-prior starts.
        away_st_rest = (
            _days_rest(starter_last_date.get(int(away_sid)), snap["start"], 4.0)
            if away_sid is not None
            else 4.0
        )
        home_st_rest = (
            _days_rest(starter_last_date.get(int(home_sid)), snap["start"], 4.0)
            if home_sid is not None
            else 4.0
        )

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
            "away_top3_same_hand_share": round(away_hand_share, 4),
            "home_top3_same_hand_share": round(home_hand_share, 4),
            "away_team_days_rest": round(away_rest, 2),
            "home_team_days_rest": round(home_rest, 2),
            "away_starter_days_rest": round(away_st_rest, 2),
            "home_starter_days_rest": round(home_st_rest, 2),
        }
        if include_umpires:
            # Plate-umpire tendency block, shrunk hard (tiny per-umpire
            # inning-1 samples). Unknown ump -> pure league prior.
            ump = umpires.get(snap["plate_umpire"]) if snap["plate_umpire"] else None
            if ump is not None and ump[0] > 0:
                ump_1st = _shrink(ump[1] / ump[0], int(ump[0]), UMPIRE_PRIOR_GAMES, priors["total"])
                ump_yrfi = (
                    _shrink(ump[2] / ump[3], int(ump[3]), UMPIRE_PRIOR_GAMES, priors["yrfi_rate"])
                    if ump[3] > 0
                    else priors["yrfi_rate"]
                )
            else:
                ump_1st = priors["total"]
                ump_yrfi = priors["yrfi_rate"]
            features["plate_ump_1st_runs"] = round(ump_1st, 4)
            features["plate_ump_yrfi_rate"] = round(ump_yrfi, 4)

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
                runs_1st_away=snap["runs_1st_away"],
                runs_1st_home=snap["runs_1st_home"],
            )
        )

        # --- Umpire accumulators (strictly-after, like every other entity).
        if snap["plate_umpire"]:
            ump = umpires.setdefault(snap["plate_umpire"], [0.0, 0.0, 0.0, 0.0])
            ump[0] += 1.0
            ump[1] += runs_total
            if snap["yrfi_flag"] is not None:
                ump[2] += float(snap["yrfi_flag"])
                ump[3] += 1.0

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
                    hand = player.get("pitch_hand")
                    if hand:
                        player_pitch_hand[pid] = hand
                    starter_last_date[pid] = snap["start"]
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
            side = player.get("bat_side")
            if side:
                player_bat_side[pid_i] = side
        for team in (home_team, away_team):
            pool = team_pool.setdefault(team, [])
            pool.append((snap["start"], participants))
            team_last_date[team] = snap["start"]

    if priors is None:
        _LEDGER_CACHE[(path, include_umpires)] = rows
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
