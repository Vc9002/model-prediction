"""Print the last archived quotes of a row's close contract next to ESPN's kickoff. Read-only."""

import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, "E:/MP/src")
from model_prediction.cli.settle import _extract_market_slug  # noqa: E402
from model_prediction.ncaaf_prekickoff_capture import ncaaf_settlement_slug  # noqa: E402

ODDS = pathlib.Path("E:/model-prediction-runtime/odds/ncaaf")
c = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
for pid in sys.argv[1:]:
    r = c.execute(
        "select event_id,event_start_utc,decision_payload_json from ledger_records where pick_id=? "
        "and status in ('settled','archived','open')",
        (pid,),
    ).fetchone()
    p = json.loads(r[2])
    print(pid, r[0], r[1], p["home_team"], "|", p["away_team"], p["selection"], p["line"])
    print("  decision quote", p["market_quote_observed_at_utc"], "settled", p["settled_at_utc"],
          "entry", p["market_probability_at_decision"], "close", p["closing_implied_probability"])
    e = json.load(open(f"E:/MP/outputs/ncaaf/bug_hunt/espn_summary_cache/{r[0]}.json"))["header"]["competitions"][0]
    print("  espn", e["date"], [(t["homeAway"], t["team"]["displayName"], t.get("score")) for t in e["competitors"]])
    slug = ncaaf_settlement_slug(_extract_market_slug(p["rationale"], p), p["rationale"])
    print("  slug", slug)
    recs = []
    for f in ODDS.glob("*/polymarket_snapshots.jsonl"):
        if f.parent.name < "2026-10-19" and r[1] > "2026-10-25":
            continue
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if slug not in line:
                    continue
                s = json.loads(line)
                if s["market_slug"] == slug:
                    recs.append((s["observed_at_utc"], f.parent.name, s.get("event_start_utc"), s.get("market_state"),
                                 s.get("team"), s.get("line"), s["long"].get("description"), s["long"].get("ask"),
                                 s["short"].get("description"), s["short"].get("ask")))
    recs.sort()
    for x in recs[-6:]:
        print("   ", x)
