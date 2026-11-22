from lib import *

d = load()
p = d[
    (d.model_id == "ncaab-spread-v6")
    & (d.market_type == "spread")
    & (d.created_at_utc >= "2026-11-12")
    & (d.edge >= 0.05)
    & (d.edge < 0.08)
].copy()
out = [
    "### Pinned forward check: ncaab-spread-v6 spread, edge [5%,8%) on served edge, created >= 2026-11-12, deduped (main>flat)"
]
out.append(md(pd.DataFrame([summ(p)])))
out.append(
    f"verified-quote (archived_two_sided_bbo) rows: {int((p.prov == 'archived_two_sided_bbo').sum())}; research-join matched: {int((p.qstatus == 'matched').sum())}; zero-unit placeholder rows: {int((p.units == 0).sum())}; pre-fix rows (created 11-12 before 07:00Z): {int((p.created_at_utc < '2026-11-12T07:00:00Z').sum())}; tiers: {p.ledger_tier.value_counts().to_dict()}"
)
out.append("By regime/engine:\n" + md(tbl(p, ["regime"])))
out.append("By side:\n" + md(tbl(p, ["selection"])))
# sensitivity: raw-prob edge
p2 = d[(d.model_id == "ncaab-spread-v6") & (d.market_type == "spread") & (d.created_at_utc >= "2026-11-12")]
p2 = p2[((p2.rawp - p2.qp) >= 0.05) & ((p2.rawp - p2.qp) < 0.08)]
out.append("Sensitivity, bucket on RAW (pre-calibration) edge instead:\n" + md(pd.DataFrame([summ(p2)])))
open("t7.md", "w").write("\n\n".join(out))
print("\n\n".join(out))
print(
    p[
        [
            "created_at_utc",
            "selection",
            "line",
            "sp",
            "qp",
            "edge",
            "units",
            "result",
            "engine",
            "dec_calibration_method",
        ]
    ]
    .sort_values("created_at_utc")
    .to_string()
)
