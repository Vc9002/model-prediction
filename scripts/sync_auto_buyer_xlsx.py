import json
from pathlib import Path

from model_prediction.portfolio.auto_buyer_ledger import (
    FIELDNAMES,
    _probability_to_american,
    american_to_decimal,
)
from model_prediction.xlsx_ledger import write_xlsx_rows_atomic

REPO = Path(__file__).resolve().parent.parent
j_path = REPO / "data/auto_buyer_ledger.jsonl"
x_path = REPO / "data/auto_buyer_picks.xlsx"

records = [json.loads(line) for line in j_path.read_text(encoding="utf-8").splitlines() if line.strip()]
xlsx_rows = []
for r in records:
    price = float(r.get("entry_price") or 0.50)
    american = _probability_to_american(price)
    dec = american_to_decimal(american)
    mkt_p = float(r.get("market_probability") or 0.5)
    mod_p = float(r.get("model_probability") or 0.5)
    edge = float(r.get("edge") or 0.0)
    units = float(r.get("units") or 1.0)
    pnl_u = float(r.get("pnl_units") or 0.0)

    x_row = {f: "" for f in FIELDNAMES}
    x_row.update(
        {
            "pick_id": str(r.get("pick_id") or r.get("order_id")),
            "created_at_utc": str(r.get("executed_at_utc") or ""),
            "event_start_utc": str(r.get("event_start_utc") or ""),
            "event_id": str(r.get("market_slug") or ""),
            "league": str(r.get("sport") or ""),
            "away_team": str(r.get("away_team") or ""),
            "home_team": str(r.get("home_team") or ""),
            "market_type": str(r.get("market_type") or ""),
            "selection": str(r.get("selection") or ""),
            "line": str(r.get("line") or ""),
            "sportsbook": "polymarket_us",
            "american_odds": str(american),
            "decimal_odds": f"{dec:.4f}",
            "market_implied_probability": f"{mkt_p:.4f}",
            "model_probability": f"{mod_p:.4f}",
            "edge": f"{edge:.4f}",
            "units": f"{units:.2f}",
            "model_version": str(r.get("model_id") or ""),
            "status": str(r.get("status") or "open"),
            "result": str(r.get("result") or ""),
            "away_score": str(r.get("away_score") or ""),
            "home_score": str(r.get("home_score") or ""),
            "pnl_units": f"{pnl_u:.2f}",
            "settled_at_utc": str(r.get("settled_at_utc") or ""),
            "record_type": "decision",
            "decision": "take",
            "reason_code": "AUTO_BUYER_EXECUTION",
            "rationale": str(r.get("rationale") or ""),
            "ledger_schema_version": "4",
        }
    )
    xlsx_rows.append(x_row)

write_xlsx_rows_atomic(x_path, FIELDNAMES, xlsx_rows)
print(f"Synchronized {len(xlsx_rows)} rows to {x_path}")
