import numpy as np
import pandas as pd

df = pd.read_pickle("ncaab_settled.pkl")
num = lambda s: pd.to_numeric(s, errors="coerce")
df["hs"] = num(df.dec_home_score)
df["as_"] = num(df.dec_away_score)
df["margin"] = df.hs - df.as_
df["tot"] = df.hs + df.as_
df["y"] = (df.result == "win").astype(int)


# recompute result
def recompute(r):
    if r.market_type == "moneyline":
        sm = r.margin if r.selection == "home" else -r.margin
        return "win" if sm > 0 else ("loss" if sm < 0 else "push")
    if r.market_type == "spread":
        sm = r.margin if r.selection == "home" else -r.margin
        v = sm + r.line
    else:
        v = (r.tot - r.line) if r.selection == "over" else (r.line - r.tot)
    return "win" if v > 0 else ("loss" if v < 0 else "push")


df["res2"] = df.apply(recompute, axis=1)
df["mismatch"] = df.res2 != df.result
df["mp"] = df.model_probability
df["qp"] = df.market_probability
df["mp_raw"] = num(df.dec_model_probability_raw)
df["sample"] = num(df["f_current_season_sample"])
df["exp_margin"] = num(df.f_expected_margin)
df["exp_total"] = num(df.f_expected_total)
df["exec_ask"] = num(df["f_research_quote_join.executable_ask"])
df["ind_ask"] = num(df["f_research_quote_join.indicative_ask"])
df["prov"] = df.dec_market_quote_provenance.fillna("")
df["qstatus"] = df["f_research_quote_join.status"].fillna("")
df["engine"] = df.f_margin_engine.fillna("")
df["tc"] = df.dec_trade_candidate
c = df.created_at_utc
df["regime"] = np.where(c < "2026-11-12T07:00:00Z", "A", np.where(c < "2026-11-16T05:53:18Z", "B", "C"))
# for spread/ML use row-level engine for C
sm = df.market_type != "total"
df.loc[sm & (df.engine == "v7"), "regime"] = "C"
df.loc[sm & (df.engine != "v7") & (df.regime == "C"), "regime"] = "B?"
df["tier_rank"] = (df.ledger_tier == "main").astype(int)
df.to_pickle("prep.pkl")
v6 = df[df.model_id.str.endswith("v6")]
print("mismatch by model", df.groupby("model_id").mismatch.sum())
print(
    df[df.mismatch][
        ["model_id", "market_type", "selection", "line", "hs", "as_", "result", "res2", "pnl_units"]
    ].head(20)
)
print("pushes", (df.res2 == "push").sum(), (df.pnl_units == 0).sum())
print("spread line sign", v6[v6.market_type == "spread"].line.describe())
print((v6[v6.market_type == "spread"].line < 0).sum())
print(v6.regime.value_counts())
