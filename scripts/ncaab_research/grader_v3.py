"""Totals grader for addendum v3 of NCAAB research protocol 261120 (quick 261121-hmc, lane T).

Imports ``grader.py`` unchanged (its protocol, addendum-v2 and data-file pins are verified on every call) and adds
what the v1 grader cannot do: a totals verdict against the served v6 total, the T0/S0 diagnostics, the totals
reproduction gate and the v3 multiplicity (K). ``protocol_261120_addendum_v3.json`` is pinned by its canonical
sha256 below; any edit makes this module refuse to run.

Research only: reads the untracked archives under data/research/ and the CBBD cache (zero API calls), writes
only under outputs/ncaab/research_261121/ and appends to outputs/ncaab/research_261120/read_log.jsonl. Points
and probabilities only, never American odds.

    .venv/Scripts/python scripts/ncaab_research/grader_v3.py repro      # totals reproduction gate (logged)
    .venv/Scripts/python scripts/ncaab_research/grader_v3.py t0         # T0 diagnostics (logged read)
    .venv/Scripts/python scripts/ncaab_research/grader_v3.py s0         # S0 diagnostic (logged read)
    .venv/Scripts/python scripts/ncaab_research/grader_v3.py score --pred P --meta M [--shuffle-seed 5]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import grader  # byte-identical v1 grader; verifies the v1/v2 pins on load_protocol

nr = grader.nr
REPO = grader.REPO
ADDENDUM_V3_PATH = HERE / "protocol_261120_addendum_v3.json"
ADDENDUM_V3_SHA256 = "d098238e340f192eb61339a9f936a96e84f49fc00b5e944b9c312dc323b1b337"
OUT_V3 = REPO / "outputs" / "ncaab" / "research_261121"
READ_LOG = grader.READ_LOG  # shared with v1; tests monkeypatch this attribute
BASELINE_CARD = grader.OUT_DIR / "baseline_scorecard.json"
CBBD_GAMES = REPO / "data" / "research" / "ncaab_external" / "cbbd" / "games"

K_TOTAL = 26
K_SPREAD = 23
K_V1_TOTAL = 20  # the reproduction gate reproduces the baseline card at v1's K
GRID = {
    "T0_total_diagnostics": "diagnostic",
    "T1_tempo_eff_total": "mean",
    "T2_total_combiner": "mean",
    "T3_total_scale": "scale",
    "S0_open_line_diagnostic": "diagnostic",
}
SCORED_GRID = ("T1_tempo_eff_total", "T2_total_combiner", "T3_total_scale")
LN2 = math.log(2.0)
LATE_FOLDS = grader.LATE_FOLDS
COVERAGE_MIN = grader.COVERAGE_MIN
LEAK_CORR = grader.LEAK_CORR
ASOF_MIN_PRIOR = 100  # T0a: as-of values are reported once this many prior cohort games exist in the season
S0_SEASONS = (2024, 2025, 2026)
CONFIRMATION_TOTAL_SEASONS = (2018, 2019, 2020, 2022)


# --------------------------------------------------------------------------------------------- pin
def load_addendum_v3(path: Path = ADDENDUM_V3_PATH) -> dict[str, Any]:
    """Verify v1 + v2 (through grader.load_protocol) and the v3 pin; return the v3 object."""
    grader.load_protocol()
    add = json.loads(path.read_text(encoding="utf-8"))
    got = grader.canonical_sha256(add)
    if got != ADDENDUM_V3_SHA256:
        raise grader.ProtocolMismatchError(f"{path} hashes to {got}, pinned {ADDENDUM_V3_SHA256}")
    k = add["multiplicity"]["K"]
    if (k["total"], k["spread_market_relative"]) != (K_TOTAL, K_SPREAD):
        raise grader.ProtocolMismatchError("v3 K drifted from the module constants")
    if set(add["candidate_grid"]) != set(GRID):
        raise grader.ProtocolMismatchError("v3 grid drifted from the module constants")
    return add


# --------------------------------------------------------------------------------------------- read log
def _read_log_entries(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or READ_LOG
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def append_read_log(entry: dict[str, Any]) -> int:
    """Append a v3 read (BEFORE any statistic exists) and return the number of distinct prediction files read
    under this grid name in this window, this one included. Entries keep grid_name and predictions_sha256,
    which grader._append_read_log indexes on every prior line."""
    for key in ("grid_name", "predictions_sha256", "window", "candidate"):
        if key not in entry:
            raise ValueError(f"read-log entry lacks {key!r}")
    entry = {
        **entry,
        "at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "addendum_v3_sha256": ADDENDUM_V3_SHA256,
    }
    prior = _read_log_entries()
    READ_LOG.parent.mkdir(parents=True, exist_ok=True)
    with READ_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    shas = {
        p["predictions_sha256"]
        for p in prior
        if p.get("grid_name") == entry["grid_name"] and p.get("window") == entry["window"]
    }
    shas.add(entry["predictions_sha256"])
    return len(shas)


def frame_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


# --------------------------------------------------------------------------------------------- periods
def load_periods(event_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """ESPN event id -> {n_ot, reg_total, home_points, away_points} from the CBBD games cache.

    Only ids passed in (callers pass lock-filtered ids) are returned. A game whose period scores do not sum to
    its CBBD final score is left out (periods unknown)."""
    want = {str(e) for e in event_ids}
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(glob.glob(str(CBBD_GAMES / "*.json"))):
        if p.endswith(".meta.json"):
            continue
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        for g in data if isinstance(data, list) else data.get("data", []):
            eid = str(g.get("sourceId") or "")
            if eid not in want or eid in out:
                continue
            hp, ap = g.get("homePeriodPoints"), g.get("awayPeriodPoints")
            hs, as_ = g.get("homePoints"), g.get("awayPoints")
            if not (isinstance(hp, list) and isinstance(ap, list)) or len(hp) < 2 or len(hp) != len(ap):
                continue
            if hs is None or as_ is None or sum(hp) != hs or sum(ap) != as_:
                continue
            out[eid] = {
                "n_ot": len(hp) - 2,
                "reg_total": int(hp[0] + hp[1] + ap[0] + ap[1]),
                "home_points": int(hs),
                "away_points": int(as_),
            }
    return out


# --------------------------------------------------------------------------------------------- frames
def _v6_frame(archive: pd.DataFrame) -> pd.DataFrame:
    """Served v6 total (normal, v6_total_sd) for the archive rows (grader's v6_asof baseline)."""
    v6 = grader.baseline_predictions(archive)["v6_asof"]
    return v6[["event_id", "mu_total", "scale_total"]].rename(
        columns={"mu_total": "v6_mu_total", "scale_total": "v6_scale_total"}
    )


def totals_cohort(archive: pd.DataFrame) -> pd.DataFrame:
    """D1-vs-D1, closing spread AND closing total (the v1 totals cohort), with the served v6 total."""
    c = archive[archive["d1"] & archive["line"].notna() & archive["total_line"].notna()].copy()
    cols = ["event_id", "season", "game_date", "neutral", "sample", "total", "total_line", "margin", "line"]
    c = c[cols].merge(_v6_frame(archive), on="event_id", how="left")
    c = c[c["v6_mu_total"].notna() & (c["v6_scale_total"] > 0)]
    return c.reset_index(drop=True)


def p_over(mu: np.ndarray, sd: np.ndarray, line: np.ndarray, dist: np.ndarray | str = "normal") -> np.ndarray:
    d = np.broadcast_to(np.asarray(dist), np.shape(mu)) if np.ndim(dist) == 0 else np.asarray(dist)
    return grader.p_above_line(mu, sd, line, d)


# --------------------------------------------------------------------------------------------- statistics
def totals_block(t: pd.DataFrame, mu_col: str, p_col: str | None, k: int) -> dict[str, Any]:
    """RMSE/bias tables, market slope/corr, rule-1 and over calibration for one total column."""
    y, mu, line = t["total"].to_numpy(float), t[mu_col].to_numpy(float), t["total_line"].to_numpy(float)
    blk: dict[str, Any] = {
        "total_all": grader._err(y, mu),
        "total_by_games_played": grader._bucket_table(
            t, t["sample"].to_numpy(float), grader.GAMES_BUCKETS, "total", mu_col
        ),
        "total_by_season": {
            int(s): grader._err(g["total"].to_numpy(float), g[mu_col].to_numpy(float))
            for s, g in t.groupby("season")
        },
    }
    if p_col is None:
        return blk
    x, yt = mu - line, y - line
    days = t["game_date"].tolist()
    mk = grader.market_plain(x, yt, days)
    _, rs = grader._boot(days, grader._sums(x, yt), grader._corr_from_sums)
    mk["corr_ci_adj"] = grader._ci(rs, k)["ci_adj"]
    mk["by_season"] = {
        int(s): {"n": len(g), "corr": round(float(np.corrcoef(g["_x"], g["_y"])[0, 1]), 5)}
        for s, g in t.assign(_x=x, _y=yt).groupby("season")
    }
    blk["market_total"] = mk
    blk["rule1_total"] = grader.rule1_loso(t.assign(_x=x, _y=yt), "_x", "_y", k)
    po = t[p_col].to_numpy(float)
    blk["over"] = grader._prob_block(np.where(yt == 0, np.nan, po), (yt > 0).astype(float))
    return blk


def _mean_diff(days: list[str], a: np.ndarray, b: np.ndarray, mask: np.ndarray, late: np.ndarray, k: int):
    ok = mask & ~np.isnan(a) & ~np.isnan(b)
    dd = [d for d, m in zip(days, ok, strict=True) if m]
    pt, smp = grader._boot(dd, {"d": a[ok] - b[ok], "n": np.ones(int(ok.sum()))}, lambda s: s["d"] / s["n"])
    res: dict[str, Any] = {"delta": round(pt, 6), "n": int(ok.sum()), **grader._ci(smp, k)}
    lm = ok & late
    if lm.any():
        res["late_folds_point"] = round(float(np.mean(a[lm] - b[lm])), 6)
    return res, smp


def paired_vs_v6(t: pd.DataFrame, k: int = K_TOTAL) -> dict[str, Any]:
    """Candidate (cand_*) minus served v6 (v6_*) on identical totals-cohort games."""
    days = t["game_date"].tolist()
    y, line = t["total"].to_numpy(float), t["total_line"].to_numpy(float)
    yt = y - line
    xc, xv = t["cand_mu_total"].to_numpy(float) - line, t["v6_mu_total"].to_numpy(float) - line
    late = t["season"].isin(LATE_FOLDS).to_numpy()
    out: dict[str, Any] = {}

    def corr_sums(m: np.ndarray) -> dict[str, np.ndarray]:
        s = grader._sums(xc[m], yt[m], "c")
        s.update({k2: v for k2, v in grader._sums(xv[m], yt[m], "v").items() if k2 not in ("n", "y", "yy")})
        return s

    def dcorr(s: dict[str, np.ndarray]) -> np.ndarray:
        return grader._corr_from_sums(s, "c") - grader._corr_from_sums(s, "v")

    allm = np.ones(len(t), bool)
    pt, smp = grader._boot(days, corr_sums(allm), dcorr)
    out["total_corr_delta_vs_v6"] = {"delta": round(pt, 5), **grader._ci(smp, k)}
    out["total_corr_delta_vs_v6"]["one_sided_95_lower"] = round(float(np.nanpercentile(smp, 5)), 5)
    if late.any():
        late_days = [d for d, m in zip(days, late, strict=True) if m]
        out["total_corr_delta_vs_v6"]["late_folds_point"] = round(
            grader._boot(late_days, corr_sums(late), dcorr)[0], 5
        )
    ec = (y - t["cand_mu_total"].to_numpy(float)) ** 2
    ev = (y - t["v6_mu_total"].to_numpy(float)) ** 2

    def drmse(s: dict[str, np.ndarray]) -> np.ndarray:
        return np.sqrt(s["ec"] / s["n"]) - np.sqrt(s["ev"] / s["n"])

    pt, smp = grader._boot(days, {"ec": ec, "ev": ev, "n": np.ones(len(t))}, drmse)
    out["total_rmse_delta_vs_v6"] = {"delta": round(pt, 5), **grader._ci(smp, k)}
    over = (yt > 0).astype(float)
    no_push = yt != 0
    llc = grader.logloss(t["cand_p_over"].to_numpy(float), over)
    llv = grader.logloss(t["v6_p_over"].to_numpy(float), over)
    res, smp = _mean_diff(days, llc, llv, no_push, late, k)
    res["one_sided_95_upper"] = round(float(np.nanpercentile(smp, 95)), 6)
    out["over_logloss_delta_vs_v6"] = res
    res, smp = _mean_diff(days, llc, np.full(len(t), LN2), no_push, late, k)
    res["one_sided_95_upper"] = round(float(np.nanpercentile(smp, 95)), 6)
    out["over_logloss_vs_ln2"] = res
    bc = (t["cand_p_over"].to_numpy(float) - over) ** 2
    bv = (t["v6_p_over"].to_numpy(float) - over) ** 2
    out["over_brier_delta_vs_v6"] = _mean_diff(days, bc, bv, no_push, late, k)[0]
    out["mu_equals_v6"] = bool(np.allclose(t["cand_mu_total"], t["v6_mu_total"], atol=1e-6, rtol=0))
    return out


def leak_flags_totals(cand: dict[str, Any], line_by_season: dict[int, dict[str, Any]]) -> list[str]:
    flags = []
    for s, e in cand["total_by_season"].items():
        le = line_by_season.get(s)
        if e["rmse"] is not None and le and le["rmse"] is not None and e["rmse"] < le["rmse"]:
            flags.append(f"rmse_below_line season {s}: {e['rmse']} < {le['rmse']}")
    for s, c in cand["market_total"]["by_season"].items():
        if abs(c["corr"]) > LEAK_CORR:
            flags.append(f"market_corr season {s}: {c['corr']}")
    return flags


def verdict_v3(
    family: str | None, registered: bool, paired: dict[str, Any], coverage_ok: bool, flags: list[str]
) -> dict[str, Any]:
    if not registered:
        return {"status": "UNREGISTERED", "reason": "grid_name not in the v3 scored grid: no verdict"}
    if not coverage_ok:
        return {"status": "INCOMPLETE", "reason": f"coverage below {COVERAGE_MIN:.0%} in a held-out season"}
    if flags:
        return {"status": "LEAK_SUSPECT", "reason": flags}
    if family == "mean":
        c = paired["total_corr_delta_vs_v6"]
        checks = {
            "corr_delta_adj_lower_gt_0": c["ci_adj"][0] > 0,
            "rmse_delta_point_le_0": paired["total_rmse_delta_vs_v6"]["delta"] <= 0,
            "late_folds_corr_delta_gt_0": c.get("late_folds_point", -1.0) > 0,
        }
    elif family == "scale":
        c = paired["over_logloss_delta_vs_v6"]
        checks = {
            "over_logloss_delta_adj_upper_lt_0": c["ci_adj"][1] < 0,
            "over_logloss_vs_ln2_adj_upper_lt_0": paired["over_logloss_vs_ln2"]["ci_adj"][1] < 0,
            "late_folds_over_logloss_delta_lt_0": c.get("late_folds_point", 1.0) < 0,
            "over_brier_delta_point_le_0": paired["over_brier_delta_vs_v6"]["delta"] <= 0,
        }
    else:
        raise ValueError(f"family {family!r} carries no verdict")
    ok = all(checks.values())
    return {
        "family_scored": family,
        "checks": checks,
        "status": "DEV_ELIGIBLE" if ok else "NOT_ELIGIBLE",
        "meaning": "eligible for the sealed totals confirmation window (2018-2020 + 2022); NOT 'beats served "
        "v6 total' until confirmation passes"
        if ok
        else "does not beat the served v6 total under the pinned development rule",
    }


# --------------------------------------------------------------------------------------------- submissions
def validate_meta_v3(meta: dict[str, Any], seasons: Iterable[int]) -> dict[str, Any]:
    for key in (
        "candidate",
        "grid_name",
        "protocol_sha256",
        "addendum_v3_sha256",
        "code_sha256",
        "fit_seasons_by_predicted_season",
    ):
        if key not in meta:
            raise grader.SubmissionError(f"sidecar meta lacks {key!r}")
    if meta["protocol_sha256"] != grader.PROTOCOL_SHA256:
        raise grader.SubmissionError("sidecar protocol_sha256 does not match the pinned v1 protocol")
    if meta["addendum_v3_sha256"] != ADDENDUM_V3_SHA256:
        raise grader.SubmissionError("sidecar addendum_v3_sha256 does not match the pinned addendum v3")
    fits = {int(k): [int(x) for x in v] for k, v in meta["fit_seasons_by_predicted_season"].items()}
    for s in seasons:
        if s not in fits:
            raise grader.SubmissionError(f"sidecar has no fit seasons for predicted season {s}")
        if s in fits[s]:
            raise grader.SubmissionError(f"leak: season {s} is among its own fit seasons {fits[s]}")
        if any(f >= grader.LOCK_SEASON_MIN for f in fits[s]):
            raise grader.LockedWindowError(f"fit seasons for {s} include a locked season")
    registered = meta["grid_name"] in SCORED_GRID
    return {
        "registered": registered,
        "family": GRID[meta["grid_name"]] if registered else None,
        "fit_seasons": fits,
    }


def score_totals(
    predictions: str | Path | pd.DataFrame,
    meta: dict[str, Any],
    *,
    shuffle_seed: int | None = None,
    archive: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Development read of a totals candidate against the served v6 total (LOSO 2023-2026, espn_archive)."""
    load_addendum_v3()
    a = archive if archive is not None else grader.load_archive(grader.DEV_SEASONS, shuffle_seed=shuffle_seed)
    if a.attrs.get("line_source", {}).get("name") != grader.DEV_LINE_SOURCE:
        raise grader.SubmissionError(
            f"development archive must come from load_archive({grader.DEV_LINE_SOURCE})"
        )
    pred = grader.load_predictions(predictions)
    if pred["mu_total"].isna().all():
        raise grader.SubmissionError("a totals candidate must submit mu_total")
    grader.validate_echo(pred, a)
    seasons = sorted(set(pred["season"]) & set(grader.DEV_SEASONS)) or list(grader.DEV_SEASONS)
    info = validate_meta_v3(meta, seasons)
    read_count = None
    if shuffle_seed is None:  # logged BEFORE any statistic exists
        read_count = append_read_log(
            {
                "grid_name": meta["grid_name"],
                "candidate": meta["candidate"],
                "window": "development",
                "line_source": a.attrs["line_source"]["name"],
                "predictions_sha256": frame_sha256(pred),
            }
        )
    t = totals_cohort(a)
    before = t.groupby("season").size()
    sub = pred[pred["mu_total"].notna()][["event_id", "mu_total", "scale_total", "dist_total"]]
    sub = sub.rename(columns={"mu_total": "cand_mu_total", "scale_total": "cand_scale_total"})
    t = t.merge(sub, on="event_id", how="inner")
    after = t.groupby("season").size()
    coverage = {int(s): round(float(after.get(s, 0) / before[s]), 5) for s in before.index}
    line = t["total_line"].to_numpy(float)
    t["v6_p_over"] = p_over(t["v6_mu_total"].to_numpy(float), t["v6_scale_total"].to_numpy(float), line)
    t["cand_p_over"] = p_over(
        t["cand_mu_total"].to_numpy(float),
        t["cand_scale_total"].to_numpy(float),
        line,
        t["dist_total"].to_numpy(),
    )
    t["line_mu_total"] = t["total_line"]
    blocks = {
        "cand": totals_block(t, "cand_mu_total", "cand_p_over", K_TOTAL),
        "v6_served": totals_block(t, "v6_mu_total", "v6_p_over", K_TOTAL),
        "closing_total": totals_block(t, "line_mu_total", None, K_TOTAL),
    }
    paired = paired_vs_v6(t)
    flags = leak_flags_totals(blocks["cand"], blocks["closing_total"]["total_by_season"])
    cov_ok = bool(coverage) and min(coverage.values()) >= COVERAGE_MIN
    card: dict[str, Any] = {
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "addendum_v3_sha256": ADDENDUM_V3_SHA256,
        "window": "development",
        "seasons": list(grader.DEV_SEASONS),
        "shuffled_outcomes": shuffle_seed is not None,
        "line_source": a.attrs["line_source"],
        "read_count_for_grid_name": read_count,
        "k_total": K_TOTAL,
        "cohort": {"n_scored": len(t), "n_by_season": {int(s): int(n) for s, n in after.items()}},
        "candidate": {
            "label": meta["candidate"],
            "grid_name": meta["grid_name"],
            "code_sha256": meta["code_sha256"],
            "fit_seasons_by_predicted_season": info["fit_seasons"],
            "coverage_by_season": coverage,
            "leak_flags": flags,
        },
        "models": blocks,
        "paired_vs_v6": paired,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    card["verdict"] = verdict_v3(info["family"], info["registered"], paired, cov_ok, flags)
    if info["registered"] and read_count is not None and read_count > 1:
        card["verdict"] = {
            "status": "REREAD_NO_VERDICT",
            "reason": f"read {read_count} of grid name {meta['grid_name']}: only the first file carries a verdict",
            "would_have_been": card["verdict"],
        }
    card["edge_dev_total"] = blocks["cand"]["rule1_total"]["edge_eligible"]
    return card


# --------------------------------------------------------------------------------------------- reproduction
REPRO_KEYS = ("total_all", "total_by_games_played", "market_total", "rule1_total", "over")


def _close(a: Any, b: Any, tol: float = 1e-9) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_close(a[k], b[k], tol) for k in a)
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b, strict=True))
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=tol)
    return a == b


def _jsonable(o: Any) -> Any:
    return json.loads(json.dumps(o, default=lambda v: v.item() if hasattr(v, "item") else str(v)))


def reproduction_gate(archive: pd.DataFrame | None = None, *, log: bool = True) -> dict[str, Any]:
    """grader.model_block (v1 K = 20) on the baselines must equal the baseline card's totals blocks."""
    load_addendum_v3()
    proto = grader.load_protocol()
    a = archive if archive is not None else grader.load_archive(grader.DEV_SEASONS)
    if log:
        append_read_log(
            {
                "grid_name": "BASELINE_TOTALS_REPRO_V3",
                "candidate": "served v6 total + closing total (baseline card reproduction, no new K)",
                "window": "development",
                "line_source": a.attrs["line_source"]["name"],
                "predictions_sha256": "baselines",
            }
        )
    base = grader.baseline_predictions(a)
    cohort = a[a["d1"] & a["line"].notna()].copy()
    cols = ["mu_margin", "scale_margin", "dist", "p_home_ml", "mu_total", "scale_total", "dist_total"]
    df = cohort[["event_id", "season", "game_date", "margin", "total", "line", "total_line", "sample"]].copy()
    for name, m in base.items():
        df = df.merge(m[["event_id", *cols]].rename(columns={c: f"{name}_{c}" for c in cols}), on="event_id")
    df["games_played_min"] = df["sample"].astype(float)
    sigma = grader.fold_sigma(df)
    for name in ["closing_line", "v7_served", "v6_asof"]:
        df = grader._model_columns(df, name, sigma)
    card = json.loads(BASELINE_CARD.read_text(encoding="utf-8"))
    got_v6 = _jsonable(grader.model_block(df, "v6_asof", proto)["totals"])
    got_line = _jsonable(grader.model_block(df, "closing_line", proto)["totals"])
    want_v6, want_line = card["models"]["v6_asof"]["totals"], card["models"]["closing_line"]["totals"]
    checks = {key: _close(got_v6[key], want_v6[key]) for key in REPRO_KEYS}
    checks["closing_total_all"] = _close(got_line["total_all"], want_line["total_all"])
    checks["k_is_v1_20"] = got_v6["rule1_total"]["k"] == K_V1_TOTAL
    out = {
        "passed": all(checks.values()),
        "checks": checks,
        "got": {
            "n": got_v6["total_all"]["n"],
            "rmse": got_v6["total_all"]["rmse"],
            "bias": got_v6["total_all"]["bias"],
            "slope": got_v6["market_total"]["slope"],
            "slope_ci95": got_v6["market_total"]["slope_ci95"],
            "corr": got_v6["market_total"]["corr"],
            "rule1_mse_delta": got_v6["rule1_total"]["mse_delta"],
            "rule1_mse_delta_ci_adj_k20": got_v6["rule1_total"]["mse_delta_ci_adj"],
            "over_brier": got_v6["over"]["brier"],
            "over_logloss": got_v6["over"]["logloss"],
            "closing_rmse": got_line["total_all"]["rmse"],
            "closing_bias": got_line["total_all"]["bias"],
        },
    }
    if not out["passed"]:
        raise RuntimeError(f"totals reproduction gate failed: {out}")
    return out


# --------------------------------------------------------------------------------------------- T0
def _asof_by_season(t: pd.DataFrame, col: str) -> np.ndarray:
    """Per row: mean of ``col`` over the same season's cohort rows with an EARLIER game_date (NaN when fewer
    than ASOF_MIN_PRIOR such rows)."""
    out = np.full(len(t), np.nan)
    for _, g in t.groupby("season"):
        day = g.groupby("game_date")[col].agg(["sum", "count"]).sort_index()
        cs, cn = day["sum"].cumsum().shift(1, fill_value=0.0), day["count"].cumsum().shift(1, fill_value=0)
        mean = (cs / cn.where(cn >= ASOF_MIN_PRIOR)).to_dict()
        out[g.index.to_numpy()] = g["game_date"].map(mean).to_numpy(float)
    return out


def _dist(v: np.ndarray) -> dict[str, Any]:
    v = v[~np.isnan(v)]
    if not len(v):
        return {"n": 0}
    q = np.percentile(v, [10, 50, 90])
    return {
        "n": len(v),
        "mean": round(float(v.mean()), 3),
        "p10": round(q[0], 3),
        "p50": round(q[1], 3),
        "p90": round(q[2], 3),
    }


def t0_diagnostics(archive: pd.DataFrame | None = None, *, shuffle_seed: int | None = None) -> dict[str, Any]:
    load_addendum_v3()
    a = archive if archive is not None else grader.load_archive(grader.DEV_SEASONS, shuffle_seed=shuffle_seed)
    t = totals_cohort(a)
    if shuffle_seed is None:
        append_read_log(
            {
                "grid_name": "T0_total_diagnostics",
                "candidate": "T0 diagnostics: as-of level offset, regulation vs overtime bias, v6 lean",
                "window": "development",
                "line_source": a.attrs["line_source"]["name"],
                "predictions_sha256": "baselines+cbbd_periods",
            }
        )
    t["d_line"] = t["v6_mu_total"] - t["total_line"]
    t["r_v6"] = t["total"] - t["v6_mu_total"]
    t["r_line"] = t["total"] - t["total_line"]
    for col in ("d_line", "r_v6", "r_line"):
        t[f"asof_{col}"] = _asof_by_season(t, col)
    t["month"] = t["game_date"].str[:7]
    t0a: dict[str, Any] = {"definition": "as-of means over the same season's earlier-dated cohort games"}
    for col, label in (
        ("asof_d_line", "off_line_v6_minus_close"),
        ("asof_r_v6", "bias_v6_actual_minus_v6"),
        ("asof_r_line", "bias_line_actual_minus_close"),
    ):
        t0a[label] = {
            "all": _dist(t[col].to_numpy(float)),
            "by_season": {int(s): _dist(g[col].to_numpy(float)) for s, g in t.groupby("season")},
            "by_month": {m: _dist(g[col].to_numpy(float)) for m, g in t.groupby("month")},
        }
    per = load_periods(t["event_id"])
    t["n_ot"] = t["event_id"].map(lambda e: per.get(e, {}).get("n_ot", np.nan))
    t["reg_total"] = t["event_id"].map(lambda e: per.get(e, {}).get("reg_total", np.nan))
    ptot = t["event_id"].map(
        lambda e: (
            per.get(e, {}).get("home_points", np.nan) + per.get(e, {}).get("away_points", np.nan)
            if e in per
            else np.nan
        )
    )
    known = t["n_ot"].notna() & (ptot == t["total"])
    t0b: dict[str, Any] = {
        "n_cohort": len(t),
        "n_periods_known": int(known.sum()),
        "n_periods_unknown_or_score_mismatch": int((~known).sum()),
    }
    k = t[known]
    for name, m in (("regulation", k["n_ot"] == 0), ("overtime", k["n_ot"] > 0)):
        g = k[m]
        t0b[name] = {
            "n": len(g),
            "share": round(len(g) / max(len(k), 1), 5),
            "mean_actual_minus_close": round(float(g["r_line"].mean()), 4) if len(g) else None,
            "mean_actual_minus_v6": round(float(g["r_v6"].mean()), 4) if len(g) else None,
        }
    t0b["overtime_periods_mean_given_ot"] = round(float(k.loc[k["n_ot"] > 0, "n_ot"].mean()), 4)
    t0b["mean_regulation_total_minus_close_all_known"] = round(
        float((k["reg_total"] - k["total_line"]).mean()), 4
    )
    t0b["mean_overtime_points_per_game_all_known"] = round(float((k["total"] - k["reg_total"]).mean()), 4)
    t0b["by_season"] = {
        int(s): {
            "n": len(g),
            "ot_share": round(float((g["n_ot"] > 0).mean()), 5),
            "mean_actual_minus_close": round(float(g["r_line"].mean()), 4),
            "mean_regulation_minus_close": round(float((g["reg_total"] - g["total_line"]).mean()), 4),
        }
        for s, g in k.groupby("season")
    }
    line = t["total_line"].to_numpy(float)
    pv = p_over(t["v6_mu_total"].to_numpy(float), t["v6_scale_total"].to_numpy(float), line)
    yt = t["r_line"].to_numpy(float)
    np_mask = yt != 0
    over = yt > 0
    lean_over = t["d_line"].to_numpy(float) > 0
    t0c = {
        "share_v6_above_close": round(float(lean_over.mean()), 5),
        "share_v6_equal_close": round(float((t["d_line"] == 0).mean()), 5),
        "mean_v6_minus_close": round(float(t["d_line"].mean()), 4),
        "mean_v6_p_over": round(float(pv.mean()), 5),
        "share_v6_p_over_gt_half": round(float((pv > 0.5).mean()), 5),
        "realized_over_rate_pushes_dropped": round(float(over[np_mask].mean()), 5),
        "n_pushes": int((~np_mask).sum()),
        "realized_over_rate_when_v6_above_close": round(float(over[np_mask & lean_over].mean()), 5),
        "realized_over_rate_when_v6_below_close": round(
            float(over[np_mask & (t["d_line"].to_numpy(float) < 0)].mean()), 5
        ),
        "by_season": {
            int(s): {
                "share_v6_above_close": round(float((g["d_line"] > 0).mean()), 5),
                "mean_v6_minus_close": round(float(g["d_line"].mean()), 4),
            }
            for s, g in t.groupby("season")
        },
    }
    return {
        "grid_name": "T0_total_diagnostics",
        "addendum_v3_sha256": ADDENDUM_V3_SHA256,
        "shuffled_outcomes": shuffle_seed is not None,
        "cohort_n": len(t),
        "T0a_level_offset_asof": t0a,
        "T0b_bias_regulation_vs_overtime": t0b,
        "T0c_v6_lean": t0c,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# --------------------------------------------------------------------------------------------- S0
def load_opens(event_ids: Iterable[str]) -> dict[str, dict[str, float | None]]:
    """Opening lines for the given (already lock-filtered) ids only; locked rows are never parsed further."""
    want = {str(e) for e in event_ids}
    out: dict[str, dict[str, float | None]] = {}
    for rec in grader._iter_jsonl(nr.LINES):
        eid = str(rec.get("event_id"))
        if eid not in want:
            continue
        sel = rec.get("selected") or {}
        out[eid] = {
            "open_home_spread_line": sel.get("open_home_spread_line"),
            "open_total_line": sel.get("open_total_line"),
        }
    return out


def _market_k(x: np.ndarray, y: np.ndarray, days: list[str], k: int) -> dict[str, Any]:
    mk = grader.market_plain(x, y, days)

    def slope(s: dict[str, np.ndarray]) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return (s["xy"] - s["x"] * s["y"] / s["n"]) / (s["xx"] - s["x"] ** 2 / s["n"])

    _, bs = grader._boot(days, grader._sums(x, y), slope)
    _, rs = grader._boot(days, grader._sums(x, y), grader._corr_from_sums)
    mk["slope_ci_adj"] = grader._ci(bs, k)["ci_adj"]
    mk["corr_ci_adj"] = grader._ci(rs, k)["ci_adj"]
    mk["k"] = k
    return mk


def s0_diagnostic(archive: pd.DataFrame | None = None, *, shuffle_seed: int | None = None) -> dict[str, Any]:
    load_addendum_v3()
    a = archive if archive is not None else grader.load_archive(S0_SEASONS, shuffle_seed=shuffle_seed)
    a = a[a["season"].isin(S0_SEASONS)]
    if shuffle_seed is None:
        append_read_log(
            {
                "grid_name": "S0_open_line_diagnostic",
                "candidate": "S0: served v7 spread and served v6 total vs the opening line",
                "window": "development",
                "line_source": a.attrs["line_source"]["name"],
                "predictions_sha256": "baselines+opens",
            }
        )
    opens = load_opens(a["event_id"])
    base = grader.baseline_predictions(a)
    d1 = a[a["d1"]].copy()
    d1["open_margin"] = d1["event_id"].map(
        lambda e: grader.implied_home_margin(opens.get(e, {}).get("open_home_spread_line"))
    )
    d1["open_total"] = pd.to_numeric(d1["event_id"].map(lambda e: opens.get(e, {}).get("open_total_line")))
    v7 = base["v7_served"].set_index("event_id")
    d1["v7"] = d1["event_id"].map(v7["mu_margin"])
    d1["v6_total"] = d1["event_id"].map(base["v6_asof"].set_index("event_id")["mu_total"])
    out: dict[str, Any] = {
        "grid_name": "S0_open_line_diagnostic",
        "addendum_v3_sha256": ADDENDUM_V3_SHA256,
        "shuffled_outcomes": shuffle_seed is not None,
        "seasons": list(S0_SEASONS),
        "pit_assumption": "open posted no later than the morning of the game day; the model cut (Eastern midnight "
        "of the game day) may include previous-evening results the open did not see, which biases "
        "corr(model - open, close - open) upward",
    }
    for kind, actual, open_col, close_col, model_col, k in (
        ("spread", "margin", "open_margin", "line", "v7", K_SPREAD),
        ("total", "total", "open_total", "total_line", "v6_total", K_TOTAL),
    ):
        g = d1[d1[open_col].notna() & d1[close_col].notna() & d1[model_col].notna()]
        days = g["game_date"].tolist()
        x = (g[model_col] - g[open_col]).to_numpy(float)
        out[kind] = {
            "n": len(g),
            "n_by_season": {int(s): int(n) for s, n in g.groupby("season").size().items()},
            "model_minus_open_vs_actual_minus_open": _market_k(
                x, (g[actual] - g[open_col]).to_numpy(float), days, k
            ),
            "model_minus_open_vs_close_minus_open": _market_k(
                x, (g[close_col] - g[open_col]).to_numpy(float), days, k
            ),
            "corr_by_season_close_move": {
                int(s): round(
                    float(np.corrcoef(h[model_col] - h[open_col], h[close_col] - h[open_col])[0, 1]), 5
                )
                for s, h in g.groupby("season")
            },
            "rmse_vs_actual": {
                "model": grader._err(g[actual].to_numpy(float), g[model_col].to_numpy(float)),
                "open": grader._err(g[actual].to_numpy(float), g[open_col].to_numpy(float)),
                "close": grader._err(g[actual].to_numpy(float), g[close_col].to_numpy(float)),
            },
            "mean_close_minus_open": round(float((g[close_col] - g[open_col]).mean()), 4),
            "share_line_moved": round(float((g[close_col] != g[open_col]).mean()), 5),
        }
    out["generated_at_utc"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


# --------------------------------------------------------------------------------------------- rendering
def _f(v: Any, nd: int = 4) -> str:
    return grader._f(v, nd)


def render_markdown(card: dict[str, Any]) -> str:
    c, p = card["candidate"], card["paired_vs_v6"]
    lines = [
        f"# {c['grid_name']} (addendum v3, development, totals)",
        "",
        f"- candidate: {c['label']}",
        f"- addendum v3 sha256 `{card['addendum_v3_sha256']}`; K (totals) = {card['k_total']}",
        f"- read count for grid name: {card['read_count_for_grid_name']}; shuffled: {card['shuffled_outcomes']}",
        f"- coverage by season: {c['coverage_by_season']}; leak flags: {c['leak_flags'] or 'none'}",
        f"- **verdict: {card['verdict']['status']}** {json.dumps(card['verdict'].get('checks', ''))}",
        "",
        "| model | RMSE / bias (n) | slope [95%] | corr [95%] {K-adj} | rule-1 MSE delta {K-adj} | over Brier / LL |",
        "|---|---|---|---|---|---|",
    ]
    for name, b in card["models"].items():
        ta = b["total_all"]
        if "market_total" in b:
            m, r, o = b["market_total"], b["rule1_total"], b["over"]
            lines.append(
                f"| {name} | {_f(ta['rmse'], 2)} / {_f(ta['bias'], 2)} ({ta['n']}) | {_f(m['slope'], 3)} "
                f"{grader._ci_txt(m['slope_ci95'])} | {_f(m['corr'])} {grader._ci_txt(m['corr_ci95'])} "
                f"{{{grader._ci_txt(m['corr_ci_adj'])}}} | {_f(r['mse_delta'])} {{{grader._ci_txt(r['mse_delta_ci_adj'])}}}"
                f" | {_f(o['brier'])} / {_f(o['logloss'])} |"
            )
        else:
            lines.append(
                f"| {name} | {_f(ta['rmse'], 2)} / {_f(ta['bias'], 2)} ({ta['n']}) | N/A | N/A | N/A | N/A |"
            )
    lines += ["", "## Paired vs served v6 total (identical games)", ""]
    for key, v in p.items():
        lines.append(f"- {key}: {json.dumps(v)}")
    lines += ["", "## RMSE / bias by season", ""]
    for name, b in card["models"].items():
        lines.append(f"- {name}: {json.dumps(b['total_by_season'])}")
    lines += ["", "## RMSE / bias by games played", ""]
    for name, b in card["models"].items():
        lines.append(f"- {name}: {json.dumps(b['total_by_games_played'])}")
    return "\n".join(lines) + "\n"


def write_json(obj: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(obj), indent=1, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("repro")
    for name in ("t0", "s0"):
        sp = sub.add_parser(name)
        sp.add_argument("--shuffle-seed", type=int, default=None)
    sc = sub.add_parser("score")
    sc.add_argument("--pred", required=True)
    sc.add_argument("--meta", required=True)
    sc.add_argument("--shuffle-seed", type=int, default=None)
    sc.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "repro":
        res = reproduction_gate()
        write_json(res, OUT_V3 / "reproduction_gate_totals.json")
        print(json.dumps(res["got"], indent=1), "\npassed", res["passed"])
        return 0
    if args.cmd in ("t0", "s0"):
        fn = t0_diagnostics if args.cmd == "t0" else s0_diagnostic
        res = fn(shuffle_seed=args.shuffle_seed)
        suffix = f"_shuffled_{args.shuffle_seed}" if args.shuffle_seed is not None else ""
        write_json(res, OUT_V3 / f"{res['grid_name']}{suffix}.json")
        print(json.dumps(_jsonable(res), indent=1)[:6000])
        return 0
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    card = score_totals(args.pred, meta, shuffle_seed=args.shuffle_seed)
    out = Path(args.out_dir) if args.out_dir else Path(args.pred).parent
    stem = meta["grid_name"] + (f"_shuffled_{args.shuffle_seed}" if args.shuffle_seed is not None else "")
    write_json(card, out / f"{stem}.json")
    (out / f"{stem}.md").write_text(render_markdown(card), encoding="utf-8")
    print(render_markdown(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
