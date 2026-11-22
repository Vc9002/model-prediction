"""NCAAB research candidates C1_ppp_adjusted and C2_ppp_into_v7 (protocol 261120, research only).

C1 (protocol spec, binding): opponent-adjusted offense/defense ratings in points per 100 possessions, solved
by ridge on every D1-vs-D1 team-game of the current season, venue-adjusted, point-in-time. Possessions per
game are the exact box formula ``FGA - OREB + TOV + 0.44 FTA`` averaged over both teams, for EVERY season
(2021-22..2025-26 box coverage of D1-vs-D1 games is 99.8-100%, so the attempts proxy is never used; the ~0.1%
of games without a box are left out of the fit). Adjusted tempo is a second ridge (possessions = league mean
+ T_home + T_away).

    eff_home = mu + O_home - D_away + venue * h / 2      (pts/100; D > 0 means a good defense)
    eff_away = mu + O_away - D_home - venue * h / 2
    margin   = (eff_home - eff_away) * poss / 100         (HCA counted once, through the ridge's h)
    total    = (eff_home + eff_away) * poss / 100

Free parameters, chosen per held-out season on its three training seasons only:
    ridge strength ``lam`` and carryover ``carry`` (grid below, selected by D1-vs-D1 margin RMSE),
    HCA (the ridge's venue term h, estimated point-in-time from history toward a fixed prior),
    t(8) scales per games-played bucket (maximum likelihood on the training residuals).
Constants fixed a priori (never tuned, declared in the sidecar):
    recency: Torvik T-Rank scheme, weight 1 to 40 days old, minus 1% per day to a 60% floor at 80 days;
    blowout: weight 1 up to a 20-point margin, minus 2% per extra point to a 60% floor;
    preseason prior: ``carry`` x the team's prior-season final rating + (1 - carry) x its conference tier
    prior (v7's as-of tier table, Elo/28 points, converted at 68 possessions, split evenly O/D); a team with
    no prior-season rating gets the tier prior alone. HCA prior 3.5 points (v7's), venue ridge weight 20.

C2 (protocol spec): OLS without intercept of [v7 ridge rg_c010_l02_k14, v7 tail, C1 margin] on the training
seasons' D1-vs-D1 games; t(8) scales per bucket re-fit on the training residuals.

Point in time: ratings for a game on Eastern day D use only games that tipped before Eastern midnight of D
(the grader's own cutoff, via bisect on tip time). The previous season's final ratings seed the next season.

Stages (repo root, ``OPENBLAS_NUM_THREADS=1``):
    .venv/Scripts/python scripts/ncaab_research/candidates/c_ppp_261120.py cache-boxes
    .venv/Scripts/python scripts/ncaab_research/candidates/c_ppp_261120.py ratings     # heavy, outcome-free
    .venv/Scripts/python scripts/ncaab_research/candidates/c_ppp_261120.py sanity
    .venv/Scripts/python scripts/ncaab_research/candidates/c_ppp_261120.py predict     # C1 + C2 files

Never reads a closing line. Writes only under outputs/ncaab/research_261120/{C1_ppp_adjusted,C2_ppp_into_v7}/.
"""

from __future__ import annotations

import argparse
import bisect
import functools
import gzip
import hashlib
import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
REPO = Path(__file__).resolve().parents[3]
for _p in (REPO / "src", REPO / "scripts", REPO / "scripts" / "ncaab_research"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

OUT = REPO / "outputs" / "ncaab" / "research_261120"
C1_DIR = OUT / "C1_ppp_adjusted"
C2_DIR = OUT / "C2_ppp_into_v7"
BOX_CACHE = C1_DIR / "boxes_d1_2022_2026.jsonl.gz"
COMPONENTS_OUT = C1_DIR / "c1_components.jsonl.gz"
FINALS_OUT = C1_DIR / "c1_final_ratings.json"
BOX_ROOTS = (
    REPO / "data" / "research" / "ncaab_external" / "espn_boxscores",  # 2021-22..2024-25
    REPO / "data" / "features" / "ncaab" / "boxscores",  # 2025-26 on (serving tree, read only)
)
LOCK_DATE_MIN = "2026-07-01"
DEV_SEASONS = (2023, 2024, 2025, 2026)
HISTORY_FIRST_SEASON = 2022

FT_WEIGHT = 0.44
POSS_MIN, POSS_MAX = 45.0, 110.0  # outside this a box is a data error (forfeit, partial box), never fitted
REF_POSS = 68.0  # converts tier priors (points per game) to per-100 units; fixed a priori
LAM_GRID = (1.0, 2.0, 4.0, 8.0)  # set before any outcome-based number: C1 at lam 3 was compressed vs v7 ridge
CARRY_GRID = (0.4, 0.6, 0.8)
HCA_PRIOR_POINTS = 3.5  # v7's ridge target for the home edge
HCA_LAMBDA = 20.0
MU_LAMBDA = 1e-6  # league-mean terms are effectively unpenalised
RECENCY_FULL_DAYS, RECENCY_FLOOR_DAYS, RECENCY_FLOOR = 40.0, 80.0, 0.6
BLOWOUT_KNOT, BLOWOUT_SLOPE, BLOWOUT_FLOOR = 20.0, 0.02, 0.6
BUCKET_CUTS = (1, 3, 8)  # games-played buckets 0 | 1-2 | 3-7 | 8+
TAIL_KNOT = 12.0
V7_VARIANT = "rg_c010_l02_k14"
ELO_PER_POINT = 28.0


def variant_key(lam: float, carry: float) -> str:
    return f"l{lam:g}_c{carry:g}"


VARIANTS = [(lam, carry) for lam in LAM_GRID for carry in CARRY_GRID]


# ------------------------------------------------------------------------------------------- pure functions
def team_possessions(fga: float, oreb: float, tov: float, fta: float) -> float:
    return fga - oreb + tov + FT_WEIGHT * fta


def game_possessions(home: Mapping[str, Any], away: Mapping[str, Any]) -> float:
    """Average of both sides' estimates (box payload field names)."""
    est = [
        team_possessions(
            float(s["field_goal_attempts"]),
            float(s["offensive_rebounds"]),
            float(s["turnovers"]),
            float(s["free_throw_attempts"]),
        )
        for s in (home, away)
    ]
    return 0.5 * (est[0] + est[1])


def recency_weight(age_days: np.ndarray) -> np.ndarray:
    """T-Rank: 1 up to 40 days, then -1%/day to a 60% floor at 80 days."""
    age = np.asarray(age_days, dtype=float)
    w = 1.0 - (age - RECENCY_FULL_DAYS) / (RECENCY_FLOOR_DAYS - RECENCY_FULL_DAYS) * (1.0 - RECENCY_FLOOR)
    return np.clip(w, RECENCY_FLOOR, 1.0)


def blowout_weight(margin: np.ndarray) -> np.ndarray:
    m = np.abs(np.asarray(margin, dtype=float))
    return np.clip(1.0 - BLOWOUT_SLOPE * np.maximum(m - BLOWOUT_KNOT, 0.0), BLOWOUT_FLOOR, 1.0)


def eastern_midnight_utc(day: str) -> datetime:
    from model_prediction.domain import EASTERN

    return datetime.combine(date.fromisoformat(day), dtime.min, tzinfo=EASTERN).astimezone(UTC)


def sample_bucket(sample: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.array(BUCKET_CUTS), np.asarray(sample), side="right")


def tail_term(v6_mean: np.ndarray) -> np.ndarray:
    v = np.asarray(v6_mean, dtype=float)
    return np.sign(v) * np.maximum(np.abs(v) - TAIL_KNOT, 0.0)


def tier_to_per100(tier_elo: np.ndarray) -> np.ndarray:
    """Tier Elo prior -> net efficiency gap per 100 possessions (to be split evenly between O and D)."""
    return (np.asarray(tier_elo, dtype=float) - 1500.0) / ELO_PER_POINT * 100.0 / REF_POSS


@dataclass
class Games:
    """Fitted history, one row per D1-vs-D1 game with a box (arrays sorted by tip time)."""

    start: list[datetime]
    day_ord: np.ndarray  # Eastern day ordinal
    season: np.ndarray
    hidx: np.ndarray
    aidx: np.ndarray
    hpts: np.ndarray
    apts: np.ndarray
    poss: np.ndarray
    venue: np.ndarray  # 1 home court, 0 neutral


def eff_system(
    g: Games, sel: np.ndarray, weights: np.ndarray, n: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Weighted normal equations (G, b) of the efficiency ridge and (Gt, bt) of the tempo ridge.

    Efficiency unknowns: O[0:n], D[n:2n], h (2n), mu (2n+1). Two observations per game:
    100*hpts/poss = mu + O_h - D_a + venue*h/2 and 100*apts/poss = mu + O_a - D_h - venue*h/2.
    Tempo unknowns: T[0:n], muT (n). One observation per game: poss = muT + T_h + T_a.
    """
    hi, ai = g.hidx[sel], g.aidx[sel]
    w = weights
    v = g.venue[sel] * 0.5
    m = 2 * n + 2
    G = np.zeros((m, m))
    b = np.zeros(m)
    ih, imu = 2 * n, 2 * n + 1
    for o_idx, d_idx, sgn, pts in ((hi, ai, 1.0, g.hpts[sel]), (ai, hi, -1.0, g.apts[sel])):
        y = 100.0 * pts / g.poss[sel]
        cols = (o_idx, n + d_idx, np.full_like(o_idx, ih), np.full_like(o_idx, imu))
        vals = (np.ones_like(y), -np.ones_like(y), sgn * v, np.ones_like(y))
        for c1, v1 in zip(cols, vals, strict=True):
            np.add.at(b, c1, w * v1 * y)
            for c2, v2 in zip(cols, vals, strict=True):
                np.add.at(G, (c1, c2), w * v1 * v2)
    Gt = np.zeros((n + 1, n + 1))
    bt = np.zeros(n + 1)
    p = g.poss[sel]
    tcols = (hi, ai, np.full_like(hi, n))
    for c1 in tcols:
        np.add.at(bt, c1, w * p)
        for c2 in tcols:
            np.add.at(Gt, (c1, c2), w)
    return G, b, Gt, bt


def solve_eff(
    G: np.ndarray, b: np.ndarray, n: int, lam: float, prior_o: np.ndarray, prior_d: np.ndarray, h0: float
) -> np.ndarray:
    reg = np.concatenate([np.full(2 * n, lam), [HCA_LAMBDA, MU_LAMBDA]])
    target = np.concatenate([prior_o, prior_d, [h0, 100.0]])
    return np.linalg.solve(G + np.diag(reg), b + reg * target)


def solve_tempo(Gt: np.ndarray, bt: np.ndarray, n: int, lam: float, prior_t: np.ndarray) -> np.ndarray:
    reg = np.concatenate([np.full(n, lam), [MU_LAMBDA]])
    target = np.concatenate([prior_t, [REF_POSS]])
    return np.linalg.solve(Gt + np.diag(reg), bt + reg * target)


def predict_game(
    eff: np.ndarray, tempo: np.ndarray, n: int, hi: int, ai: int, neutral: bool
) -> tuple[float, float, float, float, float]:
    """(margin, total, poss, eff_home, eff_away) from solved rating vectors."""
    o, d, h, mu = eff[:n], eff[n : 2 * n], eff[2 * n], eff[2 * n + 1]
    v = 0.0 if neutral else 0.5
    eh = mu + o[hi] - d[ai] + v * h
    ea = mu + o[ai] - d[hi] - v * h
    poss = tempo[n] + tempo[hi] + tempo[ai]
    return (eh - ea) * poss / 100.0, (eh + ea) * poss / 100.0, poss, eh, ea


def t8_scales_by_bucket(resid: np.ndarray, sample: np.ndarray) -> list[float]:
    from model_prediction.models.ncaab_v7_shadow import student_t_scale_mle

    b = sample_bucket(sample)
    return [float(student_t_scale_mle(resid[b == k], 8.0)) for k in range(len(BUCKET_CUTS) + 1)]


def sd_by_bucket(resid: np.ndarray, sample: np.ndarray) -> list[float]:
    b = sample_bucket(sample)
    return [float(np.sqrt(np.mean(resid[b == k] ** 2))) for k in range(len(BUCKET_CUTS) + 1)]


def ols_no_intercept(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(x, y, rcond=None)[0]


def select_variant(train: pd.DataFrame, keys: Iterable[str]) -> tuple[str, dict[str, float]]:
    """Lowest D1-vs-D1 margin RMSE over the training rows (which must carry 'margin' and c1_<key>)."""
    rm = {k: float(np.sqrt(np.mean((train["margin"] - train[f"c1_{k}"]) ** 2))) for k in keys}
    return min(rm, key=lambda k: (rm[k], k)), rm


# ------------------------------------------------------------------------------------------- data loading
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@functools.lru_cache(maxsize=1)
def _history_events() -> list[dict[str, Any]]:
    import grader

    return grader.load_history_events()


def _is_d1_event(e: Mapping[str, Any]) -> bool:
    return e.get("home_conference_id") is not None and e.get("away_conference_id") is not None


def cmd_cache_boxes(_args: argparse.Namespace) -> int:
    """One compact file of the box components of D1-vs-D1 history games (unlocked dates only)."""
    import grader

    events = {e["event_id"]: e for e in _history_events() if _is_d1_event(e)}
    rows: dict[str, dict[str, Any]] = {}
    for root in BOX_ROOTS:
        for path in sorted(root.glob("*/*.json")):
            if path.parent.name >= LOCK_DATE_MIN or path.stem not in events or path.stem in rows:
                continue
            p = json.loads(path.read_text(encoding="utf-8"))
            if p.get("schema") != "ncaab-boxscore-components-v1" or p.get("status") != "completed":
                continue
            day = grader.nr.et_day(p["event_start_utc"])
            if day >= LOCK_DATE_MIN:
                continue
            keep = ("team_id", "points", "field_goal_attempts", "free_throw_attempts")
            keep += ("offensive_rebounds", "turnovers")
            rows[path.stem] = {
                "event_id": path.stem,
                "home": {k: p["home"].get(k) for k in keep},
                "away": {k: p["away"].get(k) for k in keep},
            }
    C1_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(BOX_CACHE, "wt", encoding="utf-8") as fh:
        for eid in sorted(rows):
            fh.write(json.dumps(rows[eid], sort_keys=True) + "\n")
    print(f"cached {len(rows)} boxes of {len(events)} D1-vs-D1 events -> {BOX_CACHE}")
    print("sha256", sha256_file(BOX_CACHE))
    return 0


def load_boxes() -> dict[str, dict[str, Any]]:
    with gzip.open(BOX_CACHE, "rt", encoding="utf-8") as fh:
        return {r["event_id"]: r for r in map(json.loads, fh) if r}


def build_games(
    events: list[dict[str, Any]], boxes: Mapping[str, Mapping[str, Any]], team_index: dict[str, int]
) -> tuple[Games, dict[str, int]]:
    """Fit rows for D1-vs-D1 events with a usable box whose team ids match the scoreboard."""
    import grader

    rows = []
    drop = defaultdict(int)
    for e in events:
        if not _is_d1_event(e):
            continue
        bx = boxes.get(e["event_id"])
        if bx is None:
            drop["no_box"] += 1
            continue
        if (str(bx["home"]["team_id"]), str(bx["away"]["team_id"])) != (
            str(e["home_team_id"]),
            str(e["away_team_id"]),
        ):
            drop["team_id_mismatch"] += 1
            continue
        try:
            poss = game_possessions(bx["home"], bx["away"])
        except (TypeError, KeyError, ValueError):
            drop["box_field_missing"] += 1
            continue
        if not POSS_MIN <= poss <= POSS_MAX:
            drop["poss_out_of_range"] += 1
            continue
        if (int(bx["home"]["points"]), int(bx["away"]["points"])) != (
            int(e["home_score"]),
            int(e["away_score"]),
        ):
            drop["points_mismatch"] += 1
            continue
        start = datetime.strptime(e["start_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)
        rows.append(
            (
                start,
                date.fromisoformat(grader.nr.et_day(e["start_utc"])).toordinal(),
                int(e["season_year"]),
                team_index[str(e["home_team_id"])],
                team_index[str(e["away_team_id"])],
                float(e["home_score"]),
                float(e["away_score"]),
                poss,
                0.0 if e["neutral"] else 1.0,
            )
        )
    rows.sort(key=lambda r: r[0])
    cols = list(zip(*rows, strict=True))
    g = Games(
        start=list(cols[0]),
        day_ord=np.array(cols[1], dtype=int),
        season=np.array(cols[2], dtype=int),
        hidx=np.array(cols[3], dtype=int),
        aidx=np.array(cols[4], dtype=int),
        hpts=np.array(cols[5], dtype=float),
        apts=np.array(cols[6], dtype=float),
        poss=np.array(cols[7], dtype=float),
        venue=np.array(cols[8], dtype=float),
    )
    return g, dict(drop)


def tier_priors(events: list[dict[str, Any]], team_index: dict[str, int]) -> dict[int, np.ndarray]:
    """season -> per-team net per-100 tier prior from the as-of conference map (NaN = not D1 that season)."""
    from model_prediction.models.ncaab_v7_multiseason import tier_for

    conf: dict[int, dict[str, str]] = defaultdict(dict)
    for e in events:
        for side in ("home", "away"):
            cid = e.get(f"{side}_conference_id")
            if cid is not None:
                conf[int(e["season_year"])][str(e[f"{side}_team_id"])] = str(cid)
    out: dict[int, np.ndarray] = {}
    for s, teams in conf.items():
        arr = np.full(len(team_index), np.nan)
        for t, cid in teams.items():
            arr[team_index[t]] = tier_to_per100(np.array([tier_for(cid, s)]))[0]
        out[s] = arr
    return out


# ------------------------------------------------------------------------------------------- ratings walk
def run_ratings(
    events: list[dict[str, Any]],
    boxes: Mapping[str, Mapping[str, Any]],
    seasons: Iterable[int],
    variants: list[tuple[float, float]] = VARIANTS,
    targets: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Walk every Eastern game day of ``seasons`` and emit outcome-free C1 components per D1-vs-D1 event.

    For day D the fit uses current-season games with tip < Eastern midnight of D (bisect on tip time, the
    grader's cutoff); the season's prior is ``carry`` x last season's final ratings + (1 - carry) x tier.
    """
    import grader

    team_ids = sorted({str(e[f"{s}_team_id"]) for e in events if _is_d1_event(e) for s in ("home", "away")})
    team_index = {t: i for i, t in enumerate(team_ids)}
    n = len(team_ids)
    g, drops = build_games(events, boxes, team_index)
    tiers = tier_priors(events, team_index)
    h0 = HCA_PRIOR_POINTS * 100.0 / REF_POSS
    slate: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for e in events:
        if _is_d1_event(e) and (targets is None or e["event_id"] in targets):
            slate[int(e["season_year"])][grader.nr.et_day(e["start_utc"])].append(e)
    finals: dict[str, dict[str, np.ndarray]] = {}  # variant -> {"o","d","t"} of the previous season
    out: list[dict[str, Any]] = []
    season_meta: dict[str, Any] = {}
    for s in sorted(seasons):
        tier = np.nan_to_num(tiers.get(s, np.full(n, np.nan)), nan=tier_to_per100(np.array([1180.0]))[0])
        priors: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        for lam, carry in variants:
            key = variant_key(lam, carry)
            po = pd_ = 0.5 * tier
            pt = np.zeros(n)
            prev = finals.get(key)
            if prev is not None:
                has = prev["played"]
                po = np.where(has, carry * prev["o"] + (1 - carry) * 0.5 * tier, 0.5 * tier)
                pd_ = np.where(has, carry * prev["d"] + (1 - carry) * 0.5 * tier, 0.5 * tier)
                pt = np.where(has, carry * prev["t"], 0.0)
            priors[key] = (po, pd_, pt)
        in_season = np.flatnonzero(g.season == s)
        days = sorted(slate[s])
        last_day = date.fromordinal(int(g.day_ord[in_season].max())) if in_season.size else None
        walk = [*days, (last_day + timedelta(days=1)).isoformat()] if last_day else days
        for day in walk:
            cut = bisect.bisect_left(g.start, eastern_midnight_utc(day))
            sel = in_season[in_season < cut]
            dord = date.fromisoformat(day).toordinal()
            w = recency_weight(dord - g.day_ord[sel]) * blowout_weight(g.hpts[sel] - g.apts[sel])
            G, b, Gt, bt = eff_system(g, sel, w, n)
            played = np.zeros(n, dtype=bool)
            played[g.hidx[sel]] = True
            played[g.aidx[sel]] = True
            sol: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            for lam, carry in variants:
                key = variant_key(lam, carry)
                po, pd_, pt = priors[key]
                sol[key] = (solve_eff(G, b, n, lam, po, pd_, h0), solve_tempo(Gt, bt, n, lam, pt))
            if day not in slate[s]:  # the season-end solve seeds next season
                for key, (eff, tempo) in sol.items():
                    finals[key] = {"o": eff[:n], "d": eff[n : 2 * n], "t": tempo[:n], "played": played}
                key0 = variant_key(LAM_GRID[1], CARRY_GRID[1])
                eff0, tempo0 = sol[key0]
                season_meta[str(s)] = {
                    "fit_games": int(sel.size),
                    "hca_points_at_mean_poss": round(float(eff0[2 * n] * tempo0[n] / 100.0), 3),
                    "league_eff": round(float(eff0[2 * n + 1]), 2),
                    "league_poss_intercept": round(float(tempo0[n]), 2),
                    "mean_game_poss": round(float(g.poss[sel].mean()), 2),
                }
                continue
            gp_cur: dict[int, int] = defaultdict(int)
            for i in sel:
                gp_cur[int(g.hidx[i])] += 1
                gp_cur[int(g.aidx[i])] += 1
            for e in slate[s][day]:
                hi, ai = team_index[str(e["home_team_id"])], team_index[str(e["away_team_id"])]
                row: dict[str, Any] = {
                    "event_id": e["event_id"],
                    "season": s,
                    "game_date": day,
                    "n_fit_games": int(sel.size),
                    "gp_min_d1": min(gp_cur[hi], gp_cur[ai]),
                }
                for key, (eff, tempo) in sol.items():
                    m, t, p, _eh, _ea = predict_game(eff, tempo, n, hi, ai, bool(e["neutral"]))
                    row[f"c1_{key}"] = round(m, 4)
                    row[f"tot_{key}"] = round(t, 4)
                    row[f"poss_{key}"] = round(p, 3)
                out.append(row)
    meta = {"drops": drops, "n_fit_rows": len(g.start), "n_teams": n, "seasons": season_meta}
    finals_named = {
        k: {
            team_ids[i]: [round(float(v["o"][i]), 3), round(float(v["d"][i]), 3), round(float(v["t"][i]), 3)]
            for i in range(n)
            if v["played"][i]
        }
        for k, v in finals.items()
    }
    meta["finals_last_season"] = finals_named
    return out, meta


def cmd_ratings(_args: argparse.Namespace) -> int:
    events = _history_events()
    boxes = load_boxes()
    seasons = range(HISTORY_FIRST_SEASON, max(DEV_SEASONS) + 1)
    rows, meta = run_ratings(events, boxes, seasons)
    with gzip.open(COMPONENTS_OUT, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    FINALS_OUT.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{len(rows)} component rows -> {COMPONENTS_OUT}")
    print(json.dumps({k: v for k, v in meta.items() if k != "finals_last_season"}, indent=1))
    return 0


# ------------------------------------------------------------------------------------------- sanity
def cmd_sanity(_args: argparse.Namespace) -> int:
    """Outcome-free checks only: 2025-26 final ratings, possessions, HCA, component ranges."""
    meta = json.loads(FINALS_OUT.read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    for e in _history_events():
        for side in ("home", "away"):
            names[str(e[f"{side}_team_id"])] = e[f"{side}_team"]
    key = variant_key(LAM_GRID[1], CARRY_GRID[1])
    fin = meta["finals_last_season"][key]
    ranked = sorted(fin.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
    print(f"2025-26 final ratings, variant {key} (net = O + D per 100; tempo offset)")
    for t, (o, d, tp) in ranked[:15]:
        print(f"  {names.get(t, t):32s} net {o + d:+6.1f}  O {o:+5.1f}  D {d:+5.1f}  tempo {tp:+4.1f}")
    print("  ... bottom:", [names.get(t, t) for t, _ in ranked[-5:]])
    print(json.dumps(meta["seasons"], indent=1))
    comp = pd.read_json(COMPONENTS_OUT, lines=True, compression="gzip")
    for k in (variant_key(LAM_GRID[0], CARRY_GRID[0]), key, variant_key(LAM_GRID[-1], CARRY_GRID[-1])):
        print(
            k,
            "poss pct 1/50/99:",
            np.percentile(comp[f"poss_{k}"], [1, 50, 99]).round(1).tolist(),
            "margin sd",
            round(float(comp[f"c1_{k}"].std()), 2),
            "total mean",
            round(float(comp[f"tot_{k}"].mean()), 1),
        )
    return 0


# ------------------------------------------------------------------------------------------- predictions
def _identity_frame() -> pd.DataFrame:
    """Archive identity + PIT pre-game fields of the development window. Outcomes and lines are dropped
    on load: fitting reads training-season outcomes from the scoreboard history, never from here."""
    import grader

    a = grader.load_archive(DEV_SEASONS)
    keep = ["event_id", "season", "game_date", "home_team", "away_team", "neutral", "d1", "sample"]
    keep += ["v6_mean"]
    return a[keep].copy()


def _training_outcomes(seasons: Iterable[int]) -> dict[str, float]:
    want = set(seasons)
    return {
        e["event_id"]: float(e["home_score"] - e["away_score"])
        for e in _history_events()
        if int(e["season_year"]) in want
    }


def _training_totals(seasons: Iterable[int]) -> dict[str, float]:
    want = set(seasons)
    return {
        e["event_id"]: float(e["home_score"] + e["away_score"])
        for e in _history_events()
        if int(e["season_year"]) in want
    }


def fit_fold(df: pd.DataFrame, held_out: int) -> dict[str, Any]:
    """Every free parameter for one held-out season, from its three training seasons only."""
    train_seasons = [s for s in DEV_SEASONS if s != held_out]
    tr = df[df["season"].isin(train_seasons)].copy()
    tr["margin"] = tr["event_id"].map(_training_outcomes(train_seasons))
    tr["total"] = tr["event_id"].map(_training_totals(train_seasons))
    tr = tr[tr["margin"].notna()]
    keys = [variant_key(lam, c) for lam, c in VARIANTS]
    best, rm = select_variant(tr, keys)
    r1 = tr["margin"].to_numpy() - tr[f"c1_{best}"].to_numpy()
    c1_scales = t8_scales_by_bucket(r1, tr["sample"].to_numpy())
    tot_sd = sd_by_bucket(tr["total"].to_numpy() - tr[f"tot_{best}"].to_numpy(), tr["sample"].to_numpy())
    x = np.column_stack([tr[V7_VARIANT], tail_term(tr["v6_mean"]), tr[f"c1_{best}"]])
    coef = ols_no_intercept(x, tr["margin"].to_numpy())
    r2 = tr["margin"].to_numpy() - x @ coef
    c2_scales = t8_scales_by_bucket(r2, tr["sample"].to_numpy())
    return {
        "held_out": held_out,
        "fit_seasons": train_seasons,
        "n_train": len(tr),
        "variant": best,
        "train_rmse_by_variant": {k: round(v, 4) for k, v in rm.items()},
        "c1_scales": c1_scales,
        "total_sd": tot_sd,
        "c2_coef": coef.tolist(),
        "c2_train_rmse": round(float(np.sqrt(np.mean(r2**2))), 4),
        "c2_scales": c2_scales,
    }


def cmd_predict(_args: argparse.Namespace) -> int:
    import grader

    ident = _identity_frame()
    comp = pd.read_json(COMPONENTS_OUT, lines=True, compression="gzip", dtype={"event_id": str})
    comp = comp[comp["season"].isin(DEV_SEASONS)].drop(columns=["season", "game_date"])
    ridge = {}
    with grader.COMPONENTS.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            ridge[str(r["event_id"])] = r.get(V7_VARIANT)
    df = ident[ident["d1"]].merge(comp, on="event_id", how="inner")
    df[V7_VARIANT] = pd.to_numeric(df["event_id"].map(ridge), errors="coerce")
    print(f"D1 archive games {int(ident['d1'].sum())}, with C1 components {len(df)}")
    folds = {s: fit_fold(df[df[V7_VARIANT].notna()], s) for s in DEV_SEASONS}
    c1_rows, c2_rows = [], []
    for s, f in folds.items():
        h = df[df["season"] == s].copy()
        bucket = sample_bucket(h["sample"].to_numpy())
        mu1 = h[f"c1_{f['variant']}"].to_numpy()
        sc1 = np.array(f["c1_scales"])[bucket]
        base = h[["event_id", "season", "game_date", "home_team", "away_team", "neutral"]].copy()
        base["games_played_min"] = h["sample"].astype(int).to_numpy()
        base["dist"] = "t8"
        c1 = base.assign(
            mu_margin=mu1,
            scale_margin=sc1,
            mu_total=h[f"tot_{f['variant']}"].to_numpy(),
            scale_total=np.array(f["total_sd"])[bucket],
            dist_total="normal",
        )
        c1_rows.append(c1)
        hv = h[h[V7_VARIANT].notna()]
        hb = sample_bucket(hv["sample"].to_numpy())
        x = np.column_stack([hv[V7_VARIANT], tail_term(hv["v6_mean"]), hv[f"c1_{f['variant']}"]])
        c2 = base[h[V7_VARIANT].notna().to_numpy()].assign(
            mu_margin=x @ np.array(f["c2_coef"]), scale_margin=np.array(f["c2_scales"])[hb]
        )
        c2_rows.append(c2)
    code_sha = sha256_file(Path(__file__))
    sources = {
        str(BOX_CACHE.relative_to(REPO)).replace("\\", "/"): sha256_file(BOX_CACHE),
        "data/research/ncaab_market/scoreboard.jsonl": sha256_file(grader.nr.SCOREBOARD),
        "data/research/ncaab_market/v7ms_components.jsonl": sha256_file(grader.COMPONENTS),
        str(COMPONENTS_OUT.relative_to(REPO)).replace("\\", "/"): sha256_file(COMPONENTS_OUT),
    }
    fits = {str(s): f["fit_seasons"] for s, f in folds.items()}
    constants = {
        "lam_grid": LAM_GRID,
        "carry_grid": CARRY_GRID,
        "recency": "T-Rank: 1 to 40 days, -1%/day to 0.6 at 80 days (fixed a priori)",
        "blowout": "1 to a 20-point margin, -2%/point to 0.6 (fixed a priori)",
        "tier_prior": "v7 as-of tier table, Elo/28 points at 68 possessions, split O/D (fixed)",
        "hca_prior_points": HCA_PRIOR_POINTS,
        "hca_lambda": HCA_LAMBDA,
        "possessions": "FGA - OREB + TOV + 0.44 FTA (box) for every season 2022-2026; box-less games "
        "(about 0.1%) are not fitted; the attempts proxy is never used",
        "history": "D1-vs-D1 games of the current season; prior season's final ratings seed the prior",
    }
    for grid, rows, d, label in (
        ("C1_ppp_adjusted", c1_rows, C1_DIR, "C1 opponent-adjusted PPP ridge x adjusted tempo v1"),
        ("C2_ppp_into_v7", c2_rows, C2_DIR, "C2 OLS [v7 ridge, v7 tail, C1 margin] v1"),
    ):
        pred = pd.concat(rows, ignore_index=True)
        pred["p_home_ml"] = grader.served_ml_blend(
            pred["event_id"], pred["mu_margin"].to_numpy(), pred["scale_margin"].to_numpy(), pred["dist"]
        )
        if pred["p_home_ml"].isna().any():
            raise RuntimeError(
                f"{grid}: served_ml_blend returned NaN for {int(pred['p_home_ml'].isna().sum())}"
            )
        pred = pred.sort_values(["game_date", "event_id"]).reset_index(drop=True)
        d.mkdir(parents=True, exist_ok=True)
        pred.to_json(d / "preds.jsonl", orient="records", lines=True)
        meta = {
            "candidate": label,
            "grid_name": grid,
            "protocol_sha256": grader.PROTOCOL_SHA256,
            "code_sha256": code_sha,
            "fit_seasons_by_predicted_season": fits,
            "history_cutoff": "strictly before Eastern midnight of the game day",
            "data_sources": sources,
            "constants_fixed_a_priori": constants,
            "folds": {str(s): f for s, f in folds.items()},
        }
        (d / "preds.meta.json").write_text(json.dumps(meta, indent=1, default=float), encoding="utf-8")
        print(grid, len(pred), "rows; sha256", sha256_file(d / "preds.jsonl"))
    for s, f in folds.items():
        print(
            s,
            f["variant"],
            "c2 coef",
            np.round(f["c2_coef"], 4).tolist(),
            "c1 scales",
            np.round(f["c1_scales"], 2).tolist(),
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("cache-boxes", "ratings", "sanity", "predict"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    return {
        "cache-boxes": cmd_cache_boxes,
        "ratings": cmd_ratings,
        "sanity": cmd_sanity,
        "predict": cmd_predict,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
