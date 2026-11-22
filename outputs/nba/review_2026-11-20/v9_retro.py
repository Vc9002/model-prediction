"""Read-only retro replay of the served NBA v9 engine against closing lines.

Runs NBAStructuralV9Engine (artifact config/models/nba-spread-margin-v9.json) per ET date
with FeatureStore.games_before (same PIT chokepoint as serving), availability = 0 (no
historical injury snapshots), and compares expected margin / total with outcomes and with
the closing line. Writes nothing outside the scratchpad.
"""

import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "E:/MP/src")
from model_prediction.domain import EASTERN
from model_prediction.features.base import FeatureStore
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v9 import NBAStructuralV9Engine

OUT = Path(__file__).with_name("v9_retro_rows.jsonl")
artifact = json.loads(Path("E:/MP/config/models/nba-spread-margin-v9.json").read_text())
engine = NBAStructuralV9Engine.from_artifact(artifact)
fs = FeatureStore(Path("E:/MP/data"))
games = {g.event_id: g for g in fs.load_games("nba")}
lines = [
    json.loads(x)
    for x in open("E:/MP/data/odds/nba/historical_closing_lines/nba_closing_lines_2024_2026.jsonl")
]

by_date = defaultdict(list)
for ln in lines:
    g = games.get(str(ln["event_id"]))
    if g is None or not ln.get("spread") or not ln.get("total"):
        continue
    try:
        sp = float(ln["spread"])
        tot = float(ln["total"])
    except ValueError:
        continue
    away_line = sp if ln["whos_favored"] == "home" else -sp
    d = g.start.astimezone(EASTERN).date().isoformat()
    by_date[d].append((g, away_line, tot))

rows = []
for d in sorted(by_date):
    if d < "2024-11-01":
        continue
    hist = fs.games_before("nba", d)
    ups = [
        UpcomingGame(
            event_id=g.event_id,
            event_start_utc=g.start.isoformat().replace("+00:00", "Z"),
            away_team=g.away_team,
            home_team=g.home_team,
            spread_away_line=al,
            total_line=tot,
        )
        for g, al, tot in by_date[d]
    ]
    preds = engine.predict_games(hist, ups)
    meta = {g.event_id: (g, al, tot) for g, al, tot in by_date[d]}
    per = defaultdict(dict)
    for p in preds:
        per[p.event_id][p.market_type] = p
    for eid, mk in per.items():
        g, al, tot = meta[eid]
        sp = mk.get("spread")
        tp = mk.get("total")
        ml = mk.get("moneyline")
        rows.append(
            {
                "date": d,
                "event_id": eid,
                "season_type": getattr(g, "season_type", ""),
                "home_margin": g.home_score - g.away_score,
                "actual_total": g.home_score + g.away_score,
                "market_home_margin": al,
                "market_total": tot,
                "model_margin": sp.inference_inputs["mean"],
                "model_total": tp.inference_inputs["mean"],
                "p_away_cover": sp.probabilities["away"],
                "p_over": tp.probabilities["over"],
                "p_home_ml": ml.probabilities["home"],
                "min_games": sp.feature_basis["current_season_games_min"],
            }
        )

with OUT.open("w") as fh:
    for r in rows:
        fh.write(json.dumps(r) + "\n")


def ols(x, y):
    mx, my = st.mean(x), st.mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    b = sxy / sxx
    return b, my - b * mx


def corr(x, y):
    return st.correlation(x, y)


def summarize(sub, label):
    n = len(sub)
    if n < 30:
        return
    am = [r["home_margin"] for r in sub]
    mm = [r["model_margin"] for r in sub]
    km = [r["market_home_margin"] for r in sub]
    at = [r["actual_total"] for r in sub]
    mt = [r["model_total"] for r in sub]
    kt = [r["market_total"] for r in sub]
    rmse = lambda a, b: math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))
    print(f"== {label} n={n}")
    print(
        f" margin: model RMSE {rmse(mm, am):.2f} market RMSE {rmse(km, am):.2f}; "
        f"model mean {st.mean(mm):+.2f} market mean {st.mean(km):+.2f} actual mean {st.mean(am):+.2f}"
    )
    b, a = ols(mm, am)
    print(
        f"  actual~model slope {b:.2f} int {a:+.2f}; SD model {st.pstdev(mm):.2f} SD market {st.pstdev(km):.2f}"
    )
    dm = [x - y for x, y in zip(mm, km)]
    resid = [x - y for x, y in zip(am, km)]
    print(f"  corr(model-market, actual-market) {corr(dm, resid):+.3f}; SD(model-market) {st.pstdev(dm):.2f}")
    print(
        f"  residual SD actual-model {st.pstdev([x - y for x, y in zip(am, mm)]):.2f}; actual-market {st.pstdev(resid):.2f}"
    )
    print(
        f" total: model RMSE {rmse(mt, at):.2f} market RMSE {rmse(kt, at):.2f}; "
        f"model mean {st.mean(mt):.2f} market mean {st.mean(kt):.2f} actual mean {st.mean(at):.2f}"
    )
    b, a = ols(mt, at)
    print(f"  actual~model slope {b:.2f}; SD model {st.pstdev(mt):.2f} SD market {st.pstdev(kt):.2f}")
    dt = [x - y for x, y in zip(mt, kt)]
    rt = [x - y for x, y in zip(at, kt)]
    print(
        f"  corr(model-market, actual-market) {corr(dt, rt):+.3f}; SD(model-market) {st.pstdev(dt):.2f}; mean(model-market) {st.mean(dt):+.2f}"
    )
    print(
        f"  residual SD actual-model {st.pstdev([x - y for x, y in zip(at, mt)]):.2f}; actual-market {st.pstdev(rt):.2f}"
    )
    # hit rate of side picks vs closing line (excluding pushes)
    hits = tot = 0
    for r in sub:
        res = r["home_margin"] - r["market_home_margin"]
        if res == 0:
            continue
        pick_home = r["p_away_cover"] < 0.5
        tot += 1
        hits += (res > 0) == pick_home
    th = ttot = 0
    for r in sub:
        res = r["actual_total"] - r["market_total"]
        if res == 0:
            continue
        ttot += 1
        th += (res > 0) == (r["p_over"] >= 0.5)
    under_share = sum(1 for r in sub if r["p_over"] < 0.5) / n
    print(
        f"  ATS hit vs close {hits}/{tot} = {hits / tot:.3f}; O/U hit {th}/{ttot} = {th / ttot:.3f}; under share {under_share:.2f}"
    )


summarize(rows, "all")
for season in ("2024", "2025", "2026"):
    sub = [
        r
        for r in rows
        if (r["date"][:4] == season and r["date"][5:7] >= "09")
        or (r["date"][:4] == str(int(season) + 1) and r["date"][5:7] < "09")
    ]
    summarize(sub, f"season {season}-{int(season) + 1}")
summarize([r for r in rows if r["min_games"] < 15], "early (min_games<15)")
