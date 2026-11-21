"""B1_scale_linear_mu (protocol 261120): t(8) scale that grows with |expected margin|.

scale = a_b + b * |mu|, with a_b per v7 games-played bucket (0 | 1-2 | 3-7 | 8+; the protocol's optional
per-bucket form, chosen before any read because b = 0 then nests v7's own bucket scales) and one common b.
mu is the SERVED v7 mean, unchanged. a_b and b are fit by t(8) maximum likelihood on the fold's three training
seasons' D1-vs-D1 residuals (margin - served v7 mean); bounds a_b in [2, 40], b in [-0.15, 1.0], scale
floored at 1.0 (never binding in practice; recorded per fold). Moneyline: the served 0.5 Elo blend on the new
scale (``grader.served_ml_blend``).

Disclosure: the served v7 mean on training rows was itself fit with 2023/2024 data (protocol nesting caveat),
so the 2023 and 2024 folds' training residuals carry that second-order dependence.
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

GRID = "B1_scale_linear_mu"
A_BOUNDS = (2.0, 40.0)
B_BOUNDS = (-0.15, 1.0)
SCALE_FLOOR = 1.0


def scale_fn(a: np.ndarray, b: float, mu: np.ndarray, sample: np.ndarray) -> np.ndarray:
    return np.maximum(
        np.asarray(a, float)[ab.bucket(sample)] + b * np.abs(np.asarray(mu, float)), SCALE_FLOOR
    )


def fit(train: pd.DataFrame) -> dict[str, Any]:
    r = (train["margin"] - train["mu_v7"]).to_numpy(float)
    mu, s = train["mu_v7"].to_numpy(float), train["games_played_min"].to_numpy()
    a0 = ab.bucket_scales(r, s)

    def nll(th: np.ndarray) -> float:
        return ab.t8_nll(r, scale_fn(th[:4], th[4], mu, s))

    x0 = np.array([*a0, 0.0])
    res = optimize.minimize(
        nll,
        x0,
        method="L-BFGS-B",
        bounds=[A_BOUNDS] * 4 + [B_BOUNDS],
        options={"maxiter": 2000, "ftol": 1e-12},
    )
    th = res.x
    return {
        "a": [float(v) for v in th[:4]],
        "b": float(th[4]),
        "a_b0_init": [float(v) for v in a0],
        "nll": float(res.fun),
        "nll_b0": float(nll(x0)),
        "converged": bool(res.success),
        "n_train": len(r),
        "floor_binding_rows": int(
            np.sum(np.asarray(th[:4])[ab.bucket(s)] + th[4] * np.abs(mu) < SCALE_FLOOR)
        ),
        "b_at_bound": bool(np.isclose(th[4], B_BOUNDS[0]) or np.isclose(th[4], B_BOUNDS[1])),
    }


def predict(test: pd.DataFrame, p: dict[str, Any]) -> pd.DataFrame:
    out = test[ab.ID_COLS + ["games_played_min"]].copy()
    out["mu_margin"] = test["mu_v7"].to_numpy(float)
    out["scale_margin"] = scale_fn(np.array(p["a"]), p["b"], out["mu_margin"], out["games_played_min"])
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
        candidate="B1: t8 scale = a_bucket + b*|mu| on served v7 mean, LOSO t8 MLE",
        code_files=[Path(__file__).resolve(), Path(ab.__file__).resolve()],
        fit_seasons=ab.fit_seasons_loso(),
        with_ml=True,
        extra_meta={
            "form": "per-bucket a_b + common b (declared before the read)",
            "fitted_params_by_fold": {str(k): v for k, v in params.items()},
            "disclosure": "served v7 mean on training rows was fit with 2023/2024 data (protocol nesting caveat)",
        },
    )
    rep: dict[str, Any] = ab.compare_to_export(path, Path(args.export), check_scale=True)
    # switched off (b = 0, a_b = the served row scale): must reproduce the export's scale and p_home_ml
    off = frame[ab.ID_COLS + ["games_played_min"]].copy()
    off_scale = frame["scale_v7"].to_numpy(float)
    off_p = ab.ml_blend(frame["elo"], frame["mu_v7"], off_scale, 0.5)
    exp = pd.read_json(args.export, lines=True, dtype={"event_id": str})
    exp["event_id"] = exp["event_id"].astype(str)
    j = off.assign(p=off_p, sc=off_scale).merge(exp, on="event_id")
    rep["switch_off_max_abs_scale_diff"] = float(np.max(np.abs(j["sc"] - j["scale_margin"])))
    rep["switch_off_max_abs_p_diff"] = float(np.max(np.abs(j["p"] - j["p_home_ml"])))
    # the b = 0 fitter on v7's own fit rows (2023+2024, frozen in-sample mean) must give the frozen scales
    from model_prediction.models.ncaab_v7_serving import V7_SCALES, combine_f1

    fr = frame[frame["season"].isin([2023, 2024]) & frame["ridge"].notna()]
    mu_in = np.array(
        [
            combine_f1(r, v, int(s))[0]
            for r, v, s in zip(fr["ridge"], fr["v6_mean"], fr["games_played_min"], strict=True)
        ]
    )
    got = ab.bucket_scales(fr["margin"].to_numpy(float) - mu_in, fr["games_played_min"].to_numpy())
    rep["b0_fitter_vs_frozen_scales"] = {
        "got": got,
        "frozen": list(V7_SCALES),
        "max_abs_diff": float(np.max(np.abs(np.array(got) - np.array(V7_SCALES)))),
    }
    rep["fitted_params_by_fold"] = {
        str(k): {kk: params[k][kk] for kk in ("a", "b", "nll", "nll_b0", "converged", "b_at_bound")}
        for k in params
    }
    (path.parent / "repro_check.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps(rep, indent=2))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
