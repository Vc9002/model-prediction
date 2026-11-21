"""B3_early_scale_floor (protocol 261120): early-season floor on the served v7 t(8) scale.

scale = max(v7 bucket scale, c0 * (1 + c1 * max(0, 1 - s/8))), s = games_played_min (the archive's PIT
current-season sample, the same count v7 buckets on). mu is the SERVED v7 mean, unchanged; "v7 bucket scale" is
the served row scale. c0, c1 by t(8) maximum likelihood on the fold's three training seasons' D1-vs-D1
residuals (margin - served v7 mean). The likelihood is flat wherever the floor never binds, so the fit is a
fixed grid (c0 6..16 by 0.5, c1 0..2 by 0.1) followed by bounded Nelder-Mead (c0 in [1, 40], c1 in [-0.9, 5])
from the three best grid points; the lowest NLL wins (ties: grid order). Moneyline: served 0.5 Elo blend on the
new scale. Same nesting disclosure as B1.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import ab_common_261120 as ab
import numpy as np
import pandas as pd
from scipy import optimize

GRID = "B3_early_scale_floor"
C0_GRID = np.round(np.arange(6.0, 16.0001, 0.5), 6)
C1_GRID = np.round(np.arange(0.0, 2.0001, 0.1), 6)
BOUNDS = [(1.0, 40.0), (-0.9, 5.0)]


def floor_fn(c0: float, c1: float, sample: np.ndarray) -> np.ndarray:
    s = np.asarray(sample, float)
    return c0 * (1.0 + c1 * np.maximum(0.0, 1.0 - s / 8.0))


def scale_fn(v7_scale: np.ndarray, c0: float, c1: float, sample: np.ndarray) -> np.ndarray:
    return np.maximum(np.asarray(v7_scale, float), floor_fn(c0, c1, sample))


def fit(train: pd.DataFrame) -> dict[str, Any]:
    r = (train["margin"] - train["mu_v7"]).to_numpy(float)
    sc7, s = train["scale_v7"].to_numpy(float), train["games_played_min"].to_numpy(float)

    def nll(th: np.ndarray) -> float:
        return ab.t8_nll(r, scale_fn(sc7, float(th[0]), float(th[1]), s))

    base = ab.t8_nll(r, sc7)
    grid = [
        (nll(np.array([c0, c1])), i, c0, c1)
        for i, (c0, c1) in enumerate((a, b) for a in C0_GRID for b in C1_GRID)
    ]
    grid.sort()
    best = (grid[0][0], np.array([grid[0][2], grid[0][3]]))
    for g in grid[:3]:
        res = optimize.minimize(
            nll,
            np.array([g[2], g[3]]),
            method="Nelder-Mead",
            bounds=BOUNDS,
            options={"xatol": 1e-6, "fatol": 1e-9, "maxiter": 4000},
        )
        if res.fun < best[0] - 1e-9:
            best = (float(res.fun), res.x)
    c0, c1 = (float(v) for v in best[1])
    binds = floor_fn(c0, c1, s) > sc7
    return {
        "c0": c0,
        "c1": c1,
        "nll": float(best[0]),
        "nll_v7_scale": float(base),
        "n_train": len(r),
        "floor_binding_rows_train": int(binds.sum()),
        "floor_binding_by_bucket_train": {
            str(k): int(binds[ab.bucket(s) == k].sum()) for k in range(ab.N_BUCKETS)
        },
    }


def predict(test: pd.DataFrame, p: dict[str, Any]) -> pd.DataFrame:
    out = test[ab.ID_COLS + ["games_played_min"]].copy()
    out["mu_margin"] = test["mu_v7"].to_numpy(float)
    out["scale_margin"] = scale_fn(test["scale_v7"], p["c0"], p["c1"], out["games_played_min"])
    out["p_home_ml"] = ab.ml_blend(test["elo"], out["mu_margin"], out["scale_margin"], 0.5)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True, help="grader.py export --model v7_served output")
    args = ap.parse_args(argv)
    frame = ab.load_frame()
    pred, params = ab.loso_apply(frame, fit, predict)
    pred = pred.set_index("event_id").loc[frame["event_id"]].reset_index()
    path = ab.write_submission(
        pred,
        grid_name=GRID,
        candidate="B3: scale = max(v7 bucket scale, c0*(1 + c1*max(0, 1 - s/8))), LOSO t8 MLE",
        code_files=[Path(__file__).resolve(), Path(ab.__file__).resolve()],
        fit_seasons=ab.fit_seasons_loso(),
        with_ml=True,
        extra_meta={
            "fitted_params_by_fold": {str(k): v for k, v in params.items()},
            "disclosure": "served v7 mean on training rows was fit with 2023/2024 data (protocol nesting caveat)",
        },
    )
    rep: dict[str, Any] = ab.compare_to_export(path, Path(args.export), check_scale=True)
    # switched off (c0 = 0): scale = served scale exactly, p = served blend
    exp = pd.read_json(args.export, lines=True, dtype={"event_id": str})
    exp["event_id"] = exp["event_id"].astype(str)
    off = predict(frame.drop(columns=["margin"]), {"c0": 0.0, "c1": 0.0}).merge(
        exp, on="event_id", suffixes=("", "_v7")
    )
    rep["switch_off_max_abs_scale_diff"] = float(np.max(np.abs(off["scale_margin"] - off["scale_margin_v7"])))
    rep["switch_off_max_abs_p_diff"] = float(np.max(np.abs(off["p_home_ml"] - off["p_home_ml_v7"])))
    sub = pd.read_json(path, lines=True, dtype={"event_id": str})
    sub["event_id"] = sub["event_id"].astype(str)
    jj = sub.merge(exp, on="event_id", suffixes=("", "_v7"))
    rep["rows_scale_raised"] = {
        str(s): int((g["scale_margin"] > g["scale_margin_v7"] + 1e-9).sum()) for s, g in jj.groupby("season")
    }
    rep["fitted_params_by_fold"] = {str(k): v for k, v in params.items()}
    (path.parent / "repro_check.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps(rep, indent=2))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
