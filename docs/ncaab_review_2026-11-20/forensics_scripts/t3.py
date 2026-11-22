from lib import *

d = load()
out = []
d["qset"] = np.where(
    d.prov == "archived_two_sided_bbo",
    "verified_bbo",
    np.where(d.qstatus == "matched", "matched_indicative_priced", "unverified"),
)
out.append("### quote sets (n) by market\n" + md(pd.crosstab(d.market_type, d.qset)))
# matched-indicative re-priced at exec ask
rows = []


def score(g, label, mkt_col="qp"):
    y = g.y.values
    r = {
        "set": label,
        "n": len(g),
        "hit%": round(100 * y.mean(), 1),
        "mean served p%": round(100 * g.sp.mean(), 1),
        "mean raw p%": round(100 * g.rawp.mean(), 1),
        "mean mkt p%": round(100 * g[mkt_col].mean(), 1),
        "Brier served": round(brier(g.sp.values, y), 4),
        "Brier raw": round(brier(g.rawp.values, y), 4),
        "Brier mkt": round(brier(g[mkt_col].values, y), 4),
        "LL served": round(logloss(g.sp.values, y), 4),
        "LL raw": round(logloss(g.rawp.values, y), 4),
        "LL mkt": round(logloss(g[mkt_col].values, y), 4),
    }
    e = (g.sp - g[mkt_col]).values
    r["corr(edge,y)"] = round(float(np.corrcoef(e, y)[0, 1]), 3) if len(g) > 2 and e.std() > 0 else np.nan
    r["noise"] = "n<30" if len(g) < 30 else ""
    return r


for mt in ["moneyline", "spread", "total"]:
    g = d[d.market_type == mt]
    rows.append({"market": mt, **score(g, "all rows")})
    for reg in ["A", "B", "C"]:
        rows.append({"market": mt, **score(g[g.regime == reg], f"all, regime {reg}")})
    rows.append({"market": mt, **score(g[g.qset == "verified_bbo"], "verified_bbo")})
    m = g[g.qset == "matched_indicative_priced"].copy()
    if len(m):
        rows.append({"market": mt, **score(m, "matched, at recorded (indicative) price")})
        rows.append({"market": mt, **score(m, "matched, re-priced at exec ask", "exec_ask")})
    rows.append({"market": mt, **score(g[g.qset == "unverified"], "unverified (indicative/blank)")})
t = pd.DataFrame(rows).set_index(["market", "set"])
out.append("### Brier / log loss: served vs raw vs market price, selected side\n" + md(t))
# Calibration by served-p bucket per market
d["pb"] = pd.cut(d.sp, [0, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.9, 1.0], right=False)


def cal(g):
    return pd.Series(
        {
            "n": len(g),
            "mean served p%": round(100 * g.sp.mean(), 1),
            "mean raw p%": round(100 * g.rawp.mean(), 1),
            "mean mkt p%": round(100 * g.qp.mean(), 1),
            "hit%": round(100 * g.y.mean(), 1),
            "noise": "n<30" if len(g) < 30 else "",
        }
    )


out.append(
    "### Calibration by served-probability bucket (all v6 deduped)\n"
    + md(d.groupby(["market_type", "pb"], observed=True).apply(cal, include_groups=False))
)
d2 = d[d.regime != "A"]
out.append(
    "### Calibration by served-probability bucket, post-fix only (B+C)\n"
    + md(d2.groupby(["market_type", "pb"], observed=True).apply(cal, include_groups=False))
)
# placeholder prices
pc = d.qp.round(6).isin([0.5, 0.509804, 0.490196, 0.519231, 0.480769])
out.append(
    f"### exact placeholder-like prices (0.5 / 0.509804 / 0.490196 / 0.519231 / 0.480769): {int(pc.sum())} of {len(d)}; by market: "
    + str(d[pc].market_type.value_counts().to_dict())
    + f"; zero-unit placeholder rows: {int((d.units == 0).sum())}"
)
# spread/total market price spread
out.append(
    "### market price dispersion by market (std, pp): "
    + str((100 * d.groupby("market_type").qp.std()).round(2).to_dict())
)
open("t3.md", "w").write("\n\n".join(out))
print("\n\n".join(out))
