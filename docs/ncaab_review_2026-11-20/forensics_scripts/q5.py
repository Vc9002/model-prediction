import pandas as pd

df = pd.read_pickle("prep.pkl")
v6 = df[df.model_id.str.endswith("v6")].copy()
print("units==0", (v6.units == 0).sum(), v6[v6.units == 0].groupby(["ledger_tier", "tc"]).size())
v6["line_k"] = v6.line.fillna(-9999)
v6 = v6.sort_values(["tier_rank", "created_at_utc"], ascending=[False, False])
d = v6.drop_duplicates(["event_id", "market_type", "selection", "line_k", "model_id"])
print("before", len(v6), "after", len(d))
print(d.groupby(["regime", "ledger_tier"]).size())
g = d.groupby(["event_id", "market_type"]).size()
print("rows per event-market", g.value_counts())
# both sides?
s = d.groupby(["event_id", "market_type"]).selection.nunique()
print("events with both sides", (s > 1).sum())
print(d.groupby(["market_type", "prov"]).size())
print(d.groupby(["market_type", "qstatus"]).size())
print("placeholder prices", d.qp.round(6).value_counts().head(10))
d.to_pickle("dedup.pkl")
