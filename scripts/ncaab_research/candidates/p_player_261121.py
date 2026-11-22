"""P1_player_availability and P2_returning_production_early (addendum v4 of protocol 261120, lane P).

Both are mean candidates on top of served v7, exactly as pinned in ``protocol_261120_addendum_v4.json``:

- P1: mu = v7_mu + b1 * dA + b2 * dL, dA = avail_top7_prev1(home) - (away),
  dL = top7_minutes_share_out_3plus(home) - (away).
- P2: mu = v7_mu + w(s) * (c1 * dR + c2 * dG), dR = (ret_gmsc_share_same + ret_gmsc_share_transfer_in)(home)
  - (same)(away), dG = prior_gmsc40_cur_weighted(home) - (away), s = the archive's games_played_min (point in
  time), w(s) = max(0, 1 - s / 10), fixed.

Coefficients by OLS with no intercept (np.linalg.lstsq, no ridge) on the fold's three training seasons among
2023-2026, on cohort rows (D1-vs-D1 with a closing spread line: the line is reduced to that eligibility flag and
never enters an input or a target) where every input is non-null (P2: and w > 0). Target = actual margin -
v7_mu, read only for training rows. A null input (or w = 0) gives adjustment 0: the row's mu is v7's, bit for
bit. scale_margin and dist are v7's. p_home_ml = grader.served_ml_blend(...) on the candidate's margin
distribution. Season 2022 is sealed (confirmation): its feature rows are dropped on load, never fit or scored.

v7_mu is grader.baseline_predictions(...)["v7_served"] prediction columns only. No held-out statistic is computed
here, and no pooled four-season fit is built (that is the confirmation coefficient set, sealed).

    .venv/Scripts/python scripts/ncaab_research/candidates/p_player_261121.py --grid P1_player_availability
    .venv/Scripts/python scripts/ncaab_research/candidates/p_player_261121.py --grid P2_returning_production_early
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
for _p in (RESEARCH, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ab_common_261120 as ab
import grader
import grader_v4

REPO = grader.REPO
DEV_SEASONS = grader.DEV_SEASONS
FEATURE_TABLE = REPO / grader_v4.FEATURE_TABLE_REL
W_GAMES = 10.0  # P2 decay: w(s) = max(0, 1 - s / 10), fixed by the addendum
P1_INPUTS = ("avail_top7_prev1", "top7_minutes_share_out_3plus")
P2_INPUTS = ("ret_gmsc_share_same", "ret_gmsc_share_transfer_in", "prior_gmsc40_cur_weighted")
FEATURE_COLS = tuple(dict.fromkeys(P1_INPUTS + P2_INPUTS))
LABELS = {
    "P1_player_availability": "P1: v7 + b1*d(avail_top7_prev1) + b2*d(top7_minutes_share_out_3plus), LOSO OLS v1",
    "P2_returning_production_early": "P2: v7 + max(0, 1 - s/10)*(c1*d(ret gmsc same+transfer) + c2*d(prior gmsc40)), "
    "LOSO OLS v1",
}


# ------------------------------------------------------------------------------------------ fold map
def fit_seasons_map(seasons: tuple[int, ...] = DEV_SEASONS) -> dict[int, list[int]]:
    """Leave-one-season-out among 2023-2026; the predicted season and 2022 never appear among fit seasons."""
    out = {h: [s for s in seasons if s != h] for h in seasons}
    for h, f in out.items():
        assert h not in f and grader_v4.SEALED_SEASON not in f and set(f) <= grader_v4.DEV_FIT_SEASONS
    return out


# ------------------------------------------------------------------------------------------ design
def design(frame: pd.DataFrame, grid: str) -> tuple[np.ndarray, np.ndarray]:
    """Regressor matrix (n x 2) and the row validity mask (every input non-null; P2 also w > 0)."""
    h = {c: frame[f"home_{c}"].to_numpy(float) for c in FEATURE_COLS}
    a = {c: frame[f"away_{c}"].to_numpy(float) for c in FEATURE_COLS}
    if grid == "P1_player_availability":
        x = np.column_stack([h[P1_INPUTS[0]] - a[P1_INPUTS[0]], h[P1_INPUTS[1]] - a[P1_INPUTS[1]]])
        valid = np.all([~np.isnan(v[c]) for v in (h, a) for c in P1_INPUTS], axis=0)
    elif grid == "P2_returning_production_early":
        w = np.maximum(0.0, 1.0 - frame["games_played_min"].to_numpy(float) / W_GAMES)
        d_r = (h["ret_gmsc_share_same"] + h["ret_gmsc_share_transfer_in"]) - (
            a["ret_gmsc_share_same"] + a["ret_gmsc_share_transfer_in"]
        )
        d_g = h["prior_gmsc40_cur_weighted"] - a["prior_gmsc40_cur_weighted"]
        x = np.column_stack([w * d_r, w * d_g])
        valid = np.all([~np.isnan(v[c]) for v in (h, a) for c in P2_INPUTS], axis=0) & (w > 0)
    else:
        raise ValueError(f"unknown grid {grid!r}")
    return x, valid


def fit(train: pd.DataFrame, grid: str) -> dict[str, Any]:
    """OLS, no intercept, on cohort-eligible training rows with every input non-null."""
    x, valid = design(train, grid)
    keep = valid & train["cohort_eligible"].to_numpy(bool)
    y = (train["margin"] - train["mu_v7"]).to_numpy(float)[keep]
    xk = x[keep]
    coef, *_ = np.linalg.lstsq(xk, y, rcond=None)
    resid = y - xk @ coef
    return {
        "coef": [float(c) for c in coef],
        "n_train": int(keep.sum()),
        "n_train_rows_total": len(train),
        "train_target_rmse_before": round(float(np.sqrt(np.mean(y**2))), 5),
        "train_target_rmse_after": round(float(np.sqrt(np.mean(resid**2))), 5),
        "regressor_sd": [round(float(v), 5) for v in xk.std(axis=0)],
    }


def predict(test: pd.DataFrame, params: dict[str, Any], grid: str) -> pd.DataFrame:
    """mu = v7_mu + adjustment on valid rows; exactly v7_mu (same float) on null rows."""
    if "margin" in test.columns:
        raise AssertionError("held-out margin reached predict")
    x, valid = design(test, grid)
    mu_v7 = test["mu_v7"].to_numpy(float)
    adj = np.where(valid, x @ np.asarray(params["coef"], float), 0.0)
    out = test[ab.ID_COLS + ["games_played_min"]].copy()
    out["mu_margin"] = np.where(valid, mu_v7 + adj, mu_v7)
    out["scale_margin"] = test["scale_v7"].to_numpy(float)
    out["adj_valid"] = valid
    out["p_home_ml"] = grader.served_ml_blend(
        out["event_id"],
        out["mu_margin"].to_numpy(float),
        out["scale_margin"].to_numpy(float),
        np.full(len(out), "t8"),
    )
    return out


def loso(frame: pd.DataFrame, grid: str) -> tuple[pd.DataFrame, dict[int, dict[str, Any]]]:
    if (frame["season"] == grader_v4.SEALED_SEASON).any():
        raise AssertionError("sealed season 2022 reached the candidate frame")
    parts, params = [], {}
    for held, fits in fit_seasons_map().items():
        train = frame[frame["season"].isin(fits)]
        if held in set(train["season"]) or grader_v4.SEALED_SEASON in set(train["season"]):
            raise AssertionError("held-out or sealed season leaked into the training rows")
        p = fit(train, grid)
        p["fit_seasons"] = fits
        params[held] = p
        parts.append(predict(frame[frame["season"] == held].drop(columns=["margin"]), p, grid))
    return pd.concat(parts, ignore_index=True), params


# ------------------------------------------------------------------------------------------ data
def load_features(event_ids: set[str]) -> pd.DataFrame:
    """Feature rows of the given events pivoted to home_/away_ columns; season 2022 rows are dropped on load."""
    cols = ["event_id", "season", "side", "team_id", "game_date", *FEATURE_COLS]
    f = pd.read_json(FEATURE_TABLE, lines=True, dtype={"event_id": str, "team_id": str})[cols]
    f = f[(f["season"] != grader_v4.SEALED_SEASON) & f["event_id"].isin(event_ids)]
    if f.duplicated(["event_id", "side"]).any():
        raise ValueError("duplicate (event_id, side) feature rows")
    wide = f.pivot(index="event_id", columns="side")
    wide.columns = [f"{side}_{c}" for c, side in wide.columns]
    return wide.reset_index()


def load_frame() -> pd.DataFrame:
    """Served v7 D1 rows with archive sample, the cohort-eligibility flag, the training target's margin and
    the home/away player features. The closing line is reduced to the eligibility flag and dropped."""
    a = grader.load_archive(DEV_SEASONS)
    v7 = grader.baseline_predictions(a)["v7_served"]
    v7 = v7[v7["event_id"].isin(a.loc[a["d1"], "event_id"])]
    sub = a.set_index("event_id").loc[v7["event_id"]]
    f = v7[ab.ID_COLS + ["games_played_min"]].copy()
    f["mu_v7"] = v7["mu_margin"].to_numpy(float)
    f["scale_v7"] = v7["scale_margin"].to_numpy(float)
    f["cohort_eligible"] = sub["line"].notna().to_numpy()
    f["margin"] = sub["margin"].to_numpy(float)
    if not np.array_equal(f["games_played_min"].to_numpy(), sub["sample"].astype(int).to_numpy()):
        raise AssertionError("games_played_min is not the archive's point-in-time sample")
    feats = load_features(set(f["event_id"]))
    f = f.merge(feats, on="event_id", how="left")
    # outcome-free orientation check: the table's home side must be the scoreboard's home team
    home_id = {str(e["event_id"]): str(e["home_team_id"]) for e in grader.nr.load_scoreboard_events()}
    have = f["home_team_id"].notna()
    flipped = (f.loc[have, "home_team_id"].astype(str) != f.loc[have, "event_id"].map(home_id)).sum()
    # The table keys a game on the scoreboard's date; the archive on the Eastern day of the tip. They differ
    # only for tips after Eastern midnight (Hawaii/late West Coast, 15 dev games), where the table's date is
    # the day BEFORE, so its history cut is stricter. A table date later than the archive's would leak.
    fd = pd.to_datetime(f.loc[have, "home_game_date"].astype(str).str[:10])
    gap = (pd.to_datetime(f.loc[have, "game_date"]) - fd).dt.days
    if flipped or (gap < 0).any() or (gap > 1).any():
        raise AssertionError(
            f"feature orientation/date mismatch: {flipped} flipped, gaps {gap.value_counts()}"
        )
    f.attrs["feature_date_one_day_earlier"] = int((gap == 1).sum())
    f = f.drop(columns=[c for c in f.columns if c.endswith(("_team_id", "_game_date", "_season", "_side"))])
    assert "line" not in f.columns and "total_line" not in f.columns
    return f.reset_index(drop=True)


# ------------------------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", required=True, choices=sorted(grader_v4.GRID))
    args = ap.parse_args(argv)
    add = grader_v4.load_addendum_v4()
    pins = grader_v4.pinned_input_shas(add)
    grader_v4.verify_pinned_inputs_v4(pins)  # before the feature table is read
    frame = load_frame()
    pred, params = loso(frame, args.grid)
    pred = pred.set_index("event_id").loc[frame["event_id"]].reset_index()
    valid_share = {int(s): round(float(g["adj_valid"].mean()), 5) for s, g in pred.groupby("season")}
    fitted = {str(k): v for k, v in params.items()}
    print(json.dumps({"grid": args.grid, "fitted_by_fold": fitted, "adj_valid_share": valid_share}, indent=1))
    sources = {
        str(p.relative_to(REPO)).replace("\\", "/"): ab.sha256_file(p)
        for p in (
            grader.COMPONENTS,
            grader.LOSO_V7,
            grader.nr.SCOREBOARD,
            grader.nr.LINES,
            REPO / "outputs/ncaab/ncaab_v7_multiseason_frozen.json",
        )
    }
    sources.update(pins)
    path = ab.write_submission(
        pred,
        grid_name=args.grid,
        candidate=LABELS[args.grid],
        code_files=[
            Path(__file__).resolve(),
            Path(ab.__file__).resolve(),
            Path(grader_v4.__file__).resolve(),
        ],
        fit_seasons=fit_seasons_map(),
        with_ml=True,
        extra_meta={
            "addendum_v4_sha256": grader_v4.ADDENDUM_V4_SHA256,
            "data_sources": sources,
            "fitted_coefficients_by_fold": fitted,
            "adjustment_valid_share_by_season": valid_share,
            "coefficient_names": ["b1 (dA)", "b2 (dL)"]
            if args.grid == "P1_player_availability"
            else ["c1 (w*dR)", "c2 (w*dG)"],
            "training_rows": "cohort-eligible (D1-vs-D1 with a closing spread line; presence only) rows of the "
            "three training seasons with every input non-null (P2: and w > 0)",
            "null_rule": "any null input (P2: or w = 0) -> adjustment 0, mu = served v7 bit for bit",
            "v7_mu": "grader.baseline_predictions(load_archive(2023-2026))['v7_served'] prediction columns",
            "p_home_ml": "grader.served_ml_blend(event_ids, mu, scale, 't8')",
        },
    )
    (path.parent / "fit_log.json").write_text(
        json.dumps({"grid": args.grid, "fitted_by_fold": fitted, "adj_valid_share": valid_share}, indent=2),
        encoding="utf-8",
    )
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
