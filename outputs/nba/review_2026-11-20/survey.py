import collections
import json
import sqlite3

c = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
rows = c.execute(
    "select ledger_tier,model_id,market_type,status,reason_code,decision_payload_json,feature_payload_json,event_start_utc,created_at_utc from ledger_records where sport='nba'"
).fetchall()
cnt = collections.Counter()
for t, m, mt, st, rc, dp, fp, es, ca in rows:
    if st != "settled":
        continue
    d = json.loads(dp or "{}")
    cnt[
        (
            t,
            m.replace("nba-", ""),
            rc,
            d.get("market_quote_provenance"),
            d.get("market_quote_source"),
            d.get("market_quote_reconstructed"),
            d.get("record_type"),
            bool(d.get("closing_implied_probability")),
        )
    ] += 1
for k, v in sorted(cnt.items()):
    print(v, k)
# feature keys
fk = collections.Counter()
for t, m, mt, st, rc, dp, fp, es, ca in rows:
    f = json.loads(fp or "{}")
    mis = f.get("model_input_snapshot") or {}
    fk[
        (
            m,
            tuple(sorted((f.get("features") or {}).keys())),
            tuple(sorted(k for k in mis.keys() if k != "artifact")),
        )
    ] += 1
for k, v in fk.items():
    print(v, k)
