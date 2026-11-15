"""NCAAB v7 multi-season re-fit and evaluation, SHADOW ONLY (quick 261115-m5r).

Reads the archives written by quick 261115-1m8 (data/research/ncaab_market/, untracked), calls v6 read-only,
and writes only outputs/ncaab/ncaab_v7_multiseason.{md,json} plus untracked caches. No ledger writes (the
runtime ledger is opened mode=ro), no config or promotion change, v7 stays unregistered.

Stages (repo root; ``OPENBLAS_NUM_THREADS=1 PYTHONPATH=src:scripts .venv/Scripts/python scripts/ncaab_v7_multiseason.py STAGE``):
    components  outcome-free as-of-map replay of v6 and the ridge grid for every game day (cache)
    gate        today's-map v6 must equal the existing PIT projections exactly (no outcome read)
    fit         reads outcomes of the FIT seasons only; selects, fits and freezes the config
    final       the single read of 2024-25, the contaminated line-free windows, the ledger checks, the report

The pinned rule lives in ncaab_v7_multiseason_pin.py and was committed before any outcome was joined.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import ncaab_residual as nr
from ncaab_v7_multiseason_pin import PIN, PIN_SHA256

from model_prediction.models.ncaab_v7_shadow import student_t_scale_mle

DATA = REPO / "data" / "research" / "ncaab_market"
OUT = REPO / "outputs" / "ncaab"
COMPONENTS = DATA / "v7ms_components.jsonl"
FROZEN = OUT / "ncaab_v7_multiseason_frozen.json"
FIT_SEASONS = tuple(PIN["windows_by_espn_season_year"]["fit"])
DECISION_SEASONS = tuple(PIN["windows_by_espn_season_year"]["decision"])
STAGE_MAX_SEASON = {"components": 0, "gate": 0, "fit": max(FIT_SEASONS), "final": 2027}


def load_outcomes(stage: str) -> dict[str, dict[str, Any]]:
    """event id -> scores, restricted to the seasons the stage is allowed to read (structural guard)."""
    cap = STAGE_MAX_SEASON[stage]
    if cap == 0:
        raise RuntimeError(f"stage {stage!r} may not read outcomes")
    out: dict[str, dict[str, Any]] = {}
    for e in nr.load_scoreboard_events():
        if int(e["season_year"]) < 2023 or int(e["season_year"]) > cap:
            continue
        out[e["event_id"]] = {
            "margin": int(e["home_score"]) - int(e["away_score"]),
            "total": int(e["home_score"]) + int(e["away_score"]),
        }
    return out


# ----------------------------------------------------------------------------------------- components
def _worker(days: list[str]) -> list[dict[str, Any]]:
    from model_prediction.domain import EASTERN
    from model_prediction.models.basketball import UpcomingGame
    from model_prediction.models.ncaab_v7_multiseason import NCAABV7MultiSeasonModel, build_asof_conferences

    events = nr.load_scoreboard_events()
    games = nr.to_game_records(events)
    order = sorted(range(len(games)), key=lambda i: games[i].start)
    games = [games[i] for i in order]
    starts = [g.start for g in games]
    neutral = {e["event_id"]: bool(e["neutral"]) for e in events}
    by_day: dict[str, list[Any]] = defaultdict(list)
    for g in games:
        by_day[g.start.astimezone(EASTERN).date().isoformat()].append(g)
    asof = os.environ.get("V7MS_TODAY_MAP") != "1"
    model = NCAABV7MultiSeasonModel(build_asof_conferences(events), stats_root=nr.STATS_ROOT, asof_map=asof)
    rows: list[dict[str, Any]] = []
    for day in days:
        cutoff = datetime.combine(date.fromisoformat(day), dtime.min, tzinfo=EASTERN)
        hist = games[: bisect.bisect_left(starts, cutoff)]
        todays = [g for g in by_day.get(day, []) if int(g.season_year or 0) >= 2023]
        if len(hist) < 20 or not todays:
            continue
        up = [
            UpcomingGame(
                g.event_id,
                g.event_start_utc,
                g.away_team,
                g.home_team,
                league="NCAAB",
                neutral_site=neutral[g.event_id],
            )
            for g in todays
        ]
        by_id = {g.event_id: g for g in todays}
        for row in model.components(hist, up):
            g = by_id[row["event_id"]]
            rows.append(
                {**row, "day": day, "season_year": int(g.season_year or 0), "start": g.event_start_utc}
            )
    return rows


def cmd_components(args: argparse.Namespace) -> int:
    events = nr.load_scoreboard_events()
    days = sorted({nr.et_day(e["start_utc"]) for e in events if e["season_year"] >= 2023})
    if args.limit_days:
        days = days[: args.limit_days]
    if args.stride > 1:
        days = days[:: args.stride]
    # interleaved chunks balance the cost (late-season days carry the longest history)
    chunks = [days[i :: args.workers] for i in range(args.workers)]
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as ex:
        parts = list(ex.map(_worker, chunks))
    rows = sorted((r for p in parts for r in p), key=lambda r: (r["start"], r["event_id"]))
    path = Path(args.out) if args.out else COMPONENTS
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"components {len(rows)} rows, {len(days)} days, {time.time() - t0:.0f}s -> {path}", flush=True)
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """With today's map the replay's v6 margin must equal the existing PIT projections (no outcome read)."""
    proj = {r["event_id"]: r for r in nr._read_jsonl(nr.PROJECTIONS)}
    path = Path(args.components)
    comps = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    diffs = [abs(c["v6_mean"] - proj[c["event_id"]]["sp_mean"]) for c in comps if c["event_id"] in proj]
    tdiffs = [
        abs(c["v6_total"] - proj[c["event_id"]]["tp_mean"])
        for c in comps
        if c["event_id"] in proj and "v6_total" in c and "tp_mean" in proj[c["event_id"]]
    ]
    res = {
        "rows_compared": len(diffs),
        "spread_max_abs_diff": max(diffs) if diffs else None,
        "total_max_abs_diff": max(tdiffs) if tdiffs else None,
    }
    print("gate (today's map vs v6_projections.jsonl)", res)
    return 0 if diffs and max(diffs) < 0.011 and (not tdiffs or max(tdiffs) < 0.011) else 1


# ----------------------------------------------------------------------------------------- model math
BUCKET_CUTS = (1, 3, 8)  # sample buckets 0 | 1-2 | 3-7 | 8+
N_BUCKETS = 4
TAIL_KNOT = 12.0
FORMS = ("F1", "F2", "F3")
FORM_MARGIN = 0.01  # a more complex form must beat the simpler one by more than this RMSE
TOTAL_MARGIN = 0.05
DF_T = 8.0


def sbucket(sample: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.array(BUCKET_CUTS), np.asarray(sample), side="right")


def tail_term(v6: np.ndarray) -> np.ndarray:
    return np.sign(v6) * np.maximum(np.abs(v6) - TAIL_KNOT, 0.0)


def form_design(form: str, ridge: np.ndarray, v6: np.ndarray, sample: np.ndarray) -> np.ndarray:
    onehot = np.eye(N_BUCKETS)[sbucket(sample)]
    t = tail_term(v6)
    if form == "F1":
        return np.column_stack([ridge, t])
    if form == "F2":
        return np.column_stack([onehot * ridge[:, None], t])
    if form == "F3":
        return np.column_stack([onehot * ridge[:, None], t, onehot])
    raise KeyError(form)


def ols(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.solve(x.T @ x + 1e-6 * np.eye(x.shape[1]), x.T @ y)


def is_d1(df: pd.DataFrame) -> pd.Series:
    return df["d1_home"].astype(bool) & df["d1_away"].astype(bool)


def fit_config(train: pd.DataFrame, variant: str, form: str, total_mode: str) -> dict[str, Any]:
    """Fit every v7 coefficient and scale on ``train`` rows (which carry margin / total)."""
    d1 = train[is_d1(train) & train[variant].notna()]
    x = form_design(form, d1[variant].to_numpy(float), d1["v6_mean"].to_numpy(float), d1["sample"].to_numpy())
    coef = ols(x, d1["margin"].to_numpy(float))
    resid = d1["margin"].to_numpy(float) - x @ coef
    b = sbucket(d1["sample"].to_numpy())
    scales = [student_t_scale_mle(resid[b == k], DF_T) for k in range(N_BUCKETS)]
    nd = train[~is_d1(train)]
    xn = np.column_stack([nd["v6_mean"].to_numpy(float), np.ones(len(nd))])
    cn = ols(xn, nd["margin"].to_numpy(float))
    rn = nd["margin"].to_numpy(float) - xn @ cn
    cfg: dict[str, Any] = {
        "variant": variant,
        "form": form,
        "coef": coef.tolist(),
        "scales": scales,
        "nond1": {
            "alpha": float(cn[0]),
            "beta": float(cn[1]),
            "scale": student_t_scale_mle(rn, DF_T),
            "n": len(nd),
        },
        "n_d1": len(d1),
        "total": {"mode": total_mode},
    }
    tr = train[train["v6_total"].notna()]
    bt = sbucket(tr["sample"].to_numpy())
    alpha, beta, tscales = [], [], []
    for k in range(N_BUCKETS):
        sel = bt == k
        v, y = tr["v6_total"].to_numpy(float)[sel], tr["total"].to_numpy(float)[sel]
        if total_mode == "recal" and sel.sum() >= 30:
            c = ols(np.column_stack([v, np.ones(sel.sum())]), y)
        else:
            c = np.array([1.0, 0.0])
        alpha.append(float(c[1]))
        beta.append(float(c[0]))
        tscales.append(student_t_scale_mle(y - (c[0] * v + c[1]), DF_T))
    cfg["total"].update({"alpha": alpha, "beta": beta, "scales": tscales})
    return cfg


def apply_config(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """v7 mean, t-scale, total mean and total scale for every row (non-D1 rows use the linear rule)."""
    mean = np.full(len(df), np.nan)
    scale = np.full(len(df), np.nan)
    d1 = is_d1(df).to_numpy() & df[cfg["variant"]].notna().to_numpy()
    if d1.any():
        sub = df[d1]
        x = form_design(
            cfg["form"],
            sub[cfg["variant"]].to_numpy(float),
            sub["v6_mean"].to_numpy(float),
            sub["sample"].to_numpy(),
        )
        mean[d1] = x @ np.array(cfg["coef"])
        scale[d1] = np.array(cfg["scales"])[sbucket(sub["sample"].to_numpy())]
    nd = ~is_d1(df).to_numpy()
    if nd.any():
        mean[nd] = cfg["nond1"]["alpha"] * df["v6_mean"].to_numpy(float)[nd] + cfg["nond1"]["beta"]
        scale[nd] = cfg["nond1"]["scale"]
    tot = np.full(len(df), np.nan)
    tscale = np.full(len(df), np.nan)
    has = df["v6_total"].notna().to_numpy()
    bt = sbucket(df["sample"].to_numpy())
    a, b, ts = (np.array(cfg["total"][k]) for k in ("alpha", "beta", "scales"))
    tot[has] = a[bt[has]] + b[bt[has]] * df["v6_total"].to_numpy(float)[has]
    tscale[has] = ts[bt[has]]
    return pd.DataFrame(
        {"v7_mean": mean, "v7_scale": scale, "v7_total": tot, "v7_total_scale": tscale}, index=df.index
    )


def load_components(max_season_year: int) -> pd.DataFrame:
    df = pd.read_json(COMPONENTS, lines=True, dtype={"event_id": str})
    df["event_id"] = df["event_id"].astype(str)
    return df[df["season_year"] <= max_season_year].copy()


def ridge_keys(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("rg_c")]


def rmse(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(a)))) if len(a) else float("nan")


# ----------------------------------------------------------------------------------------- fit stage
def with_outcomes(df: pd.DataFrame, outcomes: dict[str, dict[str, Any]]) -> pd.DataFrame:
    df = df[df["event_id"].isin(outcomes)].copy()
    df["margin"] = df["event_id"].map(lambda e: outcomes[e]["margin"])
    df["total"] = df["event_id"].map(lambda e: outcomes[e]["total"])
    return df


def cmd_fit(_args: argparse.Namespace) -> int:
    """Select the ridge variant and combiner form by leave-one-season-out, then freeze everything."""
    outcomes = load_outcomes("fit")
    df = with_outcomes(load_components(max(FIT_SEASONS)), outcomes)
    assert set(df["season_year"]) <= set(FIT_SEASONS), "fit stage read a non-fit season"
    d1 = df[is_d1(df)]
    # 1. ridge variant: raw ridge RMSE per fit season (no fitted parameter)
    table: dict[str, Any] = {}
    for key in ridge_keys(df):
        sub = d1[d1[key].notna()]
        per = {int(s): rmse((g["margin"] - g[key]).to_numpy(float)) for s, g in sub.groupby("season_year")}
        table[key] = {"rmse_by_season": per, "score": float(np.mean(list(per.values())))}
    variant = min(table, key=lambda k: (table[k]["score"], k))
    # 2. combiner form by leave-one-season-out
    form_scores: dict[str, float] = {}
    for form in FORMS:
        errs = []
        for hold in FIT_SEASONS:
            cfg = fit_config(df[df["season_year"] != hold], variant, form, "identity")
            test = df[(df["season_year"] == hold) & is_d1(df)]
            pred = apply_config(test, cfg)["v7_mean"].to_numpy(float)
            errs.append(rmse(test["margin"].to_numpy(float) - pred))
        form_scores[form] = float(np.mean(errs))
    form = FORMS[0]
    for cand in FORMS[1:]:
        if form_scores[form] - form_scores[cand] > FORM_MARGIN:
            form = cand
    # 3. totals: recalibration only when it beats v6 as-of by more than TOTAL_MARGIN (leave-one-season-out)
    base_err, recal_err = [], []
    for hold in FIT_SEASONS:
        test = df[(df["season_year"] == hold) & df["v6_total"].notna()]
        cfg = fit_config(df[df["season_year"] != hold], variant, form, "recal")
        base_err.append(rmse(test["total"].to_numpy(float) - test["v6_total"].to_numpy(float)))
        recal_err.append(
            rmse(test["total"].to_numpy(float) - apply_config(test, cfg)["v7_total"].to_numpy(float))
        )
    total_mode = "recal" if np.mean(base_err) - np.mean(recal_err) > TOTAL_MARGIN else "identity"
    cfg_final = fit_config(df, variant, form, total_mode)
    # leave-one-season-out predictions for the fit rows (the market rule's fit window)
    loso_parts = []
    for hold in FIT_SEASONS:
        cfg_h = fit_config(df[df["season_year"] != hold], variant, form, total_mode)
        test = df[df["season_year"] == hold]
        pred = apply_config(test, cfg_h)
        pred["event_id"] = test["event_id"].to_numpy()
        loso_parts.append(pred)
    pd.concat(loso_parts).to_json(DATA / "v7ms_loso_fit_preds.jsonl", orient="records", lines=True)
    selection = {
        "ridge_variant_table": table,
        "ridge_variant_selected": variant,
        "form_loso_rmse": form_scores,
        "form_selected": form,
        "total_loso_rmse_v6_asof": float(np.mean(base_err)),
        "total_loso_rmse_recal": float(np.mean(recal_err)),
        "total_mode": total_mode,
    }
    frozen = {
        "pin_id": PIN["id"],
        "pin_sha256": PIN_SHA256,
        "components_sha256": nr.sha256_file(COMPONENTS),
        "fit_seasons": list(FIT_SEASONS),
        "fit_rows": len(df),
        "config": cfg_final,
        "selection": selection,
    }
    FROZEN.write_text(json.dumps(frozen, indent=2, sort_keys=True), encoding="utf-8")
    best5 = sorted(table, key=lambda k: table[k]["score"])[:5]
    print("top ridge variants", [(k, round(table[k]["score"], 4)) for k in best5])
    print(
        "selected", variant, "form", form, "form loso rmse", {k: round(v, 4) for k, v in form_scores.items()}
    )
    print(
        "coef", [round(c, 4) for c in cfg_final["coef"]], "scales", [round(x, 3) for x in cfg_final["scales"]]
    )
    print(
        "total loso rmse v6 as-of / recal",
        round(float(np.mean(base_err)), 4),
        round(float(np.mean(recal_err)), 4),
        total_mode,
    )
    print("wrote", FROZEN)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("components")
    c.add_argument("--workers", type=int, default=6)
    c.add_argument("--limit-days", type=int, default=0)
    c.add_argument("--stride", type=int, default=1)
    c.add_argument("--out", default="")
    g = sub.add_parser("gate")
    g.add_argument("--components", required=True)
    for name in ("fit", "final"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    print(f"pin {PIN['id']} sha256 {PIN_SHA256}", flush=True)
    if args.cmd == "components":
        return cmd_components(args)
    if args.cmd == "gate":
        return cmd_gate(args)
    if args.cmd == "fit":
        return cmd_fit(args)
    raise SystemExit(f"stage {args.cmd} is added in a later commit")


if __name__ == "__main__":
    raise SystemExit(main())
