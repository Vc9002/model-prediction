from lib import *

d = load()
d["post"] = np.where(d.regime == "A", "A (pre-fix)", "B+C (post-fix)")
d["ebkt"] = pd.cut(
    d.edge, [-1, 0, 0.035, 0.05, 0.08, 0.12, 1], labels=["<=0", "0-3.5", "3.5-5", "5-8", "8-12", "12+"]
)
d["sbkt"] = pd.cut(d["sample"], [-1, 3, 7, 1000], labels=["0-3", "4-7", "8+"])
out = []
s = d[d.market_type == "spread"].copy()
s["fav"] = np.where(s.line < 0, "fav", "dog")
s["abs_line"] = pd.cut(s.line.abs(), [0, 5, 10, 16, 100], right=False, labels=["<5", "5-10", "10-16", "16+"])
out.append("### Spread: home vs away\n" + md(tbl(s, ["post", "selection"])))
out.append("### Spread: fav vs dog x side\n" + md(tbl(s, ["post", "fav", "selection"])))
out.append("### Spread: |line|\n" + md(tbl(s, ["post", "abs_line"])))
t = d[d.market_type == "total"]
out.append("### Total: over vs under\n" + md(tbl(t, ["post", "selection"])))
m = d[d.market_type == "moneyline"].copy()
m["fav"] = np.where(m.qp > 0.5, "fav", "dog")
m["band"] = pd.cut(
    m.qp, [0, 0.35, 0.5, 0.65, 0.8, 1.01], right=False, labels=["<35%", "35-50%", "50-65%", "65-80%", "80%+"]
)
out.append("### Moneyline: fav vs dog x side\n" + md(tbl(m, ["post", "fav", "selection"])))
out.append("### Moneyline: market price band\n" + md(tbl(m, ["post", "band"])))
out.append("### Edge bucket (served edge) x market\n" + md(tbl(d, ["market_type", "post", "ebkt"])))
out.append(
    "### Current-season games (min of the two teams) x market\n" + md(tbl(d, ["market_type", "post", "sbkt"]))
)
out.append("### Sample distribution (n) by regime\n" + md(pd.crosstab(d.regime, d.sbkt)))
open("t4.md", "w").write("\n\n".join(out))
print("\n\n".join(out))
