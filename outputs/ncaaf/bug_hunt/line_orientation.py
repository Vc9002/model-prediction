"""Independent check of each row's line orientation / side at DECISION time. Read-only.

Polymarket rows: take the archived record of the row's contract observed exactly at the row's
market_quote_observed_at_utc; resolve the row's side independently (spread: the side whose description
equals the row's line, cross-checked with the anchor team; total: Over/Under word; moneyline: nickname or
anchor); check the entry price equals that side's ask (allowing the integer-American-odds rounding) and
not only the other side's ask.
ESPN-consensus spreads: the sign of the row's line from the home perspective must agree with the
favourite implied by the sibling ESPN-consensus moneyline row of the same event, and with
home_spread_current in ncaaf_pregame_capture/espn_pregame_snapshots.jsonl when a capture exists.
"""

import csv
import json
import pathlib
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "E:/MP/src")
sys.path.insert(0, "E:/MP/outputs/ncaaf/bug_hunt")
from analyze_settlement import selected_side, slug_records  # noqa: E402
from model_prediction.cli.settle import _extract_market_slug  # noqa: E402
from model_prediction.ncaaf_prekickoff_capture import ncaaf_settlement_slug  # noqa: E402

HERE = pathlib.Path("E:/MP/outputs/ncaaf/bug_hunt")
con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
rows = []
for pid, tier, status, start, mt, result, pnl, payload in con.execute(
    "select pick_id,ledger_tier,status,event_start_utc,market_type,result,pnl_units,decision_payload_json "
    "from ledger_records where sport='ncaaf' and status in ('settled','archived','open')"
):
    p = json.loads(payload)
    p.update(pick_id=pid, tier=tier, status=status, market_type=mt, result=result, pnl=pnl, event_start_utc=start)
    rows.append(p)

out = []
agg = Counter()
# ---------------------------------------------------------------- Polymarket rows
for p in rows:
    if str(p.get("sportsbook")).lower() != "polymarket_us":
        continue
    slug0 = _extract_market_slug(str(p.get("rationale", "")), p)
    if not slug0:
        agg["pm_no_slug"] += 1
        continue
    slug = ncaaf_settlement_slug(slug0, str(p.get("rationale", "")))
    dq = p.get("market_quote_observed_at_utc")
    recs = [s for _, s in slug_records(slug, p["event_start_utc"]) if s.get("observed_at_utc") == dq]
    entry = float(p.get("market_probability_at_decision") or p.get("decision_raw_implied_probability") or 0)
    row = {"market_type": p["market_type"], "selection": p["selection"], "line": p.get("line"),
           "home_team": p.get("home_team", ""), "away_team": p.get("away_team", "")}
    if not recs:
        cls, how, own_ask, other_ask = "no_decision_record", "", None, None
    else:
        s = recs[0]
        side, how = selected_side(s, row)
        other = {"long": "short", "short": "long"}.get(side or "", None)
        own_ask = (s.get(side) or {}).get("ask") if side else None
        other_ask = (s.get(other) or {}).get("ask") if other else None
        if side is None:
            cls = "side_unresolved"
        elif own_ask is not None and abs(float(own_ask) - entry) <= 0.0011:
            cls = "ok"
        elif other_ask is not None and abs(float(other_ask) - entry) <= 0.0011:
            cls = "ENTRY_IS_OTHER_SIDE_ASK"
        else:
            cls = "ENTRY_MATCHES_NEITHER"
    agg[f"pm::{cls}"] += 1
    out.append({"pick_id": p["pick_id"], "tier": p["tier"], "status": p["status"], "book": "polymarket_us",
                "mt": p["market_type"], "sel": p["selection"], "line": p.get("line"), "away": p.get("away_team"),
                "home": p.get("home_team"), "slug": slug, "entry": entry, "own_ask": own_ask,
                "other_ask": other_ask, "how": how, "class": cls, "result": p["result"], "pnl": p["pnl"],
                "model": p.get("model_version")})

# ---------------------------------------------------------------- ESPN-consensus spreads
pregame = defaultdict(list)
pg = pathlib.Path("E:/model-prediction-runtime/ncaaf_pregame_capture/espn_pregame_snapshots.jsonl")
with pg.open(encoding="utf-8") as fh:
    for line in fh:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        for prov in (d.get("core_odds") or {}).get("providers") or []:
            if prov.get("home_spread_current") is not None:
                pregame[str(d.get("event_id"))].append(float(prov["home_spread_current"]))
ml_home_p = defaultdict(list)
for p in rows:
    if p.get("sportsbook") == "espn_consensus" and p["market_type"] == "moneyline":
        mp = float(p.get("market_implied_probability") or 0)
        if 0 < mp < 1:
            ml_home_p[str(p["event_id"])].append(mp if p["selection"] == "home" else 1 - mp)
for p in rows:
    if p.get("sportsbook") != "espn_consensus" or p["market_type"] != "spread":
        continue
    try:
        line = float(p.get("decision_line") or p.get("line"))
    except (TypeError, ValueError):
        agg["espn_spread_no_line"] += 1
        continue
    home_line = line if p["selection"] == "home" else -line
    eid = str(p["event_id"])
    cls = "ok"
    notes = []
    if ml_home_p.get(eid):
        hp = sum(ml_home_p[eid]) / len(ml_home_p[eid])
        notes.append(f"ml_home_p={hp:.3f}")
        if hp >= 0.60 and home_line > 0:
            cls = "SIGN_VS_ML_FAVOURITE"
        if hp <= 0.40 and home_line < 0:
            cls = "SIGN_VS_ML_FAVOURITE"
    else:
        notes.append("no_sibling_ml")
    if pregame.get(eid):
        hs = pregame[eid]
        notes.append(f"pregame_home_spread={min(hs)}..{max(hs)}")
        if all(h * home_line < 0 and abs(h) >= 2.5 and abs(home_line) >= 2.5 for h in hs):
            cls = cls + "+SIGN_VS_PREGAME" if cls != "ok" else "SIGN_VS_PREGAME"
    agg[f"espn_spread::{cls}"] += 1
    out.append({"pick_id": p["pick_id"], "tier": p["tier"], "status": p["status"], "book": "espn_consensus",
                "mt": "spread", "sel": p["selection"], "line": line, "away": p.get("away_team"),
                "home": p.get("home_team"), "how": ";".join(notes), "class": cls, "result": p["result"],
                "pnl": p["pnl"], "model": p.get("model_version")})

keys = sorted({k for r in out for k in r})
with (HERE / "line_orientation.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=keys)
    w.writeheader()
    w.writerows(out)
print(json.dumps(dict(sorted(agg.items())), indent=1))
for r in out:
    if r["class"] not in ("ok",):
        print({k: r.get(k) for k in ("pick_id", "tier", "status", "mt", "sel", "line", "away", "home", "slug",
                                     "entry", "own_ask", "other_ask", "how", "class", "result", "pnl")})
