"""Re-summarize saved retro rows (sign-corrected market margin) without recomputing the engine."""

import json
import statistics as st
import sys

rows = [json.loads(l) for l in open(sys.argv[1])]
if sys.argv[2:] == ["flip"]:
    for r in rows:
        r["market_home_margin"] = -r["market_home_margin"]
exec(
    open("v9_retro.py")
    .read()
    .split("def ols")[0]
    .split("with OUT.open")[0]
    .split("rows = []")[0]
    .split("OUT =")[0]
)  # imports only


def ols(x, y):
    mx, my = st.mean(x), st.mean(y)
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sum((a - mx) ** 2 for a in x)
    return b, my - b * mx


src = open("v9_retro.py").read()
body = src[src.index("def corr") :]
exec(body.replace("summarize(rows", "summarize(rows"))
