"""EXPLORATORY, READ-ONLY: what the served v9 engine actually said this season (2026-27).

Pulls nba-total-margin-v9 / nba-spread-margin-v9 rows from the runtime ledger (mode=ro), keeps
the latest-created row per event, joins final scores from games.jsonl, and reports bias of the
served expected_total / expected_margin vs result and vs the Polymarket line it was priced at.
This is descriptive monitoring of already-served output; nothing is fit on it.
"""

from __future__ import annotations

import json
import sqlite3
import statistics as st
from pathlib import Path

REPO = Path("e:/MP")
games = {}
for line in (REPO / "data/processed/nba/games.jsonl").open(encoding="utf-8"):
    r = json.loads(line)
    games[str(r["event_id"])] = r

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
out = {}
for market, model, feat in (
    ("total", "nba-total-margin-v9", "expected_total"),
    ("spread", "nba-spread-margin-v9", "expected_margin"),
):
    latest: dict[str, tuple] = {}
    for ev, created, sel, line, fp in con.execute(
        "select event_id, created_at_utc, selection, line, feature_payload_json from ledger_records "
        "where lower(sport)='nba' and market_type=? and model_id=? and event_start_utc>='2026-10-20'",
        (market, model),
    ):
        try:
            f = json.loads(fp)["features"][feat]
            f = float(f)
        except Exception:
            continue
        if ev not in latest or created > latest[ev][0]:
            latest[ev] = (created, sel, float(line), f)
    rows = []
    for ev, (_c, sel, line, f) in latest.items():
        g = games.get(str(ev))
        if not g or g.get("status") != "completed":
            continue
        if market == "total":
            actual = g["home_score"] + g["away_score"]
            rows.append((f, line, actual))
        else:
            actual = g["home_score"] - g["away_score"]
            # ledger line is from the selected side; convert to home-margin line
            hm_line = line if sel == "away" else -line
            rows.append((f, hm_line, actual))
    n = len(rows)
    if not n:
        continue
    m_res = [a - f for f, _l, a in rows]
    l_res = [a - ln for _f, ln, a in rows]
    gap = [ln - f for f, ln, _a in rows]
    out[market] = {
        "n_events": n,
        "mean_actual_minus_model": round(st.mean(m_res), 2),
        "mean_actual_minus_line": round(st.mean(l_res), 2),
        "mean_line_minus_model": round(st.mean(gap), 2),
        "rmse_model": round((sum(x * x for x in m_res) / n) ** 0.5, 2),
        "rmse_line": round((sum(x * x for x in l_res) / n) ** 0.5, 2),
        "corr(line-model, actual-line)": round(st.correlation([-x for x in gap], l_res), 3)
        if n > 3
        else None,
    }
print(json.dumps(out, indent=2))
(Path(__file__).resolve().parent / "live_served.json").write_text(json.dumps(out, indent=2))
