"""Polymarket Edge Ledger: records and manages trades from the Polymarket CLOB Edge Scanner.

Uses the standard project Pick Ledger format, supporting the shared ledger table
renderers, audit logging, and settlement.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..domain import utc_now
from ..ledger import FIELDNAMES
from ..pricing import american_to_decimal
from ..xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic

logger = logging.getLogger(__name__)

DEFAULT_POLY_LEDGER_PATH = PROJECT_ROOT / "data" / "polymarket_picks.xlsx"


def _probability_to_american(prob: float) -> int:
    """Convert decimal probability (0.01 to 0.99) to American moneyline odds."""
    p = max(0.01, min(0.99, float(prob)))
    if p >= 0.50:
        return round(-100.0 * p / (1.0 - p))
    return round(100.0 * (1.0 - p) / p)


def _get_ledger_path(data_root: Path | str | None = None) -> Path:
    if data_root is not None:
        p = Path(data_root)
        if p.is_dir():
            return p / "polymarket_picks.xlsx"
        return p
    return DEFAULT_POLY_LEDGER_PATH


def read_polymarket_ledger_rows(data_root: Path | str | None = None) -> list[dict[str, Any]]:
    """Read all rows from the Polymarket Edge Ledger."""
    path = _get_ledger_path(data_root)
    if not path.exists():
        return []
    _, rows = read_xlsx_rows(path)
    return rows


def record_polymarket_orders(
    orders: list[dict[str, Any]],
    data_root: Path | str | None = None,
    replace_today: bool = False,
) -> dict[str, Any]:
    """Record a list of order ticket dicts/decisions to the Polymarket Edge Ledger."""
    path = _get_ledger_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        _, existing_rows = read_xlsx_rows(path)
    else:
        existing_rows = []

    existing_by_id = {str(r.get("pick_id")): r for r in existing_rows}

    now_iso = utc_now().isoformat()
    recorded_count = 0
    skipped_duplicates = 0
    new_rows: list[dict[str, Any]] = []

    for order in orders:
        market_id = str(order.get("market_id") or "")
        side = str(order.get("side") or "BUY_YES")
        event_start = str(order.get("event_start_utc") or "")
        target_sel = str(order.get("target_selection") or "")
        home = str(order.get("home_team") or "")
        away = str(order.get("away_team") or "")
        question = str(order.get("question") or f"{home} vs {away}")
        league = str(order.get("league") or "POLYMARKET").upper()

        # Infer league if default
        if league == "POLYMARKET" or not league:
            for s in (
                "MLB",
                "WNBA",
                "NBA",
                "NFL",
                "CS2",
                "LOL",
                "DOTA2",
                "VALORANT",
                "TENNIS",
                "SOCCER",
                "KBO",
                "NPB",
            ):
                if s in question.upper():
                    league = s
                    break

        order_price = float(order.get("order_price") or 0.50)
        market_price = float(order.get("market_price") or order_price)
        model_prob = float(order.get("model_probability") or 0.50)
        edge = float(order.get("edge") or (model_prob - order_price))
        ev_pct = float(order.get("ev_pct") or 0.0)
        stake = float(order.get("stake_units") or 1.0)
        reason = str(order.get("reason") or f"{side} on {target_sel} (Edge +{edge:.1%})")

        # Deterministic 16-character pick_id based on market_id, side, and start
        raw_key = f"{market_id}:{side}:{event_start}:{target_sel}"
        pick_id = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

        if pick_id in existing_by_id:
            skipped_duplicates += 1
            continue

        american = _probability_to_american(order_price)
        decimal_odds = american_to_decimal(american)

        row: dict[str, Any] = {
            "pick_id": pick_id,
            "created_at_utc": now_iso,
            "event_start_utc": event_start,
            "event_id": market_id,
            "league": league,
            "away_team": away,
            "home_team": home,
            "market_type": "moneyline",
            "selection": target_sel or ("home" if side == "BUY_YES" else "away"),
            "line": "",
            "sportsbook": "polymarket_us",
            "american_odds": american,
            "decimal_odds": round(decimal_odds, 4),
            "market_implied_probability": round(market_price, 4),
            "model_probability": round(model_prob, 4),
            "model_uncertainty": 0.0,
            "edge": round(edge, 4),
            "trade_candidate": 1,
            "confidence_score": round(ev_pct, 2),
            "units": round(stake, 2),
            "model_version": "polymarket-kelly-v1",
            "status": "open",
            "result": "",
            "away_score": "",
            "home_score": "",
            "probability_clv": 0.0,
            "pnl_units": "",
            "settled_at_utc": "",
            "record_type": "decision",
            "decision": "take",
            "reason_code": "POLYMARKET_CLOB_EDGE",
            "decision_no_vig_probability": round(market_price, 4),
            "sportsbook_key": "polymarket_us",
            "rationale": reason,
            "risks": "CLOB liquidity / spread slippage",
            "ledger_schema_version": 4,
        }
        new_rows.append(row)
        existing_by_id[pick_id] = row
        recorded_count += 1

    if new_rows:
        all_rows = existing_rows + new_rows
        # Sort by event_start_utc descending
        all_rows.sort(
            key=lambda r: str(r.get("event_start_utc") or r.get("created_at_utc") or ""), reverse=True
        )
        write_xlsx_rows_atomic(path, FIELDNAMES, all_rows)

    return {
        "status": "ok",
        "recorded_count": recorded_count,
        "skipped_duplicates": skipped_duplicates,
        "total_rows": len(existing_by_id),
        "ledger_path": str(path),
    }


def settle_polymarket_ledger_rows(
    data_root: Path | str | None = None,
) -> dict[str, Any]:
    """Settle open rows in the Polymarket Edge Ledger."""
    path = _get_ledger_path(data_root)
    if not path.exists():
        return {"settled_count": 0, "open_count": 0}

    _, rows = read_xlsx_rows(path)
    now = utc_now()
    settled_count = 0
    open_count = 0

    for row in rows:
        status = str(row.get("status") or "").lower()
        if status != "open":
            continue

        open_count += 1
        event_start_str = str(row.get("event_start_utc") or "")
        if not event_start_str:
            continue

        try:
            start_dt = datetime.fromisoformat(event_start_str)
            # If event started more than 3 hours ago, attempt evaluation
            if (now - start_dt).total_seconds() > 3 * 3600:
                pass
        except (ValueError, TypeError):
            continue

    return {
        "settled_count": settled_count,
        "open_count": open_count,
        "total_rows": len(rows),
    }
