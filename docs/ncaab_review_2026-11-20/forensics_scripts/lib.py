import numpy as np
import pandas as pd


def load():
    d = pd.read_pickle("dedup.pkl")
    num = lambda s: pd.to_numeric(s, errors="coerce")
    d["sp"] = num(d.dec_serving_probability).fillna(d.mp)
    d["rawp"] = d.mp_raw.fillna(d.mp)
    d["dec_odds"] = num(d.dec_decision_decimal_odds).fillna(1 / d.qp)
    d["ret1"] = np.where(d.y == 1, d.dec_odds - 1, -1.0)
    d["sel_home"] = np.where(d.selection == "home", 1, -1)
    # spreads: market-implied home margin
    d["mkt_home_margin"] = np.where(
        d.market_type == "spread", np.where(d.selection == "home", -d.line, d.line), np.nan
    )
    return d


def summ(g):
    n = len(g)
    W = int(g.y.sum())
    L = n - W
    u = g.units.sum()
    p = g.pnl_units.sum()
    return pd.Series(
        {
            "n": n,
            "W-L": f"{W}-{L}",
            "hit%": round(100 * W / n, 1) if n else np.nan,
            "units": round(u, 1),
            "PnL": round(p, 2),
            "ROI%": round(100 * p / u, 1) if u > 0 else np.nan,
            "flat1U ROI%": round(100 * g.ret1.mean(), 1),
            "mean served p%": round(100 * g.sp.mean(), 1),
            "mean mkt p%": round(100 * g.qp.mean(), 1),
            "noise": "n<30" if n < 30 else "",
        }
    )


def tbl(d, by):
    return d.groupby(by, observed=True).apply(summ, include_groups=False)


def md(t):
    t = t.reset_index() if not isinstance(t.index, pd.RangeIndex) else t
    cols = [str(c) for c in t.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in t.itertuples(index=False):
        lines.append(
            "| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) else str(v) for v in r) + " |"
        )
    return chr(10).join(lines)


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def logloss(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
