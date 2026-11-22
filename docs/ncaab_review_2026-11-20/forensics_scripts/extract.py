import json
import sqlite3

import pandas as pd

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
rows = con.execute("select * from ledger_records where sport='ncaab' and status='settled'").fetchall()
cols = [d[0] for d in con.execute("select * from ledger_records limit 0").description]
out = []
for r in rows:
    d = dict(zip(cols, r))
    dec = json.loads(d.pop("decision_payload_json") or "{}")
    fp = json.loads(d.pop("feature_payload_json") or "{}")
    feats = fp.get("features") or {}
    for k, v in dec.items():
        if k in ("model_input_snapshot_json",):
            continue
        if k not in d:
            d["dec_" + k] = v
    for k, v in feats.items():
        d["f_" + k] = v
    out.append(d)
df = pd.DataFrame(out)
df.to_pickle("ncaab_settled.pkl")
print(df.shape)
