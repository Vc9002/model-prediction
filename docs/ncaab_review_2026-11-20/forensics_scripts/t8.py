from lib import *

d = load()
out = []
s = d[(d.market_type == "spread") & (d.regime != "A")].copy()
s["edge_sign"] = np.where(s.edge > 0, "served edge>0", "served edge<=0")
out.append(
    "### Spreads B+C by calibration method x served-edge sign\n"
    + md(tbl(s, ["regime", "dec_calibration_method", "edge_sign"]))
)
s["rawedge"] = pd.cut(
    s.rawp - s.qp, [-1, 0, 0.035, 0.05, 0.08, 0.12, 1], labels=["<=0", "0-3.5", "3.5-5", "5-8", "8-12", "12+"]
)
out.append("### Spreads B+C by RAW edge bucket\n" + md(tbl(s, ["rawedge"])))
c = d.copy()
c["pclv"] = pd.to_numeric(c.dec_probability_clv, errors="coerce")
cc = c.dropna(subset=["pclv"])
out.append(
    f"### CLV field: rows with probability_clv recorded: {len(cc)} of {len(c)}; |clv|<0.005: {int((cc.pclv.abs() < 0.005).sum())}; mean clv {cc.pclv.mean():.4f}; rows where closing price == decision price (to 1c): {int(((pd.to_numeric(cc.dec_closing_implied_probability, errors='coerce') - cc.qp).abs() < 0.006).sum())}"
)
dup = d.groupby(["event_id", "market_type"]).filter(lambda g: len(g) > 1)
out.append(
    f"### Correlated rows: {dup.groupby(['event_id', 'market_type']).ngroups} event-markets carry 2 settled rows at different lines (same side), {len(dup)} rows; no event-market has both sides."
)
open("t8.md", "w").write("\n\n".join(out))
print("\n\n".join(out))
