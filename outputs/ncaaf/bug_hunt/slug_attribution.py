"""Does each Polymarket-priced NCAAF row's contract belong to the row's own game? Read-only.

For every non-superseded NCAAF row with a market slug, take the archived contract records of that slug
(event_title, event_start_utc) and compare with the row's teams and kickoff.
"""

import csv
import json
import pathlib
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "E:/MP/src")
from model_prediction.cli.settle import _extract_market_slug  # noqa: E402
from model_prediction.domain import parse_utc  # noqa: E402
from model_prediction.learned_forward import _team_matches  # noqa: E402
from model_prediction.ncaaf_prekickoff_capture import ncaaf_settlement_slug  # noqa: E402

HERE = pathlib.Path("E:/MP/outputs/ncaaf/bug_hunt")
ODDS = pathlib.Path("E:/model-prediction-runtime/odds/ncaaf")

meta: dict[str, dict] = {}
for f in sorted(ODDS.glob("*/polymarket_snapshots.jsonl")):
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                s = json.loads(line)
            except ValueError:
                continue
            slug = s.get("market_slug")
            if slug and slug not in meta:
                meta[slug] = {"title": s.get("event_title"), "start": s.get("event_start_utc"), "team": s.get("team")}

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
out = []
agg = Counter()
for pid, tier, status, start, mt, result, pnl, payload in con.execute(
    "select pick_id,ledger_tier,status,event_start_utc,market_type,result,pnl_units,decision_payload_json "
    "from ledger_records where sport='ncaaf' and status in ('settled','archived','open')"
):
    p = json.loads(payload)
    if str(p.get("sportsbook")).lower() != "polymarket_us":
        continue
    slug0 = _extract_market_slug(str(p.get("rationale", "")), p)
    if not slug0:
        agg["no_slug"] += 1
        continue
    slug = ncaaf_settlement_slug(slug0, str(p.get("rationale", "")))
    m = meta.get(slug)
    if m is None:
        agg["slug_not_in_archive"] += 1
        out.append({"pick_id": pid, "slug": slug, "class": "slug_not_in_archive"})
        continue
    title = str(m["title"] or "")
    parts = [x.strip() for x in title.replace(" vs ", " vs. ").split(" vs. ")]
    away_t, home_t = (parts + ["", ""])[:2]
    a_ok = _team_matches(p.get("away_team", ""), away_t) or _team_matches(p.get("away_team", ""), home_t)
    h_ok = _team_matches(p.get("home_team", ""), home_t) or _team_matches(p.get("home_team", ""), away_t)
    try:
        dmin = abs((parse_utc(m["start"]) - parse_utc(start)).total_seconds()) / 60
    except (TypeError, ValueError):
        dmin = None
    cls = "ok"
    if not (a_ok and h_ok):
        cls = "TEAM_MISMATCH"
    if dmin is not None and dmin > 90:
        cls = cls + "+START_DIFF" if cls != "ok" else "START_DIFF"
    agg[cls] += 1
    agg[f"{cls}::{tier}::{status}"] += 1
    out.append(
        {
            "pick_id": pid,
            "tier": tier,
            "status": status,
            "mt": mt,
            "sel": p.get("selection"),
            "line": p.get("line"),
            "row_away": p.get("away_team"),
            "row_home": p.get("home_team"),
            "row_start": start,
            "slug": slug,
            "contract_title": title,
            "contract_start": m["start"],
            "start_diff_min": None if dmin is None else round(dmin),
            "result": result,
            "pnl": pnl,
            "entry": p.get("market_probability_at_decision"),
            "model": p.get("model_version"),
            "class": cls,
        }
    )
keys = sorted({k for r in out for k in r})
with (HERE / "slug_attribution.csv").open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=keys)
    w.writeheader()
    w.writerows(out)
print(json.dumps(dict(sorted(agg.items())), indent=1))
for r in out:
    if r["class"] not in ("ok",):
        print(r)
