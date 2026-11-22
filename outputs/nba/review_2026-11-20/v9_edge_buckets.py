"""Hit rate vs closing line, bucketed by the model's implied edge over a 50% fair price."""

import json
import sys

rows = [json.loads(l) for l in open(sys.argv[1])]
buckets = [(0, 0.035), (0.035, 0.05), (0.05, 0.10), (0.10, 1)]
for market in ("spread", "total"):
    out = {b: [0, 0] for b in buckets}
    for r in rows:
        if market == "spread":
            p_side = max(r["p_away_cover"], 1 - r["p_away_cover"])
            res = r["home_margin"] - r["market_home_margin"]
            if res == 0:
                continue
            pick_home = r["p_away_cover"] < 0.5
            hit = (res > 0) == pick_home
        else:
            p_side = max(r["p_over"], 1 - r["p_over"])
            res = r["actual_total"] - r["market_total"]
            if res == 0:
                continue
            hit = (res > 0) == (r["p_over"] >= 0.5)
        edge = p_side - 0.5
        for b in buckets:
            if b[0] <= edge < b[1]:
                out[b][0] += 1
                out[b][1] += hit
    n = sum(v[0] for v in out.values())
    for b, (k, h) in out.items():
        print(
            f"{market} edge {b[0] * 100:.1f}-{b[1] * 100:.1f}%: n={k} ({k / n * 100:.0f}% of games) hit {h / k * 100:.1f}%"
            if k
            else f"{market} {b}: n=0"
        )
