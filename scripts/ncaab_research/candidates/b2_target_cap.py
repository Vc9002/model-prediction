"""B2_target_cap (protocol 261120): v7 F1 combiner refit on a blowout-capped target.

Per fold, on the three training seasons' D1-vs-D1 rows with a served ridge value: y = clip(margin, -25, +25)
(the one pre-registered cap), F1 design [ridge rg_c010_l02_k14, sign(v6) * max(|v6 as-of mean| - 12, 0)], v7's
least squares (no intercept, 1e-6 ridge), then per-bucket t(8) scales by MLE on the capped-target residuals
(the spec refits "the combiner (and scales) on the blowout-compressed target"). Predictions apply the fitted
combiner as is on the uncapped scale. Moneyline: served 0.5 Elo blend on the new mean/scale.

Switched off (no cap, fit on v7's own fit seasons) this is ``scripts/ncaab_v7_multiseason.py::fit_config``
form F1; the checks below require it to reproduce the frozen coefficients/scales, the 2025/2026 export rows and
the 2023/2024 leave-one-season-out rows. Disclosure: the delta against served v7 mixes the cap with a
three-season refit (v7 used two); the uncapped three-season coefficients are reported (outcome-free) alongside.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import ab_common_261120 as ab
import numpy as np
import pandas as pd

GRID = "B2_target_cap"
CAP = 25.0


def design(ridge: np.ndarray, v6_mean: np.ndarray) -> np.ndarray:
    return np.column_stack([np.asarray(ridge, float), ab.tail_term(v6_mean)])


def fit_f1(train: pd.DataFrame, cap: float | None) -> dict[str, Any]:
    t = train[train["ridge"].notna()]
    x = design(t["ridge"], t["v6_mean"])
    y = t["margin"].to_numpy(float)
    if cap is not None:
        y = np.clip(y, -cap, cap)
    coef = ab.ols(x, y)
    scales = ab.bucket_scales(y - x @ coef, t["games_played_min"].to_numpy())
    return {"coef": [float(c) for c in coef], "scales": scales, "cap": cap, "n_train": len(t)}


def fit(train: pd.DataFrame) -> dict[str, Any]:
    p = fit_f1(train, CAP)
    p["uncapped_same_rows"] = {k: v for k, v in fit_f1(train, None).items() if k in ("coef", "scales")}
    return p


def predict(test: pd.DataFrame, p: dict[str, Any]) -> pd.DataFrame:
    test = test[test["ridge"].notna()]
    out = test[ab.ID_COLS + ["games_played_min"]].copy()
    out["mu_margin"] = design(test["ridge"], test["v6_mean"]) @ np.array(p["coef"])
    out["scale_margin"] = np.array(p["scales"])[ab.bucket(out["games_played_min"])]
    out["p_home_ml"] = ab.ml_blend(test["elo"], out["mu_margin"], out["scale_margin"], 0.5)
    return out


def reproduction(frame: pd.DataFrame, export: Path) -> dict[str, Any]:
    from model_prediction.models.ncaab_v7_serving import V7_COEF, V7_SCALES

    exp = pd.read_json(export, lines=True, dtype={"event_id": str})
    exp["event_id"] = exp["event_id"].astype(str)
    rep: dict[str, Any] = {}
    full = fit_f1(frame[frame["season"].isin([2023, 2024])], None)
    rep["uncapped_fit_2023_2024_vs_frozen"] = {
        "coef": full["coef"],
        "frozen_coef": list(V7_COEF),
        "scales": full["scales"],
        "frozen_scales": list(V7_SCALES),
        "max_abs_coef_diff": float(np.max(np.abs(np.array(full["coef"]) - np.array(V7_COEF)))),
        "max_abs_scale_diff": float(np.max(np.abs(np.array(full["scales"]) - np.array(V7_SCALES)))),
        "n_train": full["n_train"],
    }
    parts = [predict(frame[frame["season"].isin([2025, 2026])].drop(columns=["margin"]), full)]
    for held, fits in ((2023, [2024]), (2024, [2023])):
        p = fit_f1(frame[frame["season"].isin(fits)], None)
        parts.append(predict(frame[frame["season"] == held].drop(columns=["margin"]), p))
    off = pd.concat(parts, ignore_index=True).merge(exp, on="event_id", suffixes=("", "_v7"))
    rep["switch_off_rows"] = {
        "n_pred": int(sum(len(x) for x in parts)),
        "n_export": len(exp),
        "n_joined": len(off),
    }
    for s, g in off.groupby("season"):
        rep[f"switch_off_season_{s}"] = {
            "max_abs_mu_diff": float(np.max(np.abs(g["mu_margin"] - g["mu_margin_v7"]))),
            "max_abs_scale_diff": float(np.max(np.abs(g["scale_margin"] - g["scale_margin_v7"]))),
            "max_abs_p_diff": float(np.max(np.abs(g["p_home_ml"] - g["p_home_ml_v7"]))),
        }
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True, help="grader.py export --model v7_served output")
    ap.add_argument("--repro-only", action="store_true", help="run the switched-off checks and stop")
    args = ap.parse_args(argv)
    frame = ab.load_frame()
    rep = reproduction(frame, Path(args.export))
    if args.repro_only:
        print(json.dumps(rep, indent=2))
        return 0
    pred, params = ab.loso_apply(frame, fit, predict)
    order = frame.loc[frame["ridge"].notna(), "event_id"]
    pred = pred.set_index("event_id").loc[order].reset_index()
    path = ab.write_submission(
        pred,
        grid_name=GRID,
        candidate="B2: v7 F1 combiner + bucket t8 scales refit LOSO on margin clipped to +-25",
        code_files=[Path(__file__).resolve(), Path(ab.__file__).resolve()],
        fit_seasons=ab.fit_seasons_loso(),
        with_ml=True,
        extra_meta={
            "cap": CAP,
            "fitted_params_by_fold": {str(k): v for k, v in params.items()},
            "disclosure": "delta vs served v7 mixes the cap with a 3-season refit; uncapped 3-season coefs reported",
        },
    )
    rep["vs_export"] = ab.compare_to_export(path, Path(args.export), check_scale=True)
    rep["fitted_params_by_fold"] = {str(k): v for k, v in params.items()}
    (path.parent / "repro_check.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps(rep, indent=2))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
