"""Load every NCAAF ledger row (read-only) into a pickle with parsed payload fields."""
import json
import pickle
import re
import sqlite3

DB = "file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro"
con = sqlite3.connect(DB, uri=True)
con.row_factory = sqlite3.Row
rows = []
for r in con.execute("select * from ledger_records where sport='ncaaf'"):
    d = dict(r)
    try:
        p = json.loads(d.get("decision_payload_json") or "{}")
    except Exception:
        p = {}
    d["p"] = p
    d.pop("feature_payload_json", None)
    d.pop("decision_payload_json", None)
    rat = str(p.get("rationale") or "")
    m = re.search(r"(?:Executable|Indicative) ask ([0-9.]+) \(([^)]+)\)", rat)
    d["rat_ask"] = float(m.group(1)) if m else None
    d["rat_slug"] = m.group(2) if m else None
    m2 = re.search(r"market_slug=([A-Za-z0-9\-]+)", rat)
    d["rat_slug2"] = m2.group(1) if m2 else None
    rows.append(d)
pickle.dump(rows, open("E:/MP/outputs/ncaaf/bug_hunt/rows.pkl", "wb"))
print(len(rows))
