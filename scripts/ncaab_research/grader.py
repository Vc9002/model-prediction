"""Shared NCAAB closing-line grader for the 261120 research program (quick 261120-nip, lane B).

One importable grader for every candidate model. It joins outcomes and ESPN closing lines ONLY for the windows
pinned in ``protocol_261120.json`` (hash hard-coded below; any edit to the JSON makes the grader refuse to run),
refuses locked games before a single score or line is read, and scores each candidate side by side with three
baselines on identical games: served v7 (D1-vs-D1), v6 as-of and the closing line itself.

Research only: reads the untracked archives under data/research/ncaab_market/, never the ledger or the runtime
root, never writes outside outputs/ncaab/research_261120/. Probabilities and points only, never American odds.

Use (repo root, ``OPENBLAS_NUM_THREADS=1``):
    .venv/Scripts/python scripts/ncaab_research/grader.py baseline            # baseline scorecard (+ gates)
    .venv/Scripts/python scripts/ncaab_research/grader.py baseline --shuffle-seed 5   # plumbing only
    .venv/Scripts/python scripts/ncaab_research/grader.py export --model v7_served --out preds.jsonl
    .venv/Scripts/python scripts/ncaab_research/grader.py score --pred preds.jsonl --meta preds.meta.json

From Python: ``grader.score(predictions_df_or_path, meta)``, or ``grader.predictions_from_callable(fn)`` to
drive a model day by day with history strictly before Eastern midnight and a slate without scores.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "src", REPO / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ncaab_residual as nr  # rule-1 ridge, window_stats, bootstrap helpers, reused unchanged

PROTOCOL_PATH = Path(__file__).resolve().parent / "protocol_261120.json"
PROTOCOL_SHA256 = "72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813"
ADDENDUM_PATH = Path(__file__).resolve().parent / "protocol_261120_addendum_v2.json"
ADDENDUM_SHA256 = "75241bc7885378f266ba66ce383f3b50fe58869e9b699ae90f0ddd9c8ead6317"
DATA = REPO / "data" / "research" / "ncaab_market"
COMPONENTS = DATA / "v7ms_components.jsonl"
LOSO_V7 = DATA / "v7ms_loso_fit_preds.jsonl"
OUT_DIR = REPO / "outputs" / "ncaab" / "research_261120"
READ_LOG = OUT_DIR / "read_log.jsonl"

DEV_SEASONS = (2023, 2024, 2025, 2026)
# Pluggable closing-line sources (addendum v2). The development window is graded on espn_archive only.
LINE_SOURCES: dict[str, dict[str, Any]] = {
    "espn_archive": {"path": nr.LINES, "rule": "ncaab-espn-lines-rule-1", "format": "espn"},
    "espn_backfill_261120": {
        "path": DATA / "backfill_261120" / "espn_lines.jsonl",
        "rule": "ncaab-espn-lines-rule-1",
        "format": "espn",
    },
    "cbbd": {
        "path": REPO / "data" / "research" / "ncaab_external" / "cbbd",
        "rule": None,  # set by an amendment once the data lane pins its selection + id-crosswalk rule
        "format": "cbbd",
    },
}
DEV_LINE_SOURCE = "espn_archive"
LATE_FOLDS = (2025, 2026)
LOCK_SEASON_MIN = 2027
LOCK_DATE_MIN = "2026-07-01"
V7_LOSO_SEASONS = (2023, 2024)
ELO_WEIGHT_SERVED = 0.5
ELO_POINTS_PER_MARGIN_POINT = 28.0
DF_T = 8.0
P_CLIP = 1e-6
COVERAGE_MIN = 0.99
LEAK_CORR = 0.15
GAMES_BUCKETS = (("0", 0, 1), ("1-2", 1, 3), ("3-7", 3, 8), ("8+", 8, 10**6))
LINE_BUCKETS = (("<5", 0.0, 5.0), ("5-10", 5.0, 10.0), ("10-20", 10.0, 20.0), ("20+", 20.0, 1e9))
REL_BINS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01))
REQUIRED_FIELDS = (
    "event_id",
    "season",
    "game_date",
    "home_team",
    "away_team",
    "neutral",
    "mu_margin",
    "scale_margin",
    "dist",
    "games_played_min",
)
OPTIONAL_FIELDS = ("p_home_ml", "mu_total", "scale_total", "dist_total")  # dist_total defaults to dist
DISTS = ("t8", "normal")
BASELINES = ("v7_served", "v6_asof", "closing_line")
COMPONENT_KEYS = (
    "event_id",
    "day",
    "season_year",
    "d1_home",
    "d1_away",
    "sample",
    "v6_mean",
    "v6_scale",
    "v6_p_home",
    "v6_total",
    "v6_total_sd",
    "elo_margin",
)


class LockedWindowError(RuntimeError):
    """A prediction or request touched the locked window (2026-27 / dates from 2026-07-01)."""


class ProtocolMismatchError(RuntimeError):
    """protocol_261120.json no longer matches the pinned hash."""


class SubmissionError(ValueError):
    """A candidate submission failed validation (fields, echo check, sidecar)."""


# --------------------------------------------------------------------------------------------- protocol
def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    proto = json.loads(path.read_text(encoding="utf-8"))
    got = canonical_sha256(proto)
    if got != PROTOCOL_SHA256:
        raise ProtocolMismatchError(
            f"{path} hashes to {got}, pinned {PROTOCOL_SHA256}; amendments need a new pin"
        )
    stats = proto["statistics"]["bootstrap"]
    # the rule-1 machinery is reused unchanged; its constants must be the ones this protocol pins
    assert (stats["resamples"], stats["seed"]) == (nr.N_BOOT, nr.SEED), "bootstrap constants drifted"
    assert (nr.RIDGE_ALPHA, nr.SHRINK) == (1000.0, 0.5), "rule-1 ridge constants drifted"
    load_addendum()
    return proto


def load_addendum(path: Path = ADDENDUM_PATH) -> dict[str, Any]:
    add = json.loads(path.read_text(encoding="utf-8"))
    got = canonical_sha256(add)
    if got != ADDENDUM_SHA256:
        raise ProtocolMismatchError(f"{path} hashes to {got}, pinned {ADDENDUM_SHA256}")
    return add


def verify_pinned_inputs(proto: dict[str, Any]) -> dict[str, str]:
    """Data files must hash to the pinned values; the served v7 constants must equal the pinned frozen config."""
    from model_prediction.models import ncaab_v7_serving as sv

    checked = {}
    for rel, want in proto["pinned_inputs_sha256"].items():
        if rel.startswith("src/"):
            continue  # serving code may change without changing the formula; constants are checked below
        got = nr.sha256_file(REPO / rel)
        if got != want:
            raise ProtocolMismatchError(f"{rel} hashes to {got}, pinned {want}; archive rebuilt?")
        checked[rel] = got
    frozen = json.loads((REPO / "outputs/ncaab/ncaab_v7_multiseason_frozen.json").read_text(encoding="utf-8"))
    cfg = frozen["config"]
    if (
        sv.V7_VARIANT != cfg["variant"]
        or not np.allclose(sv.V7_COEF, cfg["coef"], rtol=0, atol=1e-12)
        or not np.allclose(sv.V7_SCALES, cfg["scales"], rtol=0, atol=1e-12)
    ):
        raise ProtocolMismatchError("served v7 constants differ from the pinned frozen config")
    return checked


def line_source_info(name: str) -> dict[str, Any]:
    if name not in LINE_SOURCES:
        raise ValueError(f"unknown line source {name!r}; known {sorted(LINE_SOURCES)}")
    src = LINE_SOURCES[name]
    if src["rule"] is None:
        raise SubmissionError(f"line source {name!r} has no pinned selection rule yet (addendum v2)")
    path = Path(src["path"])
    return {
        "name": name,
        "rule": src["rule"],
        "path": str(path.relative_to(REPO)),
        "sha256": nr.sha256_file(path),
    }


def is_locked(season_year: int, game_date: str) -> bool:
    return int(season_year) >= LOCK_SEASON_MIN or str(game_date) >= LOCK_DATE_MIN


def assert_unlocked(season_year: int, game_date: str) -> None:
    if is_locked(season_year, game_date):
        raise LockedWindowError(f"season {season_year} / {game_date} is in the locked window")


# --------------------------------------------------------------------------------------------- distributions
def dist_cdf(z: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """CDF of the standardised value z under each row's distribution ('t8' or 'normal')."""
    from scipy import stats

    z = np.asarray(z, dtype=float)
    is_t = np.asarray(dist) == "t8"
    return np.where(is_t, stats.t.cdf(z, df=DF_T), stats.norm.cdf(z))


def p_home_from_margin(mu: np.ndarray, scale: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """P(margin >= 1) = 1 - F(0.5): margins are integers and never tie."""
    return 1.0 - dist_cdf((0.5 - np.asarray(mu, float)) / np.asarray(scale, float), dist)


def p_above_line(mu: np.ndarray, scale: np.ndarray, line: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """P(outcome > line), push mass excluded on integer lines (home cover for margins, over for totals)."""
    mu, scale, line = (np.asarray(a, float) for a in (mu, scale, line))
    integer = np.mod(line, 1.0) == 0
    above_int = 1.0 - dist_cdf((line + 0.5 - mu) / scale, dist)
    below_int = dist_cdf((line - 0.5 - mu) / scale, dist)
    above_half = 1.0 - dist_cdf((line - mu) / scale, dist)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(integer, above_int / np.maximum(above_int + below_int, 1e-12), above_half)


def elo_win_from_margin(elo_margin: np.ndarray) -> np.ndarray:
    """v6's Elo logistic from the component archive's elo_margin (which already carries the Elo HCA)."""
    return 1.0 / (1.0 + 10.0 ** (-np.asarray(elo_margin, float) * ELO_POINTS_PER_MARGIN_POINT / 400.0))


def reconstruct_elo_win(v6_p_home: np.ndarray, v6_mean: np.ndarray, v6_scale: np.ndarray) -> np.ndarray:
    """Invert v6's served blend p = w*Elo + (1-w)*P_t8(margin >= 1) for the Elo half (w = 0.5)."""
    pm = p_home_from_margin(v6_mean, v6_scale, np.full(len(np.atleast_1d(v6_mean)), "t8"))
    w = ELO_WEIGHT_SERVED
    return np.clip((np.asarray(v6_p_home, float) - (1.0 - w) * pm) / w, 0.0, 1.0)


def implied_home_margin(home_spread_line: float | None) -> float:
    """ESPN home spread line (negative = home favoured) -> the home margin the market implies."""
    return -float(home_spread_line) if home_spread_line is not None else float("nan")


def logloss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), P_CLIP, 1.0 - P_CLIP)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


# --------------------------------------------------------------------------------------------- archive
def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def load_archive(
    seasons: Iterable[int] = DEV_SEASONS,
    *,
    shuffle_seed: int | None = None,
    line_source: str = DEV_LINE_SOURCE,
    verify_inputs: bool = True,
) -> pd.DataFrame:
    """One row per event of ``seasons``: identity, D1 flags, PIT components, closing lines and outcomes.

    The lock is applied FIRST: locked seasons/dates raise, and only the surviving event ids are looked up in the
    scoreboard (scores) and line files. ``shuffle_seed`` permutes margins/totals within season (plumbing test).
    """
    seasons = tuple(sorted({int(s) for s in seasons}))
    for s in seasons:
        if s >= LOCK_SEASON_MIN:
            raise LockedWindowError(f"season {s} is locked")
    if set(seasons) & set(DEV_SEASONS) and line_source != DEV_LINE_SOURCE:
        raise SubmissionError(f"the development window is graded on {DEV_LINE_SOURCE} only (addendum v2)")
    src = line_source_info(line_source)
    if verify_inputs:
        verify_pinned_inputs(load_protocol())
    comps: dict[str, dict[str, Any]] = {}
    for rec in _iter_jsonl(COMPONENTS):
        if int(rec["season_year"]) not in seasons or is_locked(rec["season_year"], rec["day"]):
            continue
        comps[str(rec["event_id"])] = {k: rec.get(k) for k in COMPONENT_KEYS}
    ids = set(comps)
    for rec in _iter_jsonl(nr.SCOREBOARD):
        if rec.get("http_status") != 200:
            continue
        for ev in rec["events"]:
            eid = str(ev["event_id"])
            if eid not in ids or "home_score" in comps[eid]:
                continue
            if not ev["completed"] or ev["home_score"] is None or ev["away_score"] is None:
                continue
            comps[eid].update(
                home_team=ev["home_team"],
                away_team=ev["away_team"],
                neutral=bool(ev["neutral"]),
                start_utc=ev["start_utc"],
                home_score=int(ev["home_score"]),
                away_score=int(ev["away_score"]),
            )
    for rec in _iter_jsonl(REPO / src["path"]):
        eid = str(rec["event_id"])
        if eid not in ids:
            continue
        sel = rec.get("selected") or {}
        hs, tl = sel.get("home_spread_line"), sel.get("total_line")
        comps[eid]["line"] = implied_home_margin(hs)
        comps[eid]["total_line"] = float(tl) if tl is not None else np.nan
    df = pd.DataFrame([r for r in comps.values() if "home_score" in r])
    df = df.rename(columns={"season_year": "season", "day": "game_date"})
    df["season"] = df["season"].astype(int)
    df["event_id"] = df["event_id"].astype(str)
    df["margin"] = (df["home_score"] - df["away_score"]).astype(float)
    df["total"] = (df["home_score"] + df["away_score"]).astype(float)
    for col in ("line", "total_line"):
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["d1"] = df["d1_home"].astype(bool) & df["d1_away"].astype(bool)
    df = df.drop(columns=["home_score", "away_score"]).sort_values(["game_date", "event_id"])
    df = df.reset_index(drop=True)
    df.attrs["line_source"] = src
    if shuffle_seed is not None:
        rng = np.random.default_rng(shuffle_seed)
        for idx in df.groupby("season").groups.values():
            perm = rng.permutation(len(idx))
            for col in ("margin", "total"):
                df.loc[idx, col] = df.loc[idx, col].to_numpy()[perm]
    return df


def load_history_events() -> list[dict[str, Any]]:
    """Every completed, unlocked scoreboard event (2021-22 on), sorted by tip time, scores included."""
    out = [
        e for e in nr.load_scoreboard_events() if not is_locked(e["season_year"], nr.et_day(e["start_utc"]))
    ]
    return sorted(out, key=lambda e: (e["start_utc"], e["event_id"]))


# --------------------------------------------------------------------------------------------- baselines
def baseline_predictions(archive: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Served v7 (D1-vs-D1), v6 as-of and closing-line predictions in the submission schema.

    closing_line carries mu = L and a placeholder scale; its per-fold sigma is set inside ``score``.
    """
    from model_prediction.models.ncaab_v7_serving import V7_VARIANT, combine_f1

    ridge = {r["event_id"]: r.get(V7_VARIANT) for r in _iter_jsonl(COMPONENTS)}
    loso = {r["event_id"]: r for r in _iter_jsonl(LOSO_V7)}
    a = archive
    ident = a[["event_id", "season", "game_date", "home_team", "away_team", "neutral"]].copy()
    ident["games_played_min"] = a["sample"].astype(int)
    # v7 served
    mu, sc = np.full(len(a), np.nan), np.full(len(a), np.nan)
    for i, row in enumerate(a.itertuples(index=False)):
        if not row.d1:
            continue
        if row.season in V7_LOSO_SEASONS:
            lr = loso.get(row.event_id)
            if lr is not None:
                mu[i], sc[i] = lr["v7_mean"], lr["v7_scale"]
            continue
        rg = ridge.get(row.event_id)
        if rg is not None:
            mu[i], sc[i] = combine_f1(float(rg), float(row.v6_mean), int(row.sample))
    elo = reconstruct_elo_win(a["v6_p_home"], a["v6_mean"], a["v6_scale"])
    t8 = np.full(len(a), "t8")
    with np.errstate(invalid="ignore"):
        pm7 = p_home_from_margin(mu, sc, t8)
    p7 = np.clip(ELO_WEIGHT_SERVED * elo + (1.0 - ELO_WEIGHT_SERVED) * pm7, 0.0001, 0.9999)
    v7 = ident.assign(mu_margin=mu, scale_margin=sc, dist="t8", p_home_ml=p7)
    v7 = v7.assign(
        mu_total=a["v6_total"].astype(float), scale_total=a["v6_total_sd"].astype(float), dist_total="normal"
    )
    v7 = v7[~np.isnan(mu)]
    # v6 as-of (served parameters, as-of conference map)
    v6 = ident.assign(
        mu_margin=a["v6_mean"].astype(float),
        scale_margin=a["v6_scale"].astype(float),
        dist="t8",
        p_home_ml=a["v6_p_home"].astype(float),
        mu_total=a["v6_total"].astype(float),
        scale_total=a["v6_total_sd"].astype(float),
        dist_total="normal",
    )
    line = ident.assign(
        mu_margin=a["line"], scale_margin=np.nan, dist="normal", p_home_ml=np.nan, mu_total=a["total_line"]
    )
    line = line.assign(scale_total=np.nan, dist_total="normal")
    line = line[a["line"].notna().to_numpy()]
    return {
        "v7_served": v7.reset_index(drop=True),
        "v6_asof": v6,
        "closing_line": line.reset_index(drop=True),
    }


def elo_reconstruction_gate(archive: pd.DataFrame) -> dict[str, Any]:
    """Outcome-free: the reconstructed Elo half must match the Elo formula on almost every game."""
    rec = reconstruct_elo_win(archive["v6_p_home"], archive["v6_mean"], archive["v6_scale"])
    formula = elo_win_from_margin(archive["elo_margin"])
    interior = (archive["v6_p_home"] > 0.001) & (archive["v6_p_home"] < 0.999)
    diff = np.abs(rec - formula)[interior.to_numpy()]
    frac = float(np.mean(diff < 0.002))
    out = {
        "n": int(interior.sum()),
        "frac_abs_diff_lt_0.002": round(frac, 4),
        "median_abs_diff": float(np.median(diff)),
        "p99_abs_diff": float(np.quantile(diff, 0.99)),
        "note": "misses are altitude home games (elo_margin carries the altitude bonus, the Elo logistic does not)",
        "passed": frac >= 0.95,
    }
    if not out["passed"]:
        raise RuntimeError(f"Elo reconstruction gate failed: {out}")
    return out


def served_ml_blend(
    event_ids: Iterable[str], mu: np.ndarray, scale: np.ndarray, dist: np.ndarray
) -> np.ndarray:
    """The SERVED moneyline rule applied to a candidate's margin distribution: 0.5 x v6 Elo win probability
    (reconstructed from the component archive) + 0.5 x P(margin >= 1). Candidates outside family A should
    submit this as p_home_ml; when p_home_ml is absent the grader derives the pure margin probability, which is
    the A_ml_w0 arm, not the served rule."""
    ids, vp, vm, vs = [], [], [], []
    for rec in _iter_jsonl(COMPONENTS):
        if not is_locked(rec["season_year"], rec["day"]):
            ids.append(str(rec["event_id"]))
            vp.append(rec["v6_p_home"])
            vm.append(rec["v6_mean"])
            vs.append(rec["v6_scale"])
    elo = dict(zip(ids, reconstruct_elo_win(np.array(vp), np.array(vm), np.array(vs)), strict=True))
    e = np.array([elo.get(str(i), np.nan) for i in event_ids], dtype=float)
    pm = p_home_from_margin(mu, scale, np.asarray(dist))
    return np.clip(ELO_WEIGHT_SERVED * e + (1.0 - ELO_WEIGHT_SERVED) * pm, 0.0001, 0.9999)


# --------------------------------------------------------------------------------------------- submissions
def load_predictions(src: str | Path | pd.DataFrame) -> pd.DataFrame:
    df = src.copy() if isinstance(src, pd.DataFrame) else pd.read_json(Path(src), lines=True, dtype=False)
    missing = [f for f in REQUIRED_FIELDS if f not in df.columns]
    if missing:
        raise SubmissionError(f"missing fields {missing}")
    for f in OPTIONAL_FIELDS:
        if f not in df.columns:
            df[f] = np.nan
    df["event_id"] = df["event_id"].astype(str)
    df["season"] = df["season"].astype(int)
    df["game_date"] = df["game_date"].astype(str)
    for f in ("mu_margin", "scale_margin", "games_played_min", "p_home_ml", "mu_total", "scale_total"):
        df[f] = pd.to_numeric(df[f], errors="coerce")
    df["dist_total"] = df["dist_total"].where(df["dist_total"].isin(DISTS), df["dist"])
    if df["event_id"].duplicated().any():
        raise SubmissionError("duplicate event_id rows")
    bad_dist = sorted(set(df["dist"]) - set(DISTS))
    if bad_dist:
        raise SubmissionError(f"dist must be one of {DISTS}, got {bad_dist}")
    locked = [
        eid
        for eid, s, d in zip(df["event_id"], df["season"], df["game_date"], strict=True)
        if is_locked(s, d)
    ]
    if locked:
        raise LockedWindowError(f"{len(locked)} prediction rows are in the locked window, e.g. {locked[:3]}")
    if df[["mu_margin", "scale_margin"]].isna().any().any() or (df["scale_margin"] <= 0).any():
        raise SubmissionError("mu_margin/scale_margin must be finite with scale > 0 (omit a game instead)")
    has_total = df["mu_total"].notna()
    if (has_total & ~(df["scale_total"] > 0)).any():
        raise SubmissionError("scale_total > 0 is required wherever mu_total is given")
    if (df["p_home_ml"].notna() & ~df["p_home_ml"].between(0.0, 1.0)).any():
        raise SubmissionError("p_home_ml must lie in [0, 1]")
    return df.reset_index(drop=True)


def validate_echo(pred: pd.DataFrame, archive: pd.DataFrame) -> None:
    """season, game_date, teams and neutral flag must equal the archive's for every event_id."""
    a = archive.set_index("event_id")
    unknown = sorted(set(pred["event_id"]) - set(a.index))
    if unknown:
        raise SubmissionError(f"{len(unknown)} event ids not in the window's archive, e.g. {unknown[:3]}")
    ref = a.loc[pred["event_id"]]
    for col_p, col_a in (
        ("season", "season"),
        ("game_date", "game_date"),
        ("home_team", "home_team"),
        ("away_team", "away_team"),
        ("neutral", "neutral"),
    ):
        mism = pred[col_p].to_numpy() != ref[col_a].to_numpy()
        if col_p == "neutral":
            mism = pred[col_p].astype(bool).to_numpy() != ref[col_a].astype(bool).to_numpy()
        if mism.any():
            first = pred.loc[mism, "event_id"].iloc[0]
            raise SubmissionError(
                f"{int(mism.sum())} rows disagree with the archive on {col_p}, e.g. {first}"
            )


def validate_meta(meta: dict[str, Any], proto: dict[str, Any], seasons: Iterable[int]) -> dict[str, Any]:
    for key in (
        "candidate",
        "grid_name",
        "protocol_sha256",
        "code_sha256",
        "fit_seasons_by_predicted_season",
    ):
        if key not in meta:
            raise SubmissionError(f"sidecar meta lacks {key!r}")
    if meta["protocol_sha256"] != PROTOCOL_SHA256:
        raise SubmissionError("sidecar protocol_sha256 does not match the pinned protocol")
    fits = {int(k): [int(x) for x in v] for k, v in meta["fit_seasons_by_predicted_season"].items()}
    for s in seasons:
        if s not in fits:
            raise SubmissionError(f"sidecar has no fit seasons for predicted season {s}")
        if s in fits[s]:
            raise SubmissionError(f"leak: season {s} is among its own fit seasons {fits[s]}")
        if any(f >= LOCK_SEASON_MIN for f in fits[s]):
            raise LockedWindowError(f"fit seasons for {s} include a locked season")
    grid = proto["candidate_grid"]
    registered = meta["grid_name"] in grid
    return {
        "registered": registered,
        "family": grid[meta["grid_name"]]["family"] if registered else None,
        "fit_seasons": fits,
    }


def predictions_from_callable(
    fn: Callable[[str, list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]],
    seasons: Iterable[int] = DEV_SEASONS,
) -> pd.DataFrame:
    """Drive a candidate day by day. ``fn(game_date, slate, history)`` gets the day's games WITHOUT scores and
    every completed game that tipped strictly before Eastern midnight of that day (scores included), and
    returns prediction dicts in the submission schema."""
    from model_prediction.domain import EASTERN

    history = load_history_events()
    starts = [datetime.strptime(e["start_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC) for e in history]
    seasons = tuple(seasons)
    slate_by_day: dict[str, list[dict[str, Any]]] = {}
    # the slate carries identity and pre-game fields only: never the score or the game's own box stats
    keep = {"event_id", "start_utc", "season_type", "competition_type", "neutral", "conference_competition"}
    keep |= {
        f"{side}_{f}" for side in ("home", "away") for f in ("team", "team_id", "conference_id", "ap_rank")
    }
    for e in history:
        day = nr.et_day(e["start_utc"])
        if int(e["season_year"]) in seasons:
            slate_by_day.setdefault(day, []).append(
                {k: v for k, v in e.items() if k in keep}
                | {"season": int(e["season_year"]), "game_date": day}
            )
    rows: list[dict[str, Any]] = []
    for day in sorted(slate_by_day):
        assert_unlocked(slate_by_day[day][0]["season"], day)
        cutoff = datetime.combine(date.fromisoformat(day), dtime.min, tzinfo=EASTERN).astimezone(UTC)
        hist = history[: bisect.bisect_left(starts, cutoff)]
        rows.extend(fn(day, slate_by_day[day], hist))
    return load_predictions(pd.DataFrame(rows))


# --------------------------------------------------------------------------------------------- bootstrap
def _boot(days: list[str], arrays: dict[str, np.ndarray], fn: Callable[[dict[str, np.ndarray]], np.ndarray]):
    """Day-cluster bootstrap of a statistic of per-day sums (4000 resamples, seed 17, as rule-1)."""
    inv, d = nr._clusters(days)
    cnt = nr._boot_counts(d, np.random.default_rng(nr.SEED))
    agg = {k: np.bincount(inv, weights=np.asarray(v, float), minlength=d) for k, v in arrays.items()}
    point = float(fn({k: np.array([v.sum()]) for k, v in agg.items()})[0])
    return point, fn({k: cnt @ v for k, v in agg.items()})


def _corr_from_sums(s: dict[str, np.ndarray], x: str = "x") -> np.ndarray:
    n = s["n"]
    with np.errstate(invalid="ignore", divide="ignore"):
        cxy = s[f"{x}y"] - s[x] * s["y"] / n
        vx = s[f"{x}{x}"] - s[x] ** 2 / n
        vy = s["yy"] - s["y"] ** 2 / n
        return cxy / np.sqrt(vx * vy)


def _sums(x: np.ndarray, y: np.ndarray, name: str = "x") -> dict[str, np.ndarray]:
    return {"n": np.ones_like(y), name: x, "y": y, f"{name}{name}": x * x, "yy": y * y, f"{name}y": x * y}


def _ci(samples: np.ndarray, k: int | None = None) -> dict[str, list[float]]:
    out = {"ci95": [round(v, 5) for v in nr._percentiles(samples, 0.025)]}
    if k:
        out["ci_adj"] = [round(v, 5) for v in nr._percentiles(samples, 0.025 / k)]
        out["k"] = k
    return out


def market_plain(x: np.ndarray, y: np.ndarray, days: list[str]) -> dict[str, Any]:
    """OLS slope and Pearson correlation of y (actual - line) on x (model - line), day-cluster CIs."""

    def slope(s: dict[str, np.ndarray]) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return (s["xy"] - s["x"] * s["y"] / s["n"]) / (s["xx"] - s["x"] ** 2 / s["n"])

    sums = _sums(x, y)
    b, bs = _boot(days, sums, slope)
    r, rs = _boot(days, sums, _corr_from_sums)
    return {
        "n": len(y),
        "slope": round(b, 5),
        "slope_ci95": _ci(bs)["ci95"],
        "corr": round(r, 5),
        "corr_ci95": _ci(rs)["ci95"],
    }


def rule1_loso(df: pd.DataFrame, xcol: str, ycol: str, k: int) -> dict[str, Any]:
    """ncaab-residual-rule-1 ridge, leave-one-season-out: fit on the other seasons, 0.5 shrink, pooled stats."""
    preds, ys, days, raw_y, per = [], [], [], [], {}
    for s in sorted(df["season"].unique()):
        tr, te = df[df["season"] != s], df[df["season"] == s]
        if len(tr) < 50 or te.empty:
            continue
        fit = nr.ridge_fit(tr[[xcol]].to_numpy(float), tr[ycol].to_numpy(float))
        p = nr.ridge_predict(fit, te[[xcol]].to_numpy(float))
        preds.append(p)
        ys.append(te[ycol].to_numpy(float) - fit["ymean"])
        raw_y.append(te[ycol].to_numpy(float))
        days.extend(te["game_date"].tolist())
        per[int(s)] = {"beta_std": float(fit["beta"][0]), "baseline": fit["ymean"], "n_fit": len(tr)}
    pred, y, yr = np.concatenate(preds), np.concatenate(ys), np.concatenate(raw_y)
    ws = nr.window_stats(pred, y, days, 0.0, k)
    take = (np.abs(pred) >= nr.MIN_GAP) & (yr != 0)
    wins = int((take & ((pred > 0) == (yr > 0))).sum())
    out = {
        "folds": per,
        "n": ws["n"],
        "slope": ws["slope"],
        "slope_ci95": ws["slope_ci_95"],
        "slope_ci_adj": ws["slope_ci_adj"],
        "mse_delta": ws["mse_delta"],
        "mse_delta_ci95": ws["mse_delta_ci_95"],
        "mse_delta_ci_adj": ws["mse_delta_ci_adj"],
        "k": k,
        "side_record_abs_pred_ge_1": {
            "bets": int(take.sum()),
            "wins": wins,
            "pushes_excluded": int(((np.abs(pred) >= nr.MIN_GAP) & (yr == 0)).sum()),
        },
    }
    out["edge_eligible"] = bool(out["slope_ci_adj"][0] > 0 and out["mse_delta_ci_adj"][0] > 0)
    return out


# --------------------------------------------------------------------------------------------- scoring
def _err(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    ok = ~np.isnan(y) & ~np.isnan(p)
    if not ok.any():
        return {"n": 0, "rmse": None, "bias": None}
    e = y[ok] - p[ok]
    return {
        "n": int(ok.sum()),
        "rmse": round(float(np.sqrt(np.mean(e * e))), 4),
        "bias": round(float(e.mean()), 4),
    }


def _bucket_table(df: pd.DataFrame, by: np.ndarray, buckets: tuple, ycol: str, pcol: str) -> dict[str, Any]:
    y, p = df[ycol].to_numpy(float), df[pcol].to_numpy(float)
    return {name: _err(y[(by >= lo) & (by < hi)], p[(by >= lo) & (by < hi)]) for name, lo, hi in buckets}


def _prob_block(p: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    ok = ~np.isnan(p) & ~np.isnan(y)
    if not ok.any():
        return {"n": 0}
    p, y = p[ok], y[ok]
    fav_p, fav_hit = np.maximum(p, 1 - p), np.where(p >= 0.5, y, 1 - y)
    rel = {}
    for lo, hi in REL_BINS:
        sel = (fav_p >= lo) & (fav_p < hi)
        rel[f"{lo:.1f}-{min(hi, 1.0):.1f}"] = {
            "n": int(sel.sum()),
            "mean_p": round(float(fav_p[sel].mean()), 4) if sel.any() else None,
            "hit": round(float(fav_hit[sel].mean()), 4) if sel.any() else None,
        }
    return {
        "n": int(ok.sum()),
        "brier": round(float(np.mean((p - y) ** 2)), 5),
        "logloss": round(float(np.mean(logloss(p, y))), 5),
        "reliability_favoured_side": rel,
    }


def fold_sigma(df: pd.DataFrame) -> dict[int, float]:
    """Per held-out season: SD of (margin - L) on the lined D1 cohort of the other seasons (closing-line sigma)."""
    out = {}
    for s in df["season"].unique():
        r = (df.loc[df["season"] != s, "margin"] - df.loc[df["season"] != s, "line"]).to_numpy(float)
        out[int(s)] = float(np.std(r, ddof=1)) if len(r) > 10 else float("nan")
    return out


def _model_columns(df: pd.DataFrame, pfx: str, sigma: dict[int, float]) -> pd.DataFrame:
    """Derived probabilities for one model (prefix ``pfx``) on the scoring frame."""
    dist = df[f"{pfx}_dist"].to_numpy()
    mu, sc = df[f"{pfx}_mu_margin"].to_numpy(float), df[f"{pfx}_scale_margin"].to_numpy(float)
    if pfx == "closing_line":
        sc = df["season"].map(sigma).to_numpy(float)
        df[f"{pfx}_scale_margin"] = sc
    pml = df[f"{pfx}_p_home_ml"].to_numpy(float)
    with np.errstate(invalid="ignore"):
        derived = p_home_from_margin(mu, sc, dist)
        df[f"{pfx}_p_ml"] = np.where(np.isnan(pml), derived, pml)
        df[f"{pfx}_p_cover"] = np.nan if pfx == "closing_line" else p_above_line(mu, sc, df["line"], dist)
        mt, st = df[f"{pfx}_mu_total"].to_numpy(float), df[f"{pfx}_scale_total"].to_numpy(float)
        dt = df[f"{pfx}_dist_total"].to_numpy()
        df[f"{pfx}_p_over"] = np.nan if pfx == "closing_line" else p_above_line(mt, st, df["total_line"], dt)
    return df


def model_block(df: pd.DataFrame, pfx: str, proto: dict[str, Any]) -> dict[str, Any]:
    k = proto["multiplicity"]["K"]
    y_m, line = df["margin"].to_numpy(float), df["line"].to_numpy(float)
    mu = df[f"{pfx}_mu_margin"].to_numpy(float)
    blk: dict[str, Any] = {
        "margin_all": _err(y_m, mu),
        "margin_by_games_played": _bucket_table(
            df, df["games_played_min"].to_numpy(float), GAMES_BUCKETS, "margin", f"{pfx}_mu_margin"
        ),
        "margin_by_abs_line": _bucket_table(df, np.abs(line), LINE_BUCKETS, "margin", f"{pfx}_mu_margin"),
        "margin_by_season": {
            int(s): _err(g["margin"].to_numpy(float), g[f"{pfx}_mu_margin"].to_numpy(float))
            for s, g in df.groupby("season")
        },
    }
    win = (y_m > 0).astype(float)
    ml = _prob_block(df[f"{pfx}_p_ml"].to_numpy(float), win)
    blk["moneyline"] = ml
    if pfx == "closing_line":
        for key in ("market_spread", "rule1_spread", "cover", "market_moneyline"):
            blk[key] = "N/A (the line itself)"
    else:
        x, y = mu - line, y_m - line
        blk["market_spread"] = market_plain(x, y, df["game_date"].tolist())
        blk["market_spread"]["by_season"] = {
            int(s): {"n": len(g), "corr": round(float(np.corrcoef(g["_x"], g["_y"])[0, 1]), 5)}
            for s, g in df.assign(_x=x, _y=y).groupby("season")
        }
        blk["rule1_spread"] = rule1_loso(df.assign(_x=x, _y=y), "_x", "_y", k["spread_market_relative"])
        push = y == 0
        cov = df[f"{pfx}_p_cover"].to_numpy(float)
        blk["cover"] = _prob_block(np.where(push, np.nan, cov), (y > 0).astype(float))
        pl = df["closing_line_p_ml"].to_numpy(float)
        pm = df[f"{pfx}_p_ml"].to_numpy(float)
        blk["market_moneyline"] = market_plain(pm - pl, win - pl, df["game_date"].tolist())
    # totals (games with a total line and the model's total)
    mt = df[f"{pfx}_mu_total"].to_numpy(float)
    tl = df["total_line"].to_numpy(float)
    has = ~np.isnan(mt) & ~np.isnan(tl)
    if has.any():
        t = df[has]
        tblk: dict[str, Any] = {
            "total_all": _err(t["total"].to_numpy(float), t[f"{pfx}_mu_total"].to_numpy(float)),
            "total_by_games_played": _bucket_table(
                t, t["games_played_min"].to_numpy(float), GAMES_BUCKETS, "total", f"{pfx}_mu_total"
            ),
        }
        if pfx != "closing_line":
            xt = t[f"{pfx}_mu_total"].to_numpy(float) - t["total_line"].to_numpy(float)
            yt = t["total"].to_numpy(float) - t["total_line"].to_numpy(float)
            tblk["market_total"] = market_plain(xt, yt, t["game_date"].tolist())
            tblk["rule1_total"] = rule1_loso(t.assign(_x=xt, _y=yt), "_x", "_y", k["total_market_relative"])
            po = t[f"{pfx}_p_over"].to_numpy(float)
            tblk["over"] = _prob_block(np.where(yt == 0, np.nan, po), (yt > 0).astype(float))
        blk["totals"] = tblk
    return blk


def paired_vs_v7(df: pd.DataFrame, k: dict[str, int]) -> dict[str, Any]:
    """Candidate minus served v7 on identical games (day-cluster bootstrap)."""
    days = df["game_date"].tolist()
    y_m, line = df["margin"].to_numpy(float), df["line"].to_numpy(float)
    y = y_m - line
    xc, xv = df["cand_mu_margin"].to_numpy(float) - line, df["v7_served_mu_margin"].to_numpy(float) - line
    late = df["season"].isin(LATE_FOLDS).to_numpy()
    out: dict[str, Any] = {}

    def dcorr(s: dict[str, np.ndarray]) -> np.ndarray:
        return _corr_from_sums(s, "c") - _corr_from_sums(s, "v")

    def corr_sums(m: np.ndarray) -> dict[str, np.ndarray]:
        s = _sums(xc[m], y[m], "c")
        s.update({k2: v for k2, v in _sums(xv[m], y[m], "v").items() if k2 not in ("n", "y", "yy")})
        return s

    allm = np.ones(len(df), bool)
    pt, smp = _boot(days, corr_sums(allm), dcorr)
    out["spread_corr_delta_vs_v7"] = {"delta": round(pt, 5), **_ci(smp, k["spread_market_relative"])}
    late_days = [d for d, m in zip(days, late, strict=True) if m]
    if late.any():
        out["spread_corr_delta_vs_v7"]["late_folds_point"] = round(
            _boot(late_days, corr_sums(late), dcorr)[0], 5
        )

    def mean_diff(kk: int | None, a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
        ok = mask & ~np.isnan(a) & ~np.isnan(b)
        dd = [d for d, m in zip(days, ok, strict=True) if m]
        p, smp = _boot(dd, {"d": a[ok] - b[ok], "n": np.ones(int(ok.sum()))}, lambda s: s["d"] / s["n"])
        res = {"delta": round(p, 6), "n": int(ok.sum()), **_ci(smp, kk)}
        lm = ok & late
        if lm.any():
            res["late_folds_point"] = round(float(np.mean(a[lm] - b[lm])), 6)
        return res

    ec, ev = (
        (y_m - df["cand_mu_margin"].to_numpy(float)) ** 2,
        (y_m - df["v7_served_mu_margin"].to_numpy(float)) ** 2,
    )

    def drmse(s: dict[str, np.ndarray]) -> np.ndarray:
        return np.sqrt(s["ec"] / s["n"]) - np.sqrt(s["ev"] / s["n"])

    p, smp = _boot(days, {"ec": ec, "ev": ev, "n": np.ones(len(df))}, drmse)
    out["margin_rmse_delta_vs_v7"] = {"delta": round(p, 5), **_ci(smp)}
    cover_y = (y > 0).astype(float)
    no_push = y != 0
    out["cover_logloss_delta_vs_v7"] = mean_diff(
        k["cover_calibration"],
        logloss(df["cand_p_cover"], cover_y),
        logloss(df["v7_served_p_cover"], cover_y),
        no_push,
    )
    win = (y_m > 0).astype(float)
    out["moneyline_logloss_delta_vs_v7"] = mean_diff(
        k["moneyline"], logloss(df["cand_p_ml"], win), logloss(df["v7_served_p_ml"], win), allm
    )
    out["moneyline_brier_delta_vs_v7"] = mean_diff(
        None,
        (df["cand_p_ml"].to_numpy(float) - win) ** 2,
        (df["v7_served_p_ml"].to_numpy(float) - win) ** 2,
        allm,
    )
    out["mu_equals_v7"] = bool(
        np.allclose(df["cand_mu_margin"], df["v7_served_mu_margin"], atol=1e-6, rtol=0)
    )
    return out


def leak_flags(blocks: dict[str, Any], name: str) -> list[str]:
    flags = []
    cand, line = blocks[name], blocks["closing_line"]
    for s, e in cand["margin_by_season"].items():
        le = line["margin_by_season"].get(s)
        if e["rmse"] is not None and le and le["rmse"] is not None and e["rmse"] < le["rmse"]:
            flags.append(f"rmse_below_line season {s}: {e['rmse']} < {le['rmse']}")
    for s, c in cand["market_spread"]["by_season"].items():
        if abs(c["corr"]) > LEAK_CORR:
            flags.append(f"market_corr season {s}: {c['corr']}")
    return flags


def verdict(
    family: str | None, registered: bool, paired: dict[str, Any], coverage_ok: bool, flags: list[str]
) -> dict[str, Any]:
    if not registered:
        return {
            "status": "UNREGISTERED",
            "reason": "grid_name not in the pinned grid: exploratory, no verdict",
        }
    if not coverage_ok:
        return {
            "status": "INCOMPLETE",
            "reason": f"coverage below {COVERAGE_MIN:.0%} of the cohort in a season",
        }
    if flags:
        return {"status": "LEAK_SUSPECT", "reason": flags}
    if family == "scale" and not paired["mu_equals_v7"]:
        family = "mean"
    if family == "mean":
        c = paired["spread_corr_delta_vs_v7"]
        checks = {
            "corr_delta_adj_lower_gt_0": c["ci_adj"][0] > 0,
            "rmse_delta_point_le_0": paired["margin_rmse_delta_vs_v7"]["delta"] <= 0,
            "late_folds_corr_delta_gt_0": c.get("late_folds_point", -1.0) > 0,
        }
    elif family == "scale":
        c = paired["cover_logloss_delta_vs_v7"]
        checks = {
            "cover_logloss_delta_adj_upper_lt_0": c["ci_adj"][1] < 0,
            "ml_brier_delta_point_le_0": paired["moneyline_brier_delta_vs_v7"]["delta"] <= 0,
            "late_folds_cover_logloss_delta_lt_0": c.get("late_folds_point", 1.0) < 0,
        }
    else:
        c = paired["moneyline_logloss_delta_vs_v7"]
        checks = {
            "ml_logloss_delta_adj_upper_lt_0": c["ci_adj"][1] < 0,
            "ml_brier_delta_point_le_0": paired["moneyline_brier_delta_vs_v7"]["delta"] <= 0,
            "late_folds_ml_logloss_delta_lt_0": c.get("late_folds_point", 1.0) < 0,
        }
    ok = all(checks.values())
    return {
        "family_scored": family,
        "checks": checks,
        "status": "DEV_ELIGIBLE" if ok else "NOT_ELIGIBLE",
        "meaning": "eligible for the sealed confirmation window; NOT 'beats v7' until confirmation passes"
        if ok
        else "does not beat v7 under the pinned development rule",
    }


def _append_read_log(entry: dict[str, Any]) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prior = (
        [json.loads(x) for x in READ_LOG.read_text(encoding="utf-8").splitlines() if x.strip()]
        if READ_LOG.exists()
        else []
    )
    with READ_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    shas = {p["predictions_sha256"] for p in prior if p["grid_name"] == entry["grid_name"]}
    shas.add(entry["predictions_sha256"])
    return len(shas)


def score(
    predictions: str | Path | pd.DataFrame | None = None,
    meta: dict[str, Any] | None = None,
    *,
    window: str = "development",
    shuffle_seed: int | None = None,
    archive: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Score a candidate (or only the baselines when ``predictions`` is None) on the pinned window.

    Returns the scorecard dict; ``render_markdown(scorecard)`` gives the table. The cohort is every D1-vs-D1
    game of the window with a closing spread line, intersected with the candidate's predictions.
    """
    proto = load_protocol()
    if window != "development":
        if window in ("confirmation_backcast", "forward_final"):
            raise LockedWindowError(f"window {window!r} is sealed under protocol {proto['id']}")
        raise ValueError(f"unknown window {window!r}")
    seasons = DEV_SEASONS
    a = archive if archive is not None else load_archive(seasons, shuffle_seed=shuffle_seed)
    if a.attrs.get("line_source", {}).get("name") != DEV_LINE_SOURCE:
        raise SubmissionError(f"development archive must come from load_archive with {DEV_LINE_SOURCE}")
    base = baseline_predictions(a)
    cohort = a[a["d1"] & a["line"].notna()].copy()
    models: dict[str, pd.DataFrame] = dict(base)
    info: dict[str, Any] = {"registered": True, "family": None}
    cand_name = None
    if predictions is not None:
        if meta is None:
            raise SubmissionError("a sidecar meta dict is required with candidate predictions")
        pred = load_predictions(predictions)
        validate_echo(pred, a)
        info = validate_meta(meta, proto, sorted(set(pred["season"]) & set(seasons)) or seasons)
        cand_name = "cand"
        models["cand"] = pred
    read_count = None
    if shuffle_seed is None:  # every real read is logged BEFORE any statistic exists; there is no bypass
        psha = (
            hashlib.sha256(
                pd.util.hash_pandas_object(models["cand"], index=False).values.tobytes()
            ).hexdigest()
            if cand_name
            else "baselines"
        )
        read_count = _append_read_log(
            {
                "at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "grid_name": meta["grid_name"] if cand_name and meta else "BASELINE",
                "candidate": meta["candidate"] if cand_name and meta else "v7_served+v6_asof+closing_line",
                "window": window,
                "line_source": a.attrs["line_source"]["name"],
                "protocol_sha256": PROTOCOL_SHA256,
                "predictions_sha256": psha,
            }
        )
    cols = ["mu_margin", "scale_margin", "dist", "p_home_ml", "mu_total", "scale_total", "dist_total"]
    cols.append("games_played_min")
    df = cohort[["event_id", "season", "game_date", "margin", "total", "line", "total_line", "sample"]].copy()
    coverage: dict[str, Any] = {}
    for name, m in models.items():
        sub = m[["event_id", *cols]].rename(columns={c: f"{name}_{c}" for c in cols})
        before = df.groupby("season").size()
        df = df.merge(sub, on="event_id", how="inner")
        after = df.groupby("season").size()
        if name == "cand":
            coverage = {int(s): round(float(after.get(s, 0) / before[s]), 5) for s in before.index}
    # buckets always use the archive's point-in-time sample so every scorecard buckets games identically
    df["games_played_min"] = df["sample"].astype(float)
    sigma = fold_sigma(df)
    for name in ["closing_line", *[m for m in models if m != "closing_line"]]:
        df = _model_columns(df, name, sigma)
    order = [*([cand_name] if cand_name else []), "v7_served", "v6_asof", "closing_line"]
    blocks = {name: model_block(df, name, proto) for name in order}
    card: dict[str, Any] = {
        "protocol_id": proto["id"],
        "protocol_sha256": PROTOCOL_SHA256,
        "window": window,
        "seasons": list(seasons),
        "shuffled_outcomes": shuffle_seed is not None,
        "addendum_sha256": ADDENDUM_SHA256,
        "line_source": a.attrs["line_source"],
        "read_count_for_grid_name": read_count,
        "cohort": {
            "definition": proto["cohort"]["games"],
            "n_scored": len(df),
            "n_by_season": {int(s): int(n) for s, n in df.groupby("season").size().items()},
            "n_d1_unlined_not_scored": int((a["d1"] & a["line"].isna()).sum()),
            "closing_line_sigma_by_fold": {s: round(v, 4) for s, v in sigma.items()},
        },
        "models": blocks,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if cand_name:
        assert meta is not None
        paired = paired_vs_v7(df, proto["multiplicity"]["K"])
        flags = leak_flags(blocks, "cand")
        cov_ok = bool(coverage) and min(coverage.values()) >= COVERAGE_MIN
        card["candidate"] = {
            "label": meta["candidate"],
            "grid_name": meta["grid_name"],
            "code_sha256": meta["code_sha256"],
            "fit_seasons_by_predicted_season": info["fit_seasons"],
            "coverage_by_season": coverage,
            "leak_flags": flags,
        }
        card["paired_vs_v7"] = paired
        card["verdict"] = verdict(info["family"], info["registered"], paired, cov_ok, flags)
        if info["registered"] and read_count is not None and read_count > 1:
            card["verdict"] = {
                "status": "REREAD_NO_VERDICT",
                "reason": f"read {read_count} of grid name {meta['grid_name']}: only the first predictions file "
                "carries a verdict unless an amendment registered the change",
                "would_have_been": card["verdict"],
            }
        card["edge_dev"] = {
            "spread": blocks["cand"]["rule1_spread"]["edge_eligible"],
            "total": (blocks["cand"].get("totals") or {}).get("rule1_total", {}).get("edge_eligible"),
        }
    return card


# --------------------------------------------------------------------------------------------- reproduction
M5R_2025 = {
    "n_d1": 5764,
    "n_lined": 5763,
    "rmse": 11.50,
    "cover_brier": 0.2585,
    "slope": -0.085,
    "slope_ci": [-0.215, 0.052],
    "line_rmse": 11.19,
}


def reproduction_gate(archive: pd.DataFrame) -> dict[str, Any]:
    """Served v7 on 2025 must reproduce the published m5r numbers (aborts otherwise)."""
    a = archive[archive["season"] == 2025]
    v7 = baseline_predictions(a)["v7_served"].set_index("event_id")
    d1 = a[a["d1"]].copy()
    d1["mu"], d1["sc"] = d1["event_id"].map(v7["mu_margin"]), d1["event_id"].map(v7["scale_margin"])
    lined = d1[d1["line"].notna()]
    y = lined["margin"].to_numpy(float) - lined["line"].to_numpy(float)
    x = lined["mu"].to_numpy(float) - lined["line"].to_numpy(float)
    ws = nr.window_stats(x, y, lined["game_date"].tolist(), 0.0, 1)
    cov = p_above_line(lined["mu"], lined["sc"], lined["line"], np.full(len(lined), "t8"))
    keep = y != 0
    got = {
        "n_d1": len(d1),
        "n_lined": len(lined),
        "rmse": float(np.sqrt(np.mean((d1["margin"] - d1["mu"]) ** 2))),
        "cover_brier": float(np.mean((cov[keep] - (y[keep] > 0)) ** 2)),
        "slope": ws["slope"],
        "slope_ci": ws["slope_ci_95"],
        "line_rmse": float(np.sqrt(np.mean(y**2))),
    }
    ok = (
        got["n_d1"] == M5R_2025["n_d1"]
        and got["n_lined"] == M5R_2025["n_lined"]
        and abs(got["rmse"] - M5R_2025["rmse"]) < 0.005
        and abs(got["cover_brier"] - M5R_2025["cover_brier"]) < 0.0005
        and abs(got["slope"] - M5R_2025["slope"]) < 0.001
        and all(abs(g - e) < 0.001 for g, e in zip(got["slope_ci"], M5R_2025["slope_ci"], strict=True))
        and abs(got["line_rmse"] - M5R_2025["line_rmse"]) < 0.005
    )
    out = {
        "expected": M5R_2025,
        "got": {k: (round(v, 5) if isinstance(v, float) else v) for k, v in got.items()},
        "passed": ok,
    }
    if not ok:
        raise RuntimeError(f"reproduction gate failed: {out}")
    return out


# --------------------------------------------------------------------------------------------- rendering
def _f(v: Any, nd: int = 3) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def _ci_txt(c: list[float] | None, nd: int = 3) -> str:
    return f"[{c[0]:+.{nd}f}, {c[1]:+.{nd}f}]" if c else "-"


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def _cell(e: dict[str, Any]) -> str:
    return f"{e['rmse']:.2f} / {e['bias']:+.2f} (n {e['n']})" if e.get("rmse") is not None else "-"


def render_markdown(card: dict[str, Any]) -> str:
    m = card["models"]
    names = list(m)
    out = [
        f"# NCAAB closing-line scorecard ({card['window']}, protocol `{card['protocol_id']}`)",
        "",
        (
            f"Protocol sha256 `{card['protocol_sha256']}`. Seasons {card['seasons']} (leave-one-season-out). "
            f"Cohort: {card['cohort']['n_scored']} D1-vs-D1 games with a closing spread line, identical for every "
            f"row (by season {card['cohort']['n_by_season']}; {card['cohort']['n_d1_unlined_not_scored']} unlined "
            "D1 games not scored). Points and probabilities only. "
            f"Line source `{card['line_source']['name']}` ({card['line_source']['rule']}, sha256 "
            f"`{card['line_source']['sha256'][:16]}...`)."
        ),
    ]
    if card["shuffled_outcomes"]:
        out += ["", "> PLUMBING RUN ON SHUFFLED OUTCOMES. No number below is evidence of anything."]
    out += ["", "## Market-relative (primary): (actual - closing line) on (model - closing line)", ""]
    rows = []
    for n in names:
        b = m[n]
        if isinstance(b["market_spread"], str):
            rows.append(
                [
                    n,
                    len(card["cohort"]["n_by_season"]) and card["cohort"]["n_scored"],
                    "N/A",
                    "-",
                    "N/A",
                    "-",
                    "N/A",
                    "-",
                    "-",
                ]
            )
            continue
        s, r = b["market_spread"], b["rule1_spread"]
        rows.append(
            [
                n,
                s["n"],
                f"{s['slope']:+.3f}",
                _ci_txt(s["slope_ci95"]),
                f"{s['corr']:+.4f}",
                _ci_txt(s["corr_ci95"], 4),
                f"{r['mse_delta']:+.4f}",
                _ci_txt(r["mse_delta_ci_adj"], 4),
                "yes" if r["edge_eligible"] else "no",
            ]
        )
    out.append(
        _table(
            [
                "model",
                "n",
                "slope",
                "95% CI",
                "corr",
                "95% CI",
                "rule-1 MSE delta",
                f"K-adj CI (K {m[names[0]]['rule1_spread']['k'] if not isinstance(m[names[0]]['rule1_spread'], str) else '-'})",
                "edge-eligible",
            ],
            rows,
        )
    )
    out += ["", "## Margin RMSE / bias (actual - predicted) by games played", ""]
    buckets = [b[0] for b in GAMES_BUCKETS]
    out.append(
        _table(
            ["model", "all", *buckets],
            [
                [n, _cell(m[n]["margin_all"]), *[_cell(m[n]["margin_by_games_played"][b]) for b in buckets]]
                for n in names
            ],
        )
    )
    out += ["", "## Margin RMSE / bias by |closing line|", ""]
    lb = [b[0] for b in LINE_BUCKETS]
    out.append(
        _table(["model", *lb], [[n, *[_cell(m[n]["margin_by_abs_line"][b]) for b in lb]] for n in names])
    )
    out += ["", "## Margin RMSE by held-out season", ""]
    seasons = sorted(m[names[0]]["margin_by_season"])
    out.append(
        _table(
            ["model", *map(str, seasons)],
            [[n, *[_cell(m[n]["margin_by_season"][s]) for s in seasons]] for n in names],
        )
    )
    out += ["", "## Cover probability at the closing line (pushes dropped)", ""]
    rows = []
    for n in names:
        c = m[n]["cover"]
        if isinstance(c, str):
            rows.append([n, "N/A", "-", "-", "-", "-", "-", "-"])
            continue
        rel = c["reliability_favoured_side"]
        rows.append(
            [
                n,
                c["n"],
                f"{c['brier']:.4f}",
                f"{c['logloss']:.4f}",
                *[f"{rel[k]['n']} / {_f(rel[k]['mean_p'])} / {_f(rel[k]['hit'])}" for k in rel],
            ]
        )
    out.append(
        _table(
            ["model", "n", "Brier", "log loss", "fav 0.5-0.6 n/p/hit", "0.6-0.7", "0.7-0.8", "0.8-1.0"], rows
        )
    )
    out += ["", "## Moneyline (home win), against the closing-spread-implied probability", ""]
    rows = []
    for n in names:
        ml = m[n]["moneyline"]
        mm = m[n]["market_moneyline"]
        rows.append(
            [
                n,
                ml["n"],
                f"{ml['brier']:.4f}",
                f"{ml['logloss']:.4f}",
                "N/A" if isinstance(mm, str) else f"{mm['corr']:+.4f} {_ci_txt(mm['corr_ci95'], 4)}",
            ]
        )
    out.append(_table(["model", "n", "Brier", "log loss", "corr(win - p_line, p - p_line), 95% CI"], rows))
    sig = card["cohort"]["closing_line_sigma_by_fold"]
    out += [
        "",
        f"p_line = 1 - Phi((0.5 - L) / sigma); sigma by held-out season (fit on the other seasons): {sig}.",
    ]
    tot_names = [n for n in names if "totals" in m[n]]
    if tot_names:
        out += ["", "## Totals (games with a closing total)", ""]
        rows = []
        for n in tot_names:
            t = m[n]["totals"]
            mt = t.get("market_total")
            rows.append(
                [
                    n,
                    _cell(t["total_all"]),
                    "N/A" if mt is None else f"{mt['slope']:+.3f} {_ci_txt(mt['slope_ci95'])}",
                    "N/A" if mt is None else f"{mt['corr']:+.4f}",
                    "N/A"
                    if mt is None
                    else f"{t['rule1_total']['mse_delta']:+.4f} {_ci_txt(t['rule1_total']['mse_delta_ci_adj'], 4)}",
                    "N/A" if mt is None else f"{t['over']['brier']:.4f} / {t['over']['logloss']:.4f}",
                ]
            )
        out.append(
            _table(
                [
                    "model",
                    "RMSE / bias",
                    "slope, 95% CI",
                    "corr",
                    "rule-1 MSE delta, K-adj CI",
                    "over Brier / log loss",
                ],
                rows,
            )
        )
        out += [
            "",
            "v7_served totals are the served v6 total (v7 never serves totals), so they equal v6_asof.",
        ]
    if "paired_vs_v7" in card:
        p = card["paired_vs_v7"]
        out += ["", "## Candidate minus served v7 (identical games)", ""]
        rows = []
        for key in (
            "spread_corr_delta_vs_v7",
            "margin_rmse_delta_vs_v7",
            "cover_logloss_delta_vs_v7",
            "moneyline_logloss_delta_vs_v7",
            "moneyline_brier_delta_vs_v7",
        ):
            v = p[key]
            rows.append(
                [
                    key,
                    f"{v['delta']:+.5f}",
                    _ci_txt(v["ci95"], 5),
                    _ci_txt(v.get("ci_adj"), 5),
                    v.get("k", "-"),
                    _f(v.get("late_folds_point"), 5),
                ]
            )
        out.append(_table(["statistic", "delta", "95% CI", "K-adj CI", "K", "2025+2026 folds"], rows))
        c = card["candidate"]
        out += [
            "",
            f"Candidate `{c['label']}` grid `{c['grid_name']}`; coverage {c['coverage_by_season']}; leak flags {c['leak_flags'] or 'none'}.",
            (
                f"**Verdict: {card['verdict']['status']}**: {card['verdict'].get('meaning', card['verdict'].get('reason'))} "
                f"{card['verdict'].get('checks', '')}. Edge (rule-1, development): {card['edge_dev']}."
            ),
        ]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------------- export / cli
def export_predictions(model: str, out: Path, archive: pd.DataFrame | None = None) -> int:
    """Write a baseline's predictions in the submission schema (outcome-free) plus its sidecar meta."""
    a = archive if archive is not None else load_archive(DEV_SEASONS)
    pred = baseline_predictions(a)[model]
    pred = pred[pred["event_id"].isin(a.loc[a["d1"], "event_id"])]
    out.parent.mkdir(parents=True, exist_ok=True)
    pred.to_json(out, orient="records", lines=True)
    meta = {
        "candidate": f"baseline {model}",
        "grid_name": f"BASELINE_{model}",
        "protocol_sha256": PROTOCOL_SHA256,
        "code_sha256": nr.sha256_file(Path(__file__)),
        "fit_seasons_by_predicted_season": {
            "2023": [2024],
            "2024": [2023],
            "2025": [2023, 2024],
            "2026": [2023, 2024],
        },
        "history_cutoff": "strictly before Eastern midnight of the game day",
        "data_sources": {str(COMPONENTS.relative_to(REPO)): nr.sha256_file(COMPONENTS)},
    }
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return len(pred)


def _write(card: dict[str, Any], stem: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.json").write_text(
        json.dumps(card, indent=2, sort_keys=True, default=_jsonable), encoding="utf-8"
    )
    (out_dir / f"{stem}.md").write_text(render_markdown(card), encoding="utf-8")


def _jsonable(o: Any) -> Any:
    if isinstance(o, np.integer | np.floating | np.bool_):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--shuffle-seed", type=int, default=None)
    b.add_argument("--out-dir", default=str(OUT_DIR))
    s = sub.add_parser("score")
    s.add_argument("--pred", required=True)
    s.add_argument("--meta", required=True)
    s.add_argument("--shuffle-seed", type=int, default=None)
    s.add_argument("--out-dir", default=str(OUT_DIR))
    e = sub.add_parser("export")
    e.add_argument("--model", choices=BASELINES[:2], required=True)
    e.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    print("protocol", load_protocol()["id"], PROTOCOL_SHA256, flush=True)
    if args.cmd == "export":
        print("wrote", export_predictions(args.model, Path(args.out)), "rows ->", args.out)
        return 0
    a = load_archive(DEV_SEASONS, shuffle_seed=args.shuffle_seed)
    gates = {"elo_reconstruction": elo_reconstruction_gate(a)}
    if args.shuffle_seed is None:
        gates["m5r_reproduction_2025"] = reproduction_gate(a)
    if args.cmd == "baseline":
        card = score(None, None, shuffle_seed=args.shuffle_seed, archive=a)
        stem = "baseline_scorecard" if args.shuffle_seed is None else f"baseline_shuffled_{args.shuffle_seed}"
    else:
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
        card = score(args.pred, meta, shuffle_seed=args.shuffle_seed, archive=a)
        stem = Path(args.pred).stem + ("" if args.shuffle_seed is None else f"_shuffled_{args.shuffle_seed}")
    card["gates"] = gates
    _write(card, stem, Path(args.out_dir))
    print("wrote", Path(args.out_dir) / f"{stem}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
