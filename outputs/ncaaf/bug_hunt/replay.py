"""Replay the CURRENT NCAAF quote selectors on each live row's decision-time archive; compare with what the row used."""
from __future__ import annotations

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
out = []
c = collections.Counter()
for r in rows:
    if r["status"] in ("superseded", "removed", "archived"):
        continue
    if r["model_id"] in ("cfb-spread-v1", "cfb-total-v1", "college-football-v1"):
        continue
    p = r["p"]
    created = ts(r["created_at_utc"])
    start = ts(r["event_start_utc"])
    iso = start.astimezone(ET).date().isoformat()
    path = Path(f"E:/model-prediction-runtime/odds/ncaaf/{iso}/polymarket_snapshots.jsonl")
    key = (iso, r["created_at_utc"])
    if key not in cache:
        cache[key] = _latest_capture_per_market(_load_decision_snapshot_rows(path, iso, created))
    snap = cache[key]
    away, home = p.get("away_team") or "", p.get("home_team") or ""
    mt = r["market_type"]
    if mt == "moneyline":
        q, side = _match_cfb_moneyline(away, home, snap)
    elif mt == "spread":
        q, side = _match_cfb_spread(away, home, snap)
    else:
        q, side = _match_cfb_total(away, home, snap), None
    used = r.get("rat_slug2") or r.get("rat_slug") or ""
    if mt == "moneyline":
        used = re.sub(r"-(home|away)$", "", used)
    now_slug = q.get("market_slug") if q else None
    sb = p.get("sportsbook")
    status = (
        "both_none" if not used and not now_slug
        else "same" if used == now_slug
        else "row_espn_now_pm" if not used and now_slug
        else "row_pm_now_none" if used and not now_slug
        else "different"
    )
    c[(r["model_id"], sb, status)] += 1
    if status != "same" and status != "both_none":
        out.append(
            dict(pick_id=r["pick_id"], tier=r["ledger_tier"], model=r["model_id"], created=r["created_at_utc"],
                 away=away, home=home, mt=mt, sel=r["selection"], line=r["line"], sb=sb, used=used,
                 now=now_slug, now_title=q.get("event_title") if q else None, now_side=side,
                 now_obs=q.get("observed_at_utc") if q else None, status=status, result=r["result"])
        )
with open(f"{OUT}/replay_diff.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
    w.writeheader()
    w.writerows(out)
for k, v in sorted(c.items()):
    print(v, k)
