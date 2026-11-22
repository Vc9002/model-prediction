"""Latent-risk checks on the archive and on v5+/challenger rows (current serving code era)."""
import collections
import pickle
import re
from datetime import datetime, timezone

OUT = "E:/MP/outputs/ncaaf/bug_hunt"
Q = pickle.load(open(f"{OUT}/quotes.pkl", "rb"))
rows = pickle.load(open(f"{OUT}/rows.pkl", "rb"))


def ts(s):
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    if re.match(r".*T\d\d:\d\d\+00:00$", s):
        s = s.replace("+00:00", ":00+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)


SLUG_RE = re.compile(r"^(aec|asc|tsc)-cfb-(.+?)-(\d{4}-\d{2}-\d{2})(?:-(.*))?$")


def full(slug):
    m = SLUG_RE.match(slug or "")
    rest = (m.group(4) or "") if m else "x"
    return bool(m) and (rest == "" or re.fullmatch(r"(pos|neg)-\d+(pt\d+)?|total-\d+(pt\d+)?", rest))


c = collections.Counter()
for q in Q:
    o, s = ts(q["obs"]), ts(q["pm_start"])
    pre = o is not None and s is not None and o < s
    if q["state"] != "MARKET_STATE_OPEN":
        c[("non_open", q["state"], "pre_kickoff" if pre else "post_kickoff", "full" if full(q["slug"]) else "partial")] += 1
    if q["tv"] is not True:
        c[("tv_false", "pre_kickoff" if pre else "post_kickoff")] += 1
    if q["la"] is not None and q["sa"] is not None and full(q["slug"]) and pre:
        ov = q["la"] + q["sa"]
        c[("overround_full_prekick", "<=1.02" if ov <= 1.02 else "<=1.05" if ov <= 1.05 else "<=1.10" if ov <= 1.10 else ">1.10")] += 1
        if q["la"] == 0.5 and q["lb"] is not None and q["lb"] < 0.4:
            c[("placeholder_half_ask_long_bid_lt40", q["mt"])] += 1
for k, v in sorted(c.items(), key=lambda kv: str(kv[0])):
    print(v, k)

# v5+ rows: selected quote age vs freshest capture of a sibling full-game rung, and chosen-side bid/ask gap
by_digest = {q["digest"]: q for q in Q}
by_event = collections.defaultdict(list)
for q in Q:
    by_event[(q["pm_event_id"], q["mt"])].append(q)
res = collections.Counter()
stale_rows = []
gap_rows = []
for r in rows:
    if r["status"] in ("superseded", "removed", "archived"):
        continue
    if r["model_id"] not in ("cfb-structural-v5", "cfb-structural-v6.2", "cfb-structural-v7"):
        continue
    q = by_digest.get(r.get("market_snapshot_record_id") or "")
    if q is None:
        continue
    created = ts(r["created_at_utc"])
    qo = ts(q["obs"])
    sibs = [x for x in by_event[(q["pm_event_id"], q["mt"])] if full(x["slug"]) and ts(x["obs"]) and ts(x["obs"]) <= created]
    newest = max((ts(x["obs"]) for x in sibs), default=qo)
    lag_h = (newest - qo).total_seconds() / 3600
    res[("selected_vs_newest_sibling_lag", "0" if lag_h < 0.05 else "<1h" if lag_h < 1 else "1-3h" if lag_h < 3 else ">=3h")] += 1
    if lag_h >= 1:
        stale_rows.append((r["pick_id"], r["ledger_tier"], r["model_id"], r["created_at_utc"], q["slug"], q["obs"], newest.isoformat(), round(lag_h, 1)))
    # chosen side ask/bid gap
    sel = r["selection"]
    ask = r.get("rat_ask")
    if ask is None:
        continue
    if abs(ask - (q["la"] or -1)) < 1e-9:
        bid = q["lb"]
    elif abs(ask - (q["sa"] or -1)) < 1e-9:
        bid = q["sb"]
    else:
        bid = None
    if bid is not None:
        g = ask - bid
        res[("chosen_side_ask_minus_bid", "<=1c" if g <= 0.0101 else "<=3c" if g <= 0.0301 else "<=10c" if g <= 0.1001 else ">10c")] += 1
        if g > 0.0301:
            gap_rows.append((r["pick_id"], r["ledger_tier"], r["model_id"], q["slug"], sel, ask, bid, r["edge"], r["result"]))
for k, v in sorted(res.items()):
    print(v, k)
print("stale-rung rows (selected quote >=1h older than newest sibling capture):", len(stale_rows))
for s in stale_rows[:40]:
    print("  ", s)
print("wide chosen side rows:", len(gap_rows))
for s in gap_rows[:40]:
    print("  ", s)
