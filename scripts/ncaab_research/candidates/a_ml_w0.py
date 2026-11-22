"""A_ml_w0 (protocol 261120): moneyline from the served v7 margin distribution alone (Elo weight 0).

p_home = w * Elo + (1 - w) * P(margin >= 1) with w = 0, the SERVED v7 mean and t(8) scale, clipped to
[0.0001, 0.9999] exactly as the served rule clips. No free parameter. w = 0.5 reproduces the served v7
moneyline (checked below against ``grader.py export --model v7_served``). mu and scale are the served v7
rows unchanged, so only p_home_ml differs from the baseline.

Usage (repo root): ``.venv/Scripts/python scripts/ncaab_research/candidates/a_ml_w0.py --export <v7.jsonl>``
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import ab_common_261120 as ab
import numpy as np
import pandas as pd

GRID = "A_ml_w0"
W = 0.0


def predict(frame: pd.DataFrame, w: float = W) -> pd.DataFrame:
    out = frame[ab.ID_COLS + ["games_played_min"]].copy()
    out["mu_margin"] = frame["mu_v7"].to_numpy(float)
    out["scale_margin"] = frame["scale_v7"].to_numpy(float)
    out["p_home_ml"] = ab.ml_blend(frame["elo"], out["mu_margin"], out["scale_margin"], w)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True, help="grader.py export --model v7_served output")
    args = ap.parse_args(argv)
    frame = ab.load_frame()
    pred = predict(frame)
    path = ab.write_submission(
        pred,
        grid_name=GRID,
        candidate="A_ml_w0: p_home = P_t8(margin >= 1) under served v7 (Elo weight 0)",
        code_files=[Path(__file__).resolve(), Path(ab.__file__).resolve()],
        fit_seasons=ab.V7_FIT_SEASONS,
        with_ml=True,
        extra_meta={
            "elo_weight": W,
            "free_parameters": "none; mu/scale are served v7 (fit seasons are v7's own, as in the export)",
        },
    )
    # switched-off reproduction: w = 0.5 must equal the export's served p_home_ml; mu/scale must be identical
    exp = pd.read_json(args.export, lines=True, dtype={"event_id": str})
    exp["event_id"] = exp["event_id"].astype(str)
    off = predict(frame, w=0.5).merge(exp[["event_id", "p_home_ml"]], on="event_id", suffixes=("", "_v7"))
    rep = ab.compare_to_export(path, Path(args.export))
    rep["switch_off_w05_max_abs_p_diff"] = float(np.max(np.abs(off["p_home_ml"] - off["p_home_ml_v7"])))
    sub = pd.read_json(path, lines=True, dtype={"event_id": str})
    sub["event_id"] = sub["event_id"].astype(str)
    j = sub[["event_id", "p_home_ml"]].merge(
        exp[["event_id", "p_home_ml"]], on="event_id", suffixes=("", "_v7")
    )
    gap = np.abs(j["p_home_ml"] - j["p_home_ml_v7"])  # outcome-free: how far the w=0 arm moves p
    rep["abs_p_change_vs_served"] = {"mean": float(gap.mean()), "max": float(gap.max())}
    (path.parent / "repro_check.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps(rep, indent=2))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
