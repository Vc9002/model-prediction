"""Read-only peek at NBA ledger rows (2026-27 season) for the code audit."""

import json
import sqlite3
from collections import defaultdict

DB = "file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro"
c = sqlite3.connect(DB, uri=True)
rows = c.execute(
    "select ledger_tier, market_type, model_id, selection, line, model_probability, market_probability, edge,"
    " units, decision, status, result, pnl_units, event_start_utc, created_at_utc, decision_payload_json,"
    " feature_payload_json from ledger_records where sport in ('NBA','nba') and event_start_utc >= '2026-10-01'"
).fetchall()
print("rows", len(rows))
agg = defaultdict(lambda: [0, 0, 0, 0.0, 0.0])  # n, settled, wins, pnl, sum_edge
for r in rows:
    tier, mkt, mid, sel, line, mp, mkp, edge, units, dec, status, res, pnl = r[:13]
    k = (tier, mkt, mid)
    a = agg[k]
    a[0] += 1
    a[4] += float(edge or 0)
    if res in ("win", "loss", "WIN", "LOSS", "won", "lost"):
        a[1] += 1
        a[2] += 1 if str(res).lower().startswith("w") else 0
        a[3] += float(pnl or 0)
for k, a in sorted(agg.items()):
    print(
        k, "n", a[0], "settled", a[1], "wins", a[2], "pnl", round(a[3], 2), "avg_edge", round(a[4] / a[0], 4)
    )

# result vocab
print({r[11] for r in rows})
# event_start_utc formats per market
fmt = defaultdict(set)
for r in rows:
    s = r[13] or ""
    fmt[r[1]].add(s[19:] if len(s) > 19 else "?")
print(dict(fmt))

# Over/under split and side split for Main
side = defaultdict(lambda: [0, 0, 0.0])
for r in rows:
    if r[1] in ("total", "spread", "moneyline") and r[11] is not None:
        res = str(r[11]).lower()
        if res.startswith("w") or res.startswith("l"):
            s = side[(r[0], r[1], r[3])]
            s[0] += 1
            s[1] += 1 if res.startswith("w") else 0
            s[2] += float(r[12] or 0)
for k, v in sorted(side.items()):
    print("side", k, v[0], v[1], round(v[2], 2))

# Look at a feature/decision payload sample for totals
for r in rows:
    if r[1] == "total":
        dp = json.loads(r[15] or "{}")
        fp = json.loads(r[16] or "{}")
        print("decision keys", list(dp)[:40])
        print("feature keys", list(fp)[:40])
        break
