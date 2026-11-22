"""Fetch the ESPN CFB scoreboard exactly as settlement requests it (dates, limit=100, no groups)."""
import json, sqlite3, time, pathlib, httpx, sys
sys.path.insert(0, "E:/MP/src")
from model_prediction.domain import EASTERN, parse_utc
OUT = pathlib.Path("E:/MP/outputs/ncaaf/bug_hunt/espn_scoreboard_cache")
c = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
days = sorted({parse_utc(r[0]).astimezone(EASTERN).date().isoformat() for r in c.execute(
    "select distinct event_start_utc from ledger_records where sport='ncaaf' and status in ('settled','archived','open')")})
print(len(days))
cl = httpx.Client(timeout=20, headers={"User-Agent": "research-client"})
for d in days:
    p = OUT / f"{d}.json"
    if p.exists():
        continue
    r = cl.get("https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
               params={"dates": d.replace("-", ""), "limit": 100})
    if r.status_code == 200:
        p.write_text(r.text)
    else:
        print(d, r.status_code)
    time.sleep(0.4)
print("done")
