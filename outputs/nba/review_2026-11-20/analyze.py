"""NBA loss forensics: builds tables used in 02_loss_forensics.md (prints markdown to stdout)."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
from scipy.stats import norm

rng = np.random.default_rng(7)
m = pd.read_pickle("enriched.pkl")
m["quoted"] = (
    (m.provenance == "decision_time_executable_quote")
    & (m.qsource == "polymarket_us")
    & (m.reconstructed.astype(str) == "False")
)
m["regime"] = np.where(m.reason == "QUALIFIED", "QUAL", np.where(m.reason == "FLAT_CALL", "FLAT", "RESEARCH"))
m["mshort"] = m.model.str.replace("nba-", "", regex=False)
m["mkt"] = m.mt.map({"moneyline": "ML", "spread": "spread", "total": "total"})

# scores per event from any settled row (for hypothetical grading of superseded picks)
scores = m[m.hs.notna()].groupby("event_id")[["hs", "as_"]].first()

st = m[m.status == "settled"].copy()
st["tier_rank"] = (st.tier != "main").astype(int)
KEY = ["event_id", "mt", "selection", "line", "model"]
dd = st.sort_values("tier_rank").drop_duplicates(subset=KEY, keep="first").copy()
dd["y"] = (dd.result == "win").astype(int)
dd["roi1"] = dd.pnl / dd.units


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def tag(n):
    return " (unproven)" if n < 15 else ""


def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def cluster_boot_diff(g, a, b, B=2000):
    """Game-clustered bootstrap CI for mean(Brier_a - Brier_b)."""
    g = g[g[a].notna() & g[b].notna()]
    if len(g) < 5:
        return (np.nan, np.nan, np.nan)
    d = (g[a] - g.y) ** 2 - (g[b] - g.y) ** 2
    ev = g.event_id.values
    uniq = np.unique(ev)
    sums = pd.Series(d.values).groupby(ev).sum()
    cnts = pd.Series(np.ones(len(d))).groupby(ev).sum()
    s, c = sums.loc[uniq].values, cnts.loc[uniq].values
    bs = []
    for _ in range(B):
        i = rng.integers(0, len(uniq), len(uniq))
        bs.append(s[i].sum() / c[i].sum())
    return (d.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5))


def summary_row(g):
    n = len(g)
    w = int(g.y.sum())
    lo, hi = wilson(w, n)
    return dict(
        n=n,
        games=g.event_id.nunique(),
        W=w,
        L=n - w,
        hit=w / n if n else np.nan,
        ci=f"{lo:.0%}-{hi:.0%}" if n else "",
        p_model=g.p_model.mean(),
        p_ask=g.p_mkt.mean(),
        p_mid=g.dec_mid.mean(),
        pnl=g.pnl.sum(),
        roi_u=g.pnl.sum() / g.units.sum() if n else np.nan,
        roi_1u=g.roi1.mean(),
    )


def fmt_tbl(rows, cols, fmts):
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    lines = [head, sep]
    for r in rows:
        lines.append("| " + " | ".join(fmts[c](r[c]) if c in fmts else str(r[c]) for c in cols) + " |")
    return "\n".join(lines)


pct = lambda x: "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.1%}"
num2 = lambda x: "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.2f}"
num3 = lambda x: "" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.4f}"
F = dict(hit=pct, p_model=pct, p_ask=pct, p_mid=pct, roi_u=pct, roi_1u=pct, pnl=num2)

print("## 0. Population")
print(
    f"NBA ledger rows: {len(m)} (settled {int((m.status == 'settled').sum())}, superseded "
    f"{int((m.status == 'superseded').sum())}, open {int((m.status == 'open').sum())}; "
    "voided/archived: 0 in ledger_records)."
)
print(
    f"Settled after dedupe on (event, selection, line, model), Main preferred: {len(dd)} picks, "
    f"{dd.event_id.nunique()} games."
)
pop = dd.groupby(["quoted", "regime"]).size().rename("n").reset_index()
print(pop.to_string(index=False))
print()

Q = dd[dd.quoted].copy()
U = dd[~dd.quoted].copy()

# ---------------- 1. per model x market
print("## 1. Record / calibration by model (quoted rows; all regimes)")
rows = []
for (mk, mo), g in Q.groupby(["mkt", "mshort"]):
    r = summary_row(g)
    r.update(mkt=mk, model=mo + tag(len(g)))
    rows.append(r)
cols = [
    "mkt",
    "model",
    "n",
    "games",
    "W",
    "L",
    "hit",
    "ci",
    "p_model",
    "p_ask",
    "p_mid",
    "pnl",
    "roi_u",
    "roi_1u",
]
print(fmt_tbl(rows, cols, F))
print()
print("### 1b. Same, split by regime (QUAL = edge-qualified call, FLAT = flat every-game call)")
rows = []
for (mk, rg), g in Q.groupby(["mkt", "regime"]):
    r = summary_row(g)
    r.update(mkt=mk, model=rg + tag(len(g)))
    rows.append(r)
print(fmt_tbl(rows, cols, F))
print()
print("### 1c. Unquoted rows (no executable Polymarket quote at decision; excluded from headline)")
rows = []
for (mk, mo), g in U.groupby(["mkt", "mshort"]):
    r = summary_row(g)
    r.update(mkt=mk, model=mo + tag(len(g)))
    rows.append(r)
print(fmt_tbl(rows, cols, F))
print()

print("### 1d. Brier / log loss: model vs market on the same picks (quoted)")
print(
    "Market = decision ask (biased high by half-spread) and decision midpoint from snapshot. "
    "Conditional on model-selected picks. Diff CI = game-clustered bootstrap of Brier(model)-Brier(mid)."
)
rows = []
for key, g in list(Q.groupby("mkt")) + [(("ALL"), Q)]:
    for sub_lab, sub in [("all", g), ("QUAL", g[g.regime == "QUAL"])]:
        h = sub[sub.dec_mid.notna()]
        if len(h) == 0:
            continue
        d, lo, hi = cluster_boot_diff(h.assign(pm=h.p_model, pk=h.dec_mid), "pm", "pk")
        rows.append(
            dict(
                mkt=f"{key} {sub_lab}{tag(len(h))}",
                n=len(h),
                games=h.event_id.nunique(),
                b_model=((h.p_model - h.y) ** 2).mean(),
                b_ask=((h.p_mkt - h.y) ** 2).mean(),
                b_mid=((h.dec_mid - h.y) ** 2).mean(),
                ll_model=ll(h.p_model, h.y).mean(),
                ll_mid=ll(h.dec_mid, h.y).mean(),
                diff=f"{d:+.4f} [{lo:+.4f},{hi:+.4f}]",
            )
        )
print(
    fmt_tbl(
        rows,
        ["mkt", "n", "games", "b_model", "b_ask", "b_mid", "ll_model", "ll_mid", "diff"],
        dict(b_model=num3, b_ask=num3, b_mid=num3, ll_model=num3, ll_mid=num3),
    )
)
print()

# ---------------- 2. edge buckets
print("## 2. Edge buckets (quoted)")
bins = [-1, 0, 0.02, 0.05, 0.08, 0.12, 1]
labs = ["<0", "0-2", "2-5", "5-8", "8-12", "12+"]
Q["eb"] = pd.cut(Q.edge, bins=bins, labels=labs, right=False)
rows = []
for (mk, eb), g in Q.groupby(["mkt", "eb"], observed=True):
    r = summary_row(g)
    r.update(mkt=mk, model=str(eb) + tag(len(g)))
    rows.append(r)
print(fmt_tbl(rows, cols, F))
print()
print("### 2b. Edge buckets, v9 only (spread+total)")
rows = []
for (mk, eb), g in Q[Q.mshort.str.endswith("v9")].groupby(["mkt", "eb"], observed=True):
    r = summary_row(g)
    r.update(mkt=mk, model=str(eb) + tag(len(g)))
    rows.append(r)
print(fmt_tbl(rows, cols, F))
print()
# correlation edge vs outcome-surplus
for mk, g in Q[Q.edge > 0].groupby("mkt"):
    sur = g.y - g.p_mkt
    print(
        f"- {mk}: corr(edge, win - ask) over edge>0 picks = {np.corrcoef(g.edge, sur)[0, 1]:+.3f} (n={len(g)})"
    )
print()

# ---------------- 3. sides
print("## 3. Sides (quoted)")


def side_tbl(lab, frame, col):
    rows = []
    for (mk, v), g in frame.groupby(["mkt", col], observed=True):
        r = summary_row(g)
        r.update(mkt=mk, model=f"{v}{tag(len(g))}")
        rows.append(r)
    print(f"### {lab}")
    print(fmt_tbl(rows, cols, F))
    print()


Q["ha"] = np.where(Q.mt == "total", "n/a", Q.selection)
side_tbl("Home/away (spread, ML)", Q[Q.mt != "total"], "ha")
Q["favdog"] = np.where(
    Q.mt == "moneyline",
    np.where(Q.p_mkt >= 0.5, "fav", "dog"),
    np.where(Q.line < 0, "fav", np.where(Q.line > 0, "dog", "pk")),
)
side_tbl("Favorite/dog (spread by line sign, ML by price)", Q[Q.mt != "total"], "favdog")
side_tbl("Over/under", Q[Q.mt == "total"], "selection")
sp = Q[Q.mt == "spread"].copy()
sp["band"] = pd.cut(sp.line.abs(), [0, 3.6, 6.6, 9.6, 50], labels=["|L|<=3.5", "4-6.5", "7-9.5", "10+"])
side_tbl("Spread |line| bands", sp, "band")
sp["fd_band"] = sp.favdog + " " + sp.band.astype(str)
side_tbl("Spread fav/dog x band", sp, "fd_band")
tt = Q[Q.mt == "total"].copy()
tt["band"] = pd.cut(tt.line, [0, 215, 225, 235, 400], labels=["<215", "215-224.5", "225-234.5", "235+"])
tt["ou_band"] = tt.selection + " " + tt.band.astype(str)
side_tbl("Total line bands", tt, "band")
side_tbl("Total over/under x band", tt, "ou_band")

# ---------------- 4. CLV
print("## 4. Closing line value (quoted, snapshot-recomputed)")
c = Q[Q.dec_mid.notna() & Q.close_mid.notna()].copy()
c["later"] = c.close_obs > c.dec_snap_obs
c["clv_mid"] = c.close_mid - c.dec_mid
c["clv_ask"] = c.close_ask - c.dec_ask
print(
    f"Rows with decision+last-pre-tip snapshot: {len(c)} of {len(Q)} quoted. "
    f"Strictly later capture than decision: {int(c.later.sum())}."
)
print(
    f"Median gap last capture -> tip: {c.close_gap_min.median():.0f} min "
    f"(IQR {c.close_gap_min.quantile(0.25):.0f}-{c.close_gap_min.quantile(0.75):.0f})."
)
lc = c[c.later]
print(f"Decision capture == last capture (CLV 0 by construction): {int((~c.later).sum())}.")
rows = []
for mk, g in list(lc.groupby("mkt")) + [("ALL", lc)]:
    if len(g) < 2:
        continue
    pos = (g.clv_mid > 0.0001).sum()
    neg = (g.clv_mid < -0.0001).sum()
    corr = np.corrcoef(g.clv_mid, g.y)[0, 1] if g.clv_mid.std() > 0 else np.nan
    rows.append(
        dict(
            mkt=mk + tag(len(g)),
            n=len(g),
            clv_mid=g.clv_mid.mean(),
            clv_ask=g.clv_ask.mean(),
            pos=int(pos),
            neg=int(neg),
            corr=corr,
            hit_pos=g[g.clv_mid > 0.0001].y.mean(),
            hit_neg=g[g.clv_mid < -0.0001].y.mean(),
        )
    )
print(
    fmt_tbl(
        rows,
        ["mkt", "n", "clv_mid", "clv_ask", "pos", "neg", "corr", "hit_pos", "hit_neg"],
        dict(
            clv_mid=lambda x: f"{x * 100:+.2f}c",
            clv_ask=lambda x: f"{x * 100:+.2f}c",
            corr=lambda x: f"{x:+.3f}",
            hit_pos=pct,
            hit_neg=pct,
        ),
    )
)
print()
lm = Q[Q.fair_dec.notna() & Q.fair_close.notna()].copy()
lm["later"] = lm.close_obs > lm.dec_snap_obs
lm = lm[lm.later]
# positive = line moved toward our pick
lm["line_clv"] = np.where(
    lm.mt == "spread",
    lm.fair_dec - lm.fair_close,
    np.where(lm.selection == "over", lm.fair_close - lm.fair_dec, lm.fair_dec - lm.fair_close),
)
print("Main-line movement (fair 50c line, pick terms; + = moved toward pick), later capture only:")
for mk, g in lm.groupby("mkt"):
    print(
        f"- {mk}: n={len(g)}, mean {g.line_clv.mean():+.2f} pts, median {g.line_clv.median():+.2f}, "
        f"toward {int((g.line_clv > 0.25).sum())} / away {int((g.line_clv < -0.25).sum())}; "
        f"hit when toward {g[g.line_clv > 0.25].y.mean():.1%}, when away {g[g.line_clv < -0.25].y.mean():.1%}"
    )
# ledger closing fields coverage
lcov = Q.close_p_ledger.notna().sum()
print(
    f"Ledger closing_* filled on {lcov}/{len(Q)} quoted rows; mean ledger probability_clv "
    f"{Q.clv_ledger.mean() * 100:+.2f}c (n={Q.clv_ledger.notna().sum()})."
)
print()

# ---------------- 5. availability adjustment
print("## 5. Injury / availability adjustment (v2-v5 only store it)")
a = Q[Q.avail_gap.notna()].copy()
print(
    f"Quoted settled rows with availability fields: {len(a)} "
    f"(models: {', '.join(sorted(a.mshort.unique()))}). v7/v9 rows store no availability component."
)
asp = a[a.mt == "spread"].copy()
# gap is added to home-minus-away margin; aligned if it pushes toward our side
asp["aligned"] = np.where(asp.selection == "home", asp.avail_gap > 0, asp.avail_gap < 0)
asp["absband"] = pd.cut(asp.avail_gap.abs(), [-0.01, 0.5, 2, 4, 100], labels=["<=0.5", "0.5-2", "2-4", "4+"])
# would the pick still have positive edge without the adjustment?
mean_noadj = asp.inf_mean - asp.avail_gap
p_away_noadj = norm.cdf(asp.inf_line, mean_noadj, asp.inf_sd)
asp["p_noadj"] = np.where(asp.selection == "away", p_away_noadj, 1 - p_away_noadj)
asp["driven"] = (asp.edge > 0) & (asp.p_noadj - asp.p_mkt <= 0)
side_tbl("Spread by |availability_points_gap|", asp, "absband")
side_tbl("Spread: adjustment pushed toward our side?", asp, "aligned")
side_tbl("Spread: pick only had +edge because of the adjustment", asp, "driven")
att = a[a.mt == "total"].copy()
att["absband"] = pd.cut(att.avail_loss.abs(), [-0.01, 1, 3, 6, 100], labels=["<=1", "1-3", "3-6", "6+"])
side_tbl("Total by |availability_total_loss|", att, "absband")

# ---------------- 6. timing / flips
print("## 6. Timing and flips")
Q["lead_h"] = (Q.start - Q.created).dt.total_seconds() / 3600
Q["lead"] = pd.cut(Q.lead_h, [-100, 1, 3, 8, 1000], labels=["<1h", "1-3h", "3-8h", "8h+"])
side_tbl("Final (settled) pick by lead time to tip", Q, "lead")
allr = m[m.status.isin(["settled", "superseded"])].copy()
allr = allr.sort_values("created")
grp = allr.groupby(["event_id", "mt", "model", "tier"])
flip_rows = []
for k, g in grp:
    if (g.status == "settled").sum() == 0:
        continue
    sides = g.selection.tolist()
    first, last = g.iloc[0], g[g.status == "settled"].iloc[-1]
    flip_rows.append(
        dict(
            event_id=k[0],
            mt=k[1],
            model=k[2],
            tier=k[3],
            runs=len(g),
            nsides=len(set(sides)),
            first_sel=first.selection,
            first_line=first.line,
            first_edge=first.edge,
            first_pm=first.p_mkt,
            first_quoted=first.provenance,
            last_sel=last.selection,
            last_line=last.line,
            result=last.result,
            first_created=first.created,
            start=first.start,
        )
    )
fr = pd.DataFrame(flip_rows)
fr = fr.sort_values("tier", key=lambda s: (s != "main").astype(int)).drop_duplicates(
    ["event_id", "mt", "model", "first_sel", "last_sel", "last_line"]
)


def hyp_grade(r):
    if r.event_id not in scores.index:
        return None
    hs, as_ = scores.loc[r.event_id]
    sel, line = r.first_sel, r.first_line
    if r.mt == "moneyline":
        return int((hs > as_) if sel == "home" else (as_ > hs))
    if r.mt == "spread":
        own, opp = (hs, as_) if sel == "home" else (as_, hs)
        v = own + line - opp
    else:
        v = (hs + as_ - line) if sel == "over" else (line - hs - as_)
    return None if v == 0 else int(v > 0)


fr["first_y"] = fr.apply(hyp_grade, axis=1)
fr["last_y"] = (fr.result == "win").astype(int)
fr["flipped"] = fr.first_sel != fr.last_sel
print(
    f"(event, market, model) groups with a settled pick: {len(fr)}; mean runs per group {fr.runs.mean():.1f}; "
    f"side flipped first-run -> settled: {int(fr.flipped.sum())} ({fr.flipped.mean():.0%}); "
    f"groups with >1 distinct side across runs: {int((fr.nsides > 1).sum())}."
)
rows = []
for (mk, fl), g in fr.groupby([fr.mt, fr.flipped]):
    g2 = g[g.first_y.notna()]
    rows.append(dict(mkt=mk, flipped=fl, n=len(g), first_hit=g2.first_y.mean(), last_hit=g.last_y.mean()))
print(fmt_tbl(rows, ["mkt", "flipped", "n", "first_hit", "last_hit"], dict(first_hit=pct, last_hit=pct)))
print(
    "first_hit = HYPOTHETICAL grade of the first-run pick at its own line (final scores); last_hit = settled."
)
print()

# ---------------- 7. residuals
print("## 7. Residuals: model projection vs market main line vs result (one row per event x model)")
res = m[(m.status == "settled") & m.hs.notna()].copy()
res = res.sort_values("tier", key=lambda s: (s != "main").astype(int)).drop_duplicates(["event_id", "model"])
res["act_margin"] = res.hs - res.as_
res["act_total"] = res.hs + res.as_
rows = []
for mo, g in res[res.mt == "spread"].groupby("mshort"):
    e_mod = g.act_margin - g.exp_margin
    # market home margin from fair line in pick terms: pick team line L -> home margin = -L if home else +L
    mk_home = np.where(g.selection == "home", -g.fair_dec, g.fair_dec)
    e_mkt = g.act_margin - mk_home
    ok = ~np.isnan(mk_home)
    rows.append(
        dict(
            model=mo + tag(len(g)),
            n=len(g),
            bias=e_mod.mean(),
            mae=e_mod.abs().mean(),
            sd=e_mod.std(),
            n_mkt=int(ok.sum()),
            mkt_bias=e_mkt[ok].mean(),
            mkt_mae=np.abs(e_mkt[ok]).mean(),
            mod_mae_same=np.abs(e_mod[ok]).mean(),
            closer=np.mean(np.abs(e_mkt[ok]) < np.abs(e_mod[ok])) if ok.any() else np.nan,
        )
    )
f2 = lambda x: "" if x is None or np.isnan(x) else f"{x:+.2f}"
f2u = lambda x: "" if x is None or np.isnan(x) else f"{x:.2f}"
print("Spread (home margin):")
print(
    fmt_tbl(
        rows,
        ["model", "n", "bias", "mae", "sd", "n_mkt", "mod_mae_same", "mkt_mae", "mkt_bias", "closer"],
        dict(bias=f2, mae=f2u, sd=f2u, mkt_bias=f2, mkt_mae=f2u, mod_mae_same=f2u, closer=pct),
    )
)
rows = []
for mo, g in res[res.mt == "total"].groupby("mshort"):
    e_mod = g.act_total - g.exp_total
    e_mkt = g.act_total - g.fair_dec
    ok = g.fair_dec.notna().values
    rows.append(
        dict(
            model=mo + tag(len(g)),
            n=len(g),
            bias=e_mod.mean(),
            mae=e_mod.abs().mean(),
            sd=e_mod.std(),
            n_mkt=int(ok.sum()),
            mkt_bias=e_mkt[ok].mean(),
            mkt_mae=np.abs(e_mkt[ok]).mean(),
            mod_mae_same=np.abs(e_mod[ok]).mean(),
            closer=np.mean(np.abs(e_mkt[ok]) < np.abs(e_mod[ok])) if ok.any() else np.nan,
            mod_minus_mkt=(g.exp_total - g.fair_dec)[ok].mean(),
        )
    )
print("Total (bias = actual - projection; + means projection too low):")
print(
    fmt_tbl(
        rows,
        [
            "model",
            "n",
            "bias",
            "mae",
            "sd",
            "n_mkt",
            "mod_mae_same",
            "mkt_mae",
            "mkt_bias",
            "closer",
            "mod_minus_mkt",
        ],
        dict(
            bias=f2, mae=f2u, sd=f2u, mkt_bias=f2, mkt_mae=f2u, mod_mae_same=f2u, closer=pct, mod_minus_mkt=f2
        ),
    )
)
print()
# market-relative skill: corr(model - market, actual - market)
for mt, mcol, acol in [("spread", "exp_margin", "act_margin"), ("total", "exp_total", "act_total")]:
    g = res[(res.mt == mt) & res.fair_dec.notna()]
    mk = g.fair_dec if mt == "total" else np.where(g.selection == "home", -g.fair_dec, g.fair_dec)
    x = g[mcol] - mk
    yv = g[acol] - mk
    print(
        f"- {mt}: corr(model - market, actual - market) = {np.corrcoef(x, yv)[0, 1]:+.3f} "
        f"(n={len(g)} event-model rows, {g.event_id.nunique()} games); OLS slope {np.polyfit(x, yv, 1)[0]:+.2f}"
    )
print()

# ---------------- 8. biggest losses / teams
print("## 8. Biggest losses and team patterns (quoted)")
big = Q.sort_values("pnl").head(12)
print("| date | game | model | pick | line | model | ask | edge | units | pnl | score |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for r in big.itertuples():
    pick = r.selection
    ln = "" if pd.isna(r.line) else f"{r.line:+.1f}" if r.mt == "spread" else f"{r.line:.1f}"
    print(
        f"| {r.start.astimezone(UTC):%m-%d} | {r.away} @ {r.home} | {r.mshort} | {pick} | {ln} | {r.p_model:.1%} | "
        f"{r.p_mkt:.1%} | {r.edge:+.1%} | {r.units:.2f} | {r.pnl:+.2f} | {int(r.as_)}-{int(r.hs)} |"
    )
print()
# team on pick side (spread/ML)
sm = Q[Q.mt != "total"].copy()
sm["pick_team"] = np.where(sm.selection == "home", sm.home, sm.away)
sm["opp_team"] = np.where(sm.selection == "home", sm.away, sm.home)
tp = sm.groupby("pick_team").agg(n=("y", "size"), W=("y", "sum"), pnl=("pnl", "sum")).sort_values("pnl")
print("Spread+ML, team we backed (worst 8 by pnl):")
print(tp.head(8).to_string())
to = sm.groupby("opp_team").agg(n=("y", "size"), W=("y", "sum"), pnl=("pnl", "sum")).sort_values("pnl")
print("Spread+ML, team we faded (worst 8 by pnl):")
print(to.head(8).to_string())
tq = Q[Q.mt == "total"].copy()
long = pd.concat([tq.assign(team=tq.home), tq.assign(team=tq.away)])
tt2 = long.groupby(["team"]).agg(n=("y", "size"), W=("y", "sum"), pnl=("pnl", "sum")).sort_values("pnl")
print("Totals, by team involved (worst 8 by pnl):")
print(tt2.head(8).to_string())
# rest / b2b (v2-v5 store rest_diff)
r5 = Q[Q.rest_diff.notna()].copy()
if len(r5):
    r5["rest"] = np.where(r5.rest_diff == 0, "equal rest", "rest edge exists")
    side_tbl("Rest differential present (v2-v5 only)", r5, "rest")

# ---------------- reproduction of the 11-18 numbers
cut = datetime(2026, 11, 18, 12, tzinfo=UTC)
print("## Reproduction of 2026-11-18 figures (deduped, settled, event start before 11-18 12:00Z)")
for mt, thr in [("total", 0.02), ("spread", 0.02), ("moneyline", 0.10)]:
    for lab, base in [("all rows", dd), ("quoted only", Q)]:
        g = base[(base.mt == mt) & (base.edge >= thr) & (base.start < cut)]
        g2 = base[(base.mt == mt) & (base.edge >= thr)]
        print(
            f"- {mt} edge>={thr:.0%} [{lab}]: to 11-18 {int(g.y.sum())}-{len(g) - int(g.y.sum())}, "
            f"claimed {g.p_model.mean():.1%}, ROI {g.pnl.sum() / g.units.sum():+.1%}; through today "
            f"{int(g2.y.sum())}-{len(g2) - int(g2.y.sum())}, ROI {g2.pnl.sum() / g2.units.sum():+.1%}"
        )

Q.to_pickle("quoted_final.pkl")
fr.to_pickle("flips.pkl")
