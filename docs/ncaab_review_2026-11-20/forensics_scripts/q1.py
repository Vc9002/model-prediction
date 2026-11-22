import sqlite3

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
cur = con.cursor()
for ts in ["2026-11-09", "2026-11-13", "2026-11-18"]:
    for mt in ["spread", "total", "moneyline"]:
        r = cur.execute(
            "select * from ledger_records where sport='ncaab' and status='settled' and market_type=? and created_at_utc>? and model_id like '%v6' order by created_at_utc limit 1",
            (mt, ts),
        ).fetchone()
        if not r:
            continue
        cols = [d[0] for d in cur.description]
        d = dict(zip(cols, r))
        print(
            "=====",
            ts,
            mt,
            {k: d[k] for k in cols if k not in ("decision_payload_json", "feature_payload_json")},
        )
        print("DEC", d["decision_payload_json"][:6000])
        print("FEAT", d["feature_payload_json"][:4000])
