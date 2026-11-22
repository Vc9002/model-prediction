"""ESPN-consensus rows (all models): compare each row's line/price with the Polymarket book the CURRENT selectors find
in the row's decision-time window (when one exists). Flags sign flips and large gaps."""
import collections
import csv
import pickle
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "E:/MP/src")
from model_prediction.models.college_football import (  # noqa: E402
    _latest_capture_per_market,
    _load_decision_snapshot_rows,
    _match_cfb_moneyline,
    _match_cfb_spread,
    _match_cfb_total,
)

OUT = "E:/MP/outputs/ncaaf/bug_hunt"
ET = ZoneInfo("America/New_York")
rows = pickle.load(open(f"{OUT}/rows.pkl", "rb"))


def ts(s):
    s = str(s).replace("Z", "+00:00")
    if re.match(r".*T\d\d:\d\d\+00:00$", s):
        s = s.replace("+00:00", ":00+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)


cache = {}
c = collections.Counter()
out = []
for r in rows:
    if r["status"] in ("superseded", "removed", "archived"):
        continue
    p = r["p"]
    if p.get("sportsbook") == "polymarket_us":
        continue
    created, start = ts(r["created_at_utc"]), ts(r["event_start_utc"])
    iso = start.astimezone(ET).date().isoformat()
    key = (iso, r["created_at_utc"])
    if key not in cache:
        path = Path(f"E:/model-prediction-runtime/odds/ncaaf/{iso}/polymarket_snapshots.jsonl")
        cache[key] = _latest_capture_per_market(_load_decision_snapshot_rows(path, iso, created))
    snap = cache[key]
    away, home = p.get("away_team") or "", p.get("home_team") or ""
    mt, sel = r["market_type"], r["selection"]
    mkt = float(r["market_probability"])
    line = None if r["line"] is None else float(r["line"])
    flag = None
    if mt == "moneyline":
        q, side = _match_cfb_moneyline(away, home, snap)
        if q is None:
            c[(r["model_id"], "no_book")] += 1
            continue
        pm_sel = q["long"]["ask"] if sel == side else q["short"]["ask"]
        gap = mkt - pm_sel
        # A swapped side shows up as the row's price sitting near the OTHER side's ask.
        pm_other = q["short"]["ask"] if sel == side else q["long"]["ask"]
        if abs(mkt - pm_other) + 0.05 < abs(gap):
            flag = "ml_side_swapped?"
        elif abs(gap) > 0.10:
            flag = "ml_gap>10c"
        detail = (round(pm_sel, 3), round(gap, 3))
    elif mt == "spread":
        q, side = _match_cfb_spread(away, home, snap)
        if q is None:
            c[(r["model_id"], "no_book")] += 1
            continue
        pm_home_line = q["line"] if side == "home" else -q["line"]
        row_home_line = line if sel == "home" else -line
        gap = row_home_line - pm_home_line
        if row_home_line * pm_home_line < 0 and abs(pm_home_line) >= 3:
            flag = "spread_sign_flip"
        elif abs(gap) > 3:
            flag = "spread_gap>3"
        detail = (pm_home_line, row_home_line)
    else:
        q = _match_cfb_total(away, home, snap)
        if q is None:
            c[(r["model_id"], "no_book")] += 1
            continue
        gap = line - q["line"]
        if abs(gap) > 4:
            flag = "total_gap>4"
        detail = (q["line"], line)
    c[(r["model_id"], "book", flag or "consistent")] += 1
    if flag:
        out.append(dict(pick_id=r["pick_id"], tier=r["ledger_tier"], model=r["model_id"], created=r["created_at_utc"],
                        away=away, home=home, mt=mt, sel=sel, line=line, mkt=mkt, flag=flag, detail=str(detail),
                        pm_slug=q.get("market_slug"), pm_obs=q.get("observed_at_utc"), result=r["result"],
                        pnl=r["pnl_units"], units=r["units"]))
for k, v in sorted(c.items()):
    print(v, k)
if out:
    with open(f"{OUT}/espn_vs_book.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
print(len(out))
for o in out[:60]:
    print(o["pick_id"], o["tier"], o["model"], o["created"][:10], o["away"][:20], "@", o["home"][:20], o["mt"], o["sel"],
          o["line"], o["mkt"], o["flag"], o["detail"], o["result"])
