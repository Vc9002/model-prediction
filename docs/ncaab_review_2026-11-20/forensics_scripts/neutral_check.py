import collections
import glob
import json
import sqlite3
import sys

esp = {}
for p in sorted(glob.glob("E:/MP/data/raw/ncaab/2026-11-*/scores_ncaab.json")):
    d = json.load(open(p, encoding="utf-8"))
    for e in d.get("events", []):
        c = e["competitions"][0]
        t = {x["homeAway"]: x["team"]["displayName"] for x in c["competitors"]}
        esp[(t.get("home"), t.get("away"))] = bool(c.get("neutralSite"))
        esp.setdefault((t.get("away"), t.get("home")), ("swapped", bool(c.get("neutralSite"))))
c = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
rows = c.execute(
    "select event_id,feature_payload_json,created_at_utc from ledger_records where sport like '%ncaab%' and created_at_utc>=? and market_type in ('spread','moneyline')",
    (sys.argv[1],),
).fetchall()
cnt = collections.Counter()
seen = set()
bad = []
for eid, fp, ca in rows:
    j = json.loads(fp)
    f = j["features"]
    s = j.get("model_input_snapshot", {})
    home, away = s.get("home_team"), s.get("away_team")
    if eid in seen:
        continue
    seen.add(eid)
    led = f.get("neutral_site") in (True, "True")
    e = esp.get((home, away))
    if e is None:
        cnt["no_espn_match"] += 1
        bad.append((home, away, "nomatch", led))
        continue
    if isinstance(e, tuple):
        cnt[("swapped", e[1], led)] += 1
        bad.append((home, away, "swapped", e[1], led))
        continue
    cnt[("espn", e, "ledger", led)] += 1
    if e != led:
        bad.append((home, away, e, led, ca))
print(cnt)
for b in bad[:30]:
    print(b)
