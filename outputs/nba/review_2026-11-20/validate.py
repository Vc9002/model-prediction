"""Validation gates: regrade, reproduce model prob, reproduce 11-18 numbers, snapshot join."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
from scipy.stats import norm

df = pd.read_pickle("nba_rows.pkl")
st = df[df.status == "settled"].copy()
print("settled rows", len(st), "result values", st.result.value_counts().to_dict())


def grade(r):
    if r.hs is None or r.as_ is None or np.isnan(r.hs) or np.isnan(r.as_):
        return None
    if r.mt == "moneyline":
        win = (r.hs > r.as_) if r.selection == "home" else (r.as_ > r.hs)
        return "win" if win else "loss"
    if r.mt == "spread":
        own, opp = (r.hs, r.as_) if r.selection == "home" else (r.as_, r.hs)
        v = own + r.line - opp
    else:
        tot = r.hs + r.as_
        v = (tot - r.line) if r.selection == "over" else (r.line - tot)
    return "push" if v == 0 else ("win" if v > 0 else "loss")


st["regrade"] = st.apply(grade, axis=1)
mm = st[st.regrade != st.result]
print("regrade mismatches", len(mm))
print(mm[["model", "mt", "selection", "line", "hs", "as_", "result", "regrade"]].head(20).to_string())


# reproduce model prob for spread/total
def repro(r):
    if r.mt == "total" and r.inf_mean is not None and not np.isnan(r.inf_mean or np.nan):
        p_over = 1 - norm.cdf(r.inf_line, r.inf_mean, r.inf_sd)
        return p_over if r.selection == "over" else 1 - p_over
    if r.mt == "spread" and r.inf_mean is not None and not np.isnan(r.inf_mean or np.nan):
        # inf_line is the away line expressed as threshold on home margin (home-away)
        p_away = norm.cdf(r.inf_line, r.inf_mean, r.inf_sd)
        return p_away if r.selection == "away" else 1 - p_away
    return np.nan


st["p_repro"] = st.apply(repro, axis=1)
for (m, mt), g in st[st.mt != "moneyline"].groupby(["model", "mt"]):
    d = (g.p_repro - g.p_model).abs()
    print(f"{m:28s} n={len(g):3d} repro_max_abs_err={d.max():.4f} n_bad(>0.01)={(d > 0.01).sum()}")

# inspect inf_line vs ledger line convention
sp = st[st.mt == "spread"]
print(
    "spread line vs inf_line examples:\n",
    sp[["model", "selection", "line", "inf_line", "inf_mean", "exp_margin", "p_model", "p_repro"]]
    .head(12)
    .to_string(),
)

# reproduce 11-18 numbers: settled by 11-18 (settled_at not loaded; use event start < 11-18 12:00 UTC), edge>=2%
key = ["event_id", "mt", "selection", "line", "model"]
st["tier_rank"] = (st.tier != "main").astype(int)
dd = st.sort_values("tier_rank").drop_duplicates(subset=key, keep="first")
print("deduped settled", len(dd), "from", len(st))
cut = datetime(2026, 11, 18, 12, tzinfo=UTC)
for lab, sub in [("all", dd), ("<=11-18", dd[dd.start < cut])]:
    for mt, thr in [("total", 0.02), ("spread", 0.02), ("moneyline", 0.10)]:
        g = sub[(sub.mt == mt) & (sub.edge >= thr)]
        print(lab, mt, f"edge>={thr}", (g.result == "win").sum(), "-", (g.result == "loss").sum())
# alt: no dedupe across tiers, main only, flat only
for tier in ["main", "flat"]:
    sub = st[(st.tier == tier) & (st.start < cut)]
    for mt, thr in [("total", 0.02), ("spread", 0.02), ("moneyline", 0.10)]:
        g = sub[(sub.mt == mt) & (sub.edge >= thr)]
        print("tier", tier, mt, (g.result == "win").sum(), "-", (g.result == "loss").sum())

# check: duplicates per (event, mt, model) after dedupe (different lines/sides)
dup = dd.groupby(["event_id", "mt", "model"]).size()
print("events with >1 settled pick per (event,mt,model):", (dup > 1).sum())
st.to_pickle("settled_validated.pkl")
