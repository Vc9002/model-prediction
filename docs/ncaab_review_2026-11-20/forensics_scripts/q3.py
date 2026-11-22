import pandas as pd

df = pd.read_pickle("ncaab_settled.pkl")
v6 = df[df.model_id.str.endswith("v6")].copy()
v6["day"] = v6.created_at_utc.str[:10]
print(v6.groupby(["dec_market_quote_provenance", "dec_sportsbook"], dropna=False).size())
print(v6.groupby(["f_research_quote_join.status"], dropna=False).size())
print(v6.groupby(["day", "f_margin_engine"], dropna=False).size().unstack())
print(v6.groupby(["day", "dec_calibration_method", "market_type"], dropna=False).size().unstack())
print(v6.groupby(["day", "reason_code", "ledger_tier"], dropna=False).size().unstack())
print(v6.groupby(["day", "model_artifact_hash"], dropna=False).size())
print(v6.groupby(["day", "dec_calibration_artifact_hash"], dropna=False).size())
print("closing nonempty", (v6.dec_closing_implied_probability.fillna("") != "").groupby(v6.day).sum())
v6["pclv"] = pd.to_numeric(v6.dec_probability_clv, errors="coerce")
print(v6.groupby("day").pclv.describe())
print(v6.result.value_counts())
print(v6.groupby("day").edge.describe())
