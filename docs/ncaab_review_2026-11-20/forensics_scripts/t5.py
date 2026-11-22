from lib import *

d = load()
out = []
lg = lambda p: np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))
try:
    import statsmodels.api as sm

    def lfit(g, label):
        X = np.column_stack([np.ones(len(g)), lg(g.qp.values), lg(g.sp.values) - lg(g.qp.values)])
        r = sm.Logit(g.y.values, X).fit(disp=0)
        return {
            "set": label,
            "n": len(g),
            "b_logit(mkt)": round(r.params[1], 3),
            "b_model-mkt": round(r.params[2], 3),
            "se": round(r.bse[2], 3),
            "p": round(r.pvalues[2], 3),
        }

    rows = [lfit(d[d.market_type == "moneyline"], "ML all")] + [
        lfit(d[(d.market_type == "moneyline") & (d.regime == r)], "ML regime " + r) for r in "ABC"
    ]
    rows.append(lfit(d[(d.market_type == "moneyline") & (d.regime != "A")], "ML B+C"))
    out.append(
        "### Moneyline: logit(y) ~ logit(mkt) + [logit(served) - logit(mkt)]  (positive b_model-mkt = model adds info beyond market)\n"
        + md(pd.DataFrame(rows))
    )
except ImportError:
    out.append("statsmodels missing")
# Spreads in points, home perspective
s = d[d.market_type == "spread"].copy()
s["model_edge_pts"] = s.exp_margin - s.mkt_home_margin  # model home margin minus market-implied home margin
s["actual_resid"] = s.margin - s.mkt_home_margin
s["sel_model_pts"] = s.model_edge_pts * s.sel_home  # selected side's projected cover margin
s["sel_actual_pts"] = s.actual_resid * s.sel_home


def pts(g, mcol, acol):
    x = g[mcol].values
    y = g[acol].values
    b = np.polyfit(x, y, 1)[0] if len(g) > 2 and x.std() > 0 else np.nan
    return pd.Series(
        {
            "n": len(g),
            "mean model-line": round(x.mean(), 2),
            "mean actual-line": round(y.mean(), 2),
            "corr": round(float(np.corrcoef(x, y)[0, 1]), 3) if len(g) > 2 else np.nan,
            "slope actual~model": round(b, 3),
            "sign agree%": round(100 * np.mean(np.sign(x) == np.sign(y)), 1),
            "MAE model vs actual": round(np.mean(np.abs(x - y)), 2),
            "MAE line vs actual (=|actual-line|)": round(np.mean(np.abs(y)), 2),
            "noise": "n<30" if len(g) < 30 else "",
        }
    )


out.append(
    "### Spreads, home-perspective points: model margin - market-implied margin vs actual margin - market-implied margin\n"
    + md(s.groupby("regime").apply(lambda g: pts(g, "model_edge_pts", "actual_resid"), include_groups=False))
)
out.append(
    "### Spreads, selected-side points (model projected cover margin vs actual cover margin), by regime x side\n"
    + md(
        s.groupby(["regime", "selection"]).apply(
            lambda g: pts(g, "sel_model_pts", "sel_actual_pts"), include_groups=False
        )
    )
)
s["fav"] = np.where(s.line < 0, "fav", "dog")
out.append(
    "### Spreads selected-side points by fav/dog (all regimes)\n"
    + md(
        s.groupby(["fav", "selection"]).apply(
            lambda g: pts(g, "sel_model_pts", "sel_actual_pts"), include_groups=False
        )
    )
)
# neutral site
s["neutral"] = s.f_neutral_site.astype(str)
out.append(
    "### Spreads home-perspective by neutral_site flag\n"
    + md(
        s.groupby(["neutral"]).apply(lambda g: pts(g, "model_edge_pts", "actual_resid"), include_groups=False)
    )
)
# totals
t = d[d.market_type == "total"].copy()
t["model_pts"] = t.exp_total - t.line
t["actual_pts"] = t.tot - t.line
out.append(
    "### Totals: model total - line vs actual total - line, by regime x side\n"
    + md(
        t.groupby(["regime", "selection"]).apply(
            lambda g: pts(g, "model_pts", "actual_pts"), include_groups=False
        )
    )
)
out.append(
    "### Totals: all sides, by regime\n"
    + md(t.groupby(["regime"]).apply(lambda g: pts(g, "model_pts", "actual_pts"), include_groups=False))
)
t["raw_total"] = pd.to_numeric(t.f_raw_model_total, errors="coerce")
tt = t.dropna(subset=["raw_total"])
tt = tt.assign(raw_pts=tt.raw_total - tt.line)
out.append(
    "### Totals: RAW (pre-credibility) model total - line, rows with raw total recorded\n"
    + md(tt.groupby(["regime"]).apply(lambda g: pts(g, "raw_pts", "actual_pts"), include_groups=False))
)
# mean levels
out.append(
    "### Totals level check: mean model total, mean line, mean actual total\n"
    + md(
        t.groupby("regime")
        .agg(n=("y", "size"), model=("exp_total", "mean"), line=("line", "mean"), actual=("tot", "mean"))
        .round(2)
    )
)
out.append(
    "### Spreads level check (home perspective): mean model home margin, mean market-implied home margin, mean actual home margin\n"
    + md(
        s.groupby(["regime", "neutral"])
        .agg(
            n=("y", "size"),
            model=("exp_margin", "mean"),
            market=("mkt_home_margin", "mean"),
            actual=("margin", "mean"),
            abs_model=("exp_margin", lambda x: x.abs().mean()),
            abs_market=("mkt_home_margin", lambda x: x.abs().mean()),
            abs_actual=("margin", lambda x: x.abs().mean()),
        )
        .round(2)
    )
)
s.to_pickle("spreads.pkl")
t.to_pickle("totals.pkl")
open("t5.md", "w").write("\n\n".join(out))
print("\n\n".join(out))
