import json
import sqlite3

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
cur = con.cursor()
for ts in ["2026-11-13", "2026-11-18"]:
    for mt in ["spread", "total", "moneyline"]:
        r = cur.execute(
            "select decision_payload_json, feature_payload_json, line, selection, created_at_utc, model_artifact_hash, market_snapshot_hash from ledger_records where sport='ncaab' and status='settled' and market_type=? and created_at_utc>? and model_id like '%v6' order by created_at_utc limit 1",
            (mt, ts),
        ).fetchone()
        d = json.loads(r[0])
        f = json.loads(r[1])
        print("=====", ts, mt, r[2:])
        print({k: v for k, v in d.items() if v not in ("", None) and k not in ("model_input_snapshot_json",)})
        print("FEATS", f.get("features"))
