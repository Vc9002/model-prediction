from lib import *

d = load()
s = pd.read_pickle("spreads.pkl")
s = s[s.f_neutral_site.astype(str) == "False"]
for r, g in s.groupby("regime"):
    bm = np.polyfit(g.exp_margin, g.margin, 1)[0]
    bk = np.polyfit(g.mkt_home_margin, g.margin, 1)[0]
    print(
        r,
        len(g),
        "slope actual~model",
        round(bm, 3),
        "slope actual~market",
        round(bk, 3),
        "corr model",
        round(np.corrcoef(g.exp_margin, g.margin)[0, 1], 3),
        "corr mkt",
        round(np.corrcoef(g.mkt_home_margin, g.margin)[0, 1], 3),
    )
print("zero-unit qp", d[d.units == 0].qp.value_counts().to_dict())
print("exactly 0.5", (d.qp == 0.5).sum())
df = pd.read_pickle("prep.pkl")
v3 = df[(df.model_id == "ncaab-tempo-v3") & (df.market_type == "moneyline")]
print(v3.groupby("ledger_tier").y.agg(["sum", "count"]))
import sqlite3

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
print(
    con.execute(
        "select count(*) from ledger_records where sport='ncaab' and decision_payload_json like '%decision_time_executable_quote%'"
    ).fetchone()
)
print(
    con.execute(
        "select count(*) from ledger_records where sport='ncaab' and json_extract(feature_payload_json,'$.features.margin_engine')='v6'"
    ).fetchone()
)
print("calibrated rows", (d.dec_calibration_method.fillna("identity") != "identity").sum())
