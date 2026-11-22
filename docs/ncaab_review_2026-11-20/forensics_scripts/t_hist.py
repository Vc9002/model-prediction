from lib import *

df = pd.read_pickle("prep.pkl")
h = df[~df.model_id.str.endswith("v6")].copy()
h["line_k"] = h.line.fillna(-9999)
h = h.sort_values(["tier_rank", "created_at_utc"], ascending=[False, False]).drop_duplicates(
    ["event_id", "market_type", "selection", "line_k", "model_id"]
)
h["sp"] = pd.to_numeric(h.dec_serving_probability, errors="coerce").fillna(h.mp)
h["dec_odds"] = pd.to_numeric(h.dec_decision_decimal_odds, errors="coerce").fillna(1 / h.qp)
h["ret1"] = np.where(h.y == 1, h.dec_odds - 1, -1.0)
print(md(tbl(h, ["model_id", "market_type"])))
ml = h[(h.model_id == "ncaab-tempo-v3") & (h.market_type == "moneyline")].copy()
ml["band"] = pd.cut(ml.qp, [0, 0.2, 0.35, 0.5, 0.65, 0.8, 1], right=False)
print(md(tbl(ml, ["selection", "band"])))
print(ml.groupby("selection")[["qp", "sp", "edge"]].describe().T)
print(ml.prov.value_counts(), ml.dec_sportsbook.value_counts())
ml2 = h[(h.model_id == "ncaab-tempo-v2") & (h.market_type == "moneyline")]
print(md(tbl(ml2, ["selection"])))
print("A-B check: neutral", ml.f_neutral_site.value_counts(dropna=False))
