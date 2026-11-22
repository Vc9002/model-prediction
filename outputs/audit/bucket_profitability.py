"""Edge-bucket profitability audit, NBA / NFL / NCAAB / NCAAF (research, read-only on the ledger).

One decision per (sport, event, market, selection, model engine), taken at its FIRST pre-start sighting (main row
first), graded from the final score at that row's own line, 1 unit at that row's own ask. NCAAB rows are labelled by
the engine that priced them (payload features.margin_engine), so v7-under-the-v6-id rows count as v7. Keys with any
archived/removed row are dropped whole. Edge = served probability - ask (same as audit_best_picks.py).

Usage: python outputs/audit/bucket_profitability.py [all|serving|winners]
"""

import collections
import json
import math
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "E:/MP/src")
from model_prediction.display_names import EDGE_BUCKET_LABELS, edge_bucket, model_display_name  # noqa: E402

DB = "file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro"
SPORTS = ("nba", "nfl", "ncaab", "ncaaf")
MARKETS = ("moneyline", "spread", "total")
SERVING = {
    ("nba", "moneyline"): "nba-elo-trend-lr-v5",
    ("nba", "spread"): "nba-spread-margin-v9",
    ("nba", "total"): "nba-total-margin-v9",
    ("nfl", "moneyline"): "nfl-elo-trend-lr-v4",
    ("nfl", "spread"): "nfl-spread-structural-v7",
    ("nfl", "total"): "nfl-total-structural-v7",
    ("ncaab", "moneyline"): "ncaab-moneyline-v7m",
    ("ncaab", "spread"): "ncaab-spread-v7",
    ("ncaab", "total"): "ncaab-total-v6",
    ("ncaaf", "moneyline"): "cfb-structural-v5",
    ("ncaaf", "spread"): "cfb-structural-v5",
    ("ncaaf", "total"): "cfb-structural-v5",
}


SERVING_NAMES = {model_display_name(m, mk) for (sp, mk), m in SERVING.items()}


def bucket(e: float) -> str:
    return edge_bucket(e)


def ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def label(r, p) -> str:
    return model_display_name(r["model_id"] or "", r["market_type"], p)


def load():
    con = sqlite3.connect(DB, uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f"""select pick_id, sport, ledger_tier, event_id, market_type, selection, line, model_id,
        model_probability, market_probability, status, created_at_utc, event_start_utc, decision_payload_json
        from ledger_records where sport in {SPORTS} and market_type in {MARKETS}"""
    ).fetchall()
    removed, scores, first, parsed = set(), {}, {}, []
    for r in rows:
        try:
            p = json.loads(r["decision_payload_json"] or "{}")
        except Exception:
            p = {}
        if r["status"] == "settled":
            try:
                scores.setdefault((r["sport"], r["event_id"]), (float(p["away_score"]), float(p["home_score"])))
            except (KeyError, TypeError, ValueError):
                pass
        key = (r["sport"], r["event_id"], r["market_type"], (r["selection"] or "").lower(), label(r, p))
        if r["status"] in ("archived", "removed"):
            removed.add(key)
            continue
        if r["status"] not in ("open", "superseded", "settled"):
            continue
        c, s = ts(r["created_at_utc"]), ts(r["event_start_utc"])
        if not c or not s or c >= s:
            continue
        parsed.append((c, 0 if r["ledger_tier"] == "main" else 1, key, r, p))
    parsed.sort(key=lambda x: (x[0], x[1]))
    for c, _, key, r, p in parsed:
        first.setdefault(key, (r, p, c))
    dec = []
    for key, (r, p, c) in first.items():
        if key in removed:
            continue
        sc = scores.get((r["sport"], r["event_id"]))
        if not sc:
            continue
        a, h = sc
        sel, m = key[3], r["market_type"]
        try:
            served = p.get("serving_probability")
            served = float(served) if served not in (None, "") else float(r["model_probability"])
            ask = float(r["market_probability"])
        except (TypeError, ValueError):
            continue
        if not (0.01 < ask < 0.99):
            continue
        if m == "moneyline":
            if sel not in ("home", "away"):
                continue
            v = (a - h) if sel == "away" else (h - a)
        elif m == "spread":
            if sel not in ("home", "away") or r["line"] is None:
                continue
            v = ((a - h) if sel == "away" else (h - a)) + float(r["line"])
        else:
            if sel not in ("over", "under") or r["line"] is None:
                continue
            v = (a + h - float(r["line"])) * (1 if sel == "over" else -1)
        res = 1 if v > 0 else 0 if v == 0 else -1
        pnl = (1 / ask - 1) if res == 1 else -1.0 if res == -1 else 0.0
        dec.append(
            dict(sport=key[0], market=m, model=key[4], sel=sel, edge=served - ask, ask=ask, res=res, pnl=pnl, t=c,
                 verified=p.get("market_quote_provenance") == "decision_time_executable_quote")
        )
    return dec


def stats(ds):
    g = [d for d in ds if d["res"] != 0]
    n = len(g)
    if not n:
        return None
    w = sum(d["res"] == 1 for d in g)
    pn = [d["pnl"] for d in g]
    roi = sum(pn) / n
    sd = math.sqrt(sum((x - roi) ** 2 for x in pn) / max(n - 1, 1))
    se = sd / math.sqrt(n)
    srt = sorted(g, key=lambda d: d["t"])
    h1, h2 = srt[: n // 2], srt[n // 2 :]
    r1 = sum(d["pnl"] for d in h1) / len(h1) if h1 else float("nan")
    r2 = sum(d["pnl"] for d in h2) / len(h2) if h2 else float("nan")
    return dict(n=n, w=w, l=n - w, units=sum(pn), roi=roi, lo=roi - 1.96 * se, hi=roi + 1.96 * se, r1=r1, r2=r2,
                price=sum(d["ask"] for d in g) / n, first=str(srt[0]["t"].date()), last=str(srt[-1]["t"].date()))


def main():
    dec = load()
    groups = collections.defaultdict(list)
    for d in dec:
        base = (d["sport"], d["market"], d["model"])
        groups[base + (bucket(d["edge"]),)].append(d)
        groups[base + ("ALL",)].append(d)
        if d["edge"] > 0:
            groups[base + ("ALL edge>0",)].append(d)
        if d["verified"]:
            groups[base + (bucket(d["edge"]) + " [verified]",)].append(d)
            if d["edge"] > 0:
                groups[base + ("ALL edge>0 [verified]",)].append(d)
    out = [(k, s) for k, ds in groups.items() if (s := stats(ds))]
    with open("E:/MP/outputs/audit/bucket_profitability.json", "w", encoding="utf-8") as f:
        json.dump([dict(sport=k[0], market=k[1], model=k[2], bucket=k[3], **s) for k, s in out], f, indent=1)
    print("decisions graded:", len(dec), " verified-quote:", sum(d["verified"] for d in dec))

    def show(title, flt, sort=None):
        print("\n" + title)
        print(f"{'model':24}{'bucket':18}{'n':>5}{'W-L':>10}{'units':>8}{'ROI':>8}"
              f"{'95% CI':>13}{'half1':>7}{'half2':>7}{'ask':>5}  dates")
        order = {lab: i for i, lab in enumerate(EDGE_BUCKET_LABELS)}
        default_sort = sort is None
        rows_ = [x for x in out if flt(x)]
        rows_.sort(key=(lambda x: (x[0][:3], order.get(x[0][3].split(" [")[0], -1), x[0][3])) if default_sort else sort)
        for k, s in rows_:
            ci = "[%+.0f,%+.0f]" % (s["lo"] * 100, s["hi"] * 100)
            print(f"{k[2][:23]:24}{k[3]:18}{s['n']:5d}{s['w']:>6}-{s['l']:<3}{s['units']:+8.1f}"
                  f"{s['roi'] * 100:+7.1f}%{ci:>13}{s['r1'] * 100:+6.0f}%{s['r2'] * 100:+6.0f}%{s['price'] * 100:4.0f}c"
                  f"  {s['first']}..{s['last']}")

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "all":
        show("MODEL TOTALS, n>=20", lambda x: x[0][3].startswith("ALL") and x[1]["n"] >= 20)
    elif mode == "serving":
        show("CURRENT SERVING MODELS by edge bucket",
             lambda x: x[0][2] in SERVING_NAMES
             and not x[0][3].startswith("ALL"))
    elif mode == "winners":
        show("PROFITABLE CANDIDATES: n>=30, ROI>0, both halves >0",
             lambda x: x[1]["n"] >= 30 and x[1]["roi"] > 0 and x[1]["r1"] > 0 and x[1]["r2"] > 0,
             sort=lambda x: -x[1]["lo"])


if __name__ == "__main__":
    main()
