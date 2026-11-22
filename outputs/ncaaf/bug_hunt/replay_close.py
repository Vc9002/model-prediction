"""Replay the CURRENT settlement close lookup on every settled Polymarket-priced NCAAF row and
compare it with the independent close from analyze_settlement.py (settlement_rows.csv). Read-only."""

import csv
import json
import pathlib
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, "E:/MP/src")
from model_prediction.cli.settle import (  # noqa: E402
    _close_after_decision_kwargs,
    _closing_probability_for_moneyline_pick,
    _extract_market_slug,
)
from model_prediction.ncaaf_prekickoff_capture import ncaaf_settlement_slug  # noqa: E402

HERE = pathlib.Path("E:/MP/outputs/ncaaf/bug_hunt")
ROOT = pathlib.Path("E:/model-prediction-runtime")
indep = {r["pick_id"]: r for r in csv.DictReader(open(HERE / "settlement_rows.csv", encoding="utf-8"))}
con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
out = []
agg = Counter()
for pid, start, mt, payload in con.execute(
    "select pick_id,event_start_utc,market_type,decision_payload_json from ledger_records "
    "where sport='ncaaf' and status in ('settled','archived')"
):
    p = json.loads(payload)
    if str(p.get("sportsbook")).lower() != "polymarket_us" or p.get("void_reason"):
        continue
    slug = _extract_market_slug(str(p.get("rationale", "")), p)
    if slug is None:
        agg["no_slug"] += 1
        continue
    slug = ncaaf_settlement_slug(slug, str(p.get("rationale", "")))
    try:
        prob, _ = _closing_probability_for_moneyline_pick(
            ROOT,
            "ncaaf",
            slug,
            start,
            p.get("home_team", ""),
            p.get("away_team", ""),
            p.get("selection", ""),
            line=p.get("line"),
            **_close_after_decision_kwargs(p, "ncaaf"),
        )
    except Exception as e:  # noqa: BLE001
        prob = f"ERR {e}"
    ind = indep.get(pid, {})
    ic = ind.get("indep_close") or None
    ic = float(ic) if ic not in (None, "") else None
    entry = float(ind["entry"]) if ind.get("entry") else None
    if prob is None and ic is None:
        k = "both_none"
    elif isinstance(prob, str):
        k = "error"
    elif prob is None:
        k = "code_none_indep_has"
    elif ic is None:
        k = "code_has_indep_none"
    elif abs(prob - ic) < 1e-9:
        k = "agree"
    else:
        k = "DISAGREE"
    agg[k] += 1
    agg[f"{k}::{mt}"] += 1
    out.append(
        {
            "pick_id": pid,
            "mt": mt,
            "sel": p.get("selection"),
            "line": p.get("line"),
            "slug": slug,
            "entry": entry,
            "recorded_close": p.get("closing_implied_probability"),
            "replay_close": prob,
            "indep_close": ic,
            "indep_how": ind.get("indep_close_how"),
            "n_valid": ind.get("n_valid_close_quotes"),
            "class": k,
            "settled_at": ind.get("settled_at"),
            "model": ind.get("model"),
        }
    )
with (HERE / "close_replay.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0]))
    w.writeheader()
    w.writerows(out)
print(json.dumps(dict(sorted(agg.items())), indent=1))
