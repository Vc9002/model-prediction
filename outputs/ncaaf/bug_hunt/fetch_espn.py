"""Fetch ESPN college-football summary for every NCAAF ledger event_id (cached)."""
import json, sqlite3, time, pathlib, httpx
OUT = pathlib.Path("E:/MP/outputs/ncaaf/bug_hunt/espn_summary_cache")
c = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
ids = sorted({r[0] for r in c.execute(
    "select distinct event_id from ledger_records where sport='ncaaf' and status in ('settled','archived','open','voided')")})
print(len(ids))
cl = httpx.Client(timeout=20, headers={"User-Agent": "research-client"})
for i, eid in enumerate(ids):
    p = OUT / f"{eid}.json"
    if p.exists():
        continue
    for attempt in range(3):
        try:
            r = cl.get("https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary",
                       params={"event": eid})
            if r.status_code == 200:
                d = r.json()
                keep = {"header": d.get("header"), "gameInfo": d.get("gameInfo")}
                p.write_text(json.dumps(keep))
                break
            print(eid, r.status_code)
        except Exception as e:
            print(eid, e)
        time.sleep(2)
    time.sleep(0.25)
    if i % 50 == 0:
        print(i, flush=True)
print("done")
