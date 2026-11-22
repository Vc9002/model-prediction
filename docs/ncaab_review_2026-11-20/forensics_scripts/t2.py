from lib import *

d = load()
out = []
out.append("### v6 settled, deduped: regime x market\n" + md(tbl(d, ["regime", "market_type"])))
d["edge_sign"] = np.where(d.edge > 0, "edge>0", "edge<=0")
out.append("### regime x market x edge sign\n" + md(tbl(d, ["regime", "market_type", "edge_sign"])))
out.append("### regime x market x trade_candidate\n" + md(tbl(d, ["regime", "market_type", "tc"])))
out.append(
    "### quote provenance by regime x market (n)\n"
    + md(pd.crosstab([d.regime, d.market_type], d.prov.replace("", "(blank)")))
)
out.append(
    "### calibration method by regime x market (n)\n"
    + md(pd.crosstab([d.regime, d.market_type], d.dec_calibration_method.fillna("")))
)
open("t2.md", "w").write("\n\n".join(out))
print("\n\n".join(out))
