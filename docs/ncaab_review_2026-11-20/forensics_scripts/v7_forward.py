import collections
import json
import sqlite3
import sys

import numpy as np

sys.path.insert(0, "E:/MP/src")
from model_prediction.features.base import FeatureStore

fs = FeatureStore("E:/MP/data")
games = {g.event_id: g for g in fs.load_games("ncaab")}
byteams = {}
for g in games.values():
    byteams[(g.home_team, g.away_team, g.start.date().isoformat())] = g
c = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
rows = c.execute(
    "select ledger_tier,market_type,selection,line,model_probability,market_probability,result,pnl_units,units,feature_payload_json,event_start_utc,status,decision_payload_json from ledger_records where sport like '%ncaab%' and created_at_utc>='2026-11-13'"
).fetchall()
agg = collections.defaultdict(lambda: dict(n=0, w=0, l=0, p=0, pnl=0.0, mp=0.0, mk=0.0, brier=0.0, quoted=0))
resid = collections.defaultdict(list)
seen = set()
for tier, mt, sel, line, mp, mk, res, pnl, units, fp, est, status, dp in rows:
    j = json.loads(fp)
    f = j["features"]
    s = j.get("model_input_snapshot", {})
    eng = f.get("margin_engine", "n/a") if mt != "total" else "v6tot"
    if res not in ("win", "loss", "push", "WIN", "LOSS", "PUSH"):
        continue
    quoted = f.get("research_quote_join.status") == "matched"
    k = (tier, mt, eng)
    a = agg[k]
    a["n"] += 1
    r = res.lower()
    a[r[0]] += 1
    a["pnl"] += pnl or 0
    a["mp"] += mp
    a["mk"] += mk or 0
    a["quoted"] += quoted
    if r != "push":
        a["brier"] += (mp - (r == "win")) ** 2
    # residual of margin
    if mt == "spread":
        key = (s.get("event_id"),)
        if key in seen:
            continue
        seen.add(key)
        hd = est[:10]
        g = None
        for d in (hd,):
            pass
        # search
        for gg in games.values():
            if (
                gg.home_team == s.get("home_team")
                and gg.away_team == s.get("away_team")
                and abs(
                    (gg.start.timestamp())
                    - __import__("datetime").datetime.fromisoformat(est.replace("Z", "+00:00")).timestamp()
                )
                < 86400
            ):
                g = gg
                break
        if g is None:
            continue
        mean = float(f["expected_margin"])
        samp = int(f["current_season_sample"])
        b = "0" if samp == 0 else "1-2" if samp < 3 else "3-7" if samp < 8 else "8+"
        resid[(eng, b)].append((g.margin - mean, float(f["margin_scale"])))
for k, a in sorted(agg.items()):
    n = a["n"]
    print(
        k,
        "n",
        n,
        "W-L-P",
        a["w"],
        a["l"],
        a["p"],
        "pnl %.2f" % a["pnl"],
        "mean_model %.3f" % (a["mp"] / n),
        "mean_mkt %.3f" % (a["mk"] / n),
        "hit %.3f" % (a["w"] / max(1, a["w"] + a["l"])),
        "brier %.4f" % (a["brier"] / max(1, a["w"] + a["l"])),
        "quoted",
        a["quoted"],
    )
for k, v in sorted(resid.items()):
    r = np.array([x[0] for x in v])
    sc = np.mean([x[1] for x in v])
    print(
        "resid",
        k,
        "n",
        len(r),
        "bias %.2f" % r.mean(),
        "sd %.2f" % r.std(),
        "mean scale %.2f" % sc,
        "implied t8 sd %.2f" % (sc * np.sqrt(8 / 6)),
    )
