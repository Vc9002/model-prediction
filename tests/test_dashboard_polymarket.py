"""Unit test for Polymarket Dashboard endpoint integration."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import Mock

from model_prediction.dashboard.routes import Handler


def test_dashboard_polymarket_scan_endpoint():
    handler = Handler.__new__(Handler)
    handler.path = "/api/polymarket/scan?sport=esports&min_edge=0.03&bankroll=1500"
    handler.headers = {}
    handler.wfile = BytesIO()

    # Mock send_response, send_header, end_headers
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()

    handler.do_GET()

    response_bytes = handler.wfile.getvalue()
    assert len(response_bytes) > 0
    data = json.loads(response_bytes.decode("utf-8"))

    assert "total_markets_scanned" in data
    assert "actionable_orders_count" in data
    assert "total_capital_staked" in data
    assert "orders" in data
    assert isinstance(data["orders"], list)


def test_polymarket_ledger_record_and_read(tmp_path):
    from model_prediction.portfolio.polymarket_ledger import (
        read_polymarket_ledger_rows,
        record_polymarket_orders,
    )

    orders = [
        {
            "market_id": "465539",
            "side": "BUY_NO",
            "order_price": 0.52,
            "market_price": 0.52,
            "model_probability": 0.553,
            "edge": 0.033,
            "ev_pct": 0.064,
            "stake_units": 17.33,
            "target_selection": "New York Yankees",
            "home_team": "New York Yankees",
            "away_team": "Toronto Blue Jays",
            "league": "MLB",
            "event_start_utc": "2026-08-22T17:35:00Z",
            "reason": "BUY NO on New York Yankees: Edge +3.3%",
        },
        {
            "market_id": "465565",
            "side": "BUY_YES",
            "order_price": 0.40,
            "market_price": 0.40,
            "model_probability": 0.459,
            "edge": 0.059,
            "ev_pct": 0.146,
            "stake_units": 24.39,
            "target_selection": "Atlanta Braves",
            "home_team": "Milwaukee Brewers",
            "away_team": "Atlanta Braves",
            "league": "MLB",
            "event_start_utc": "2026-08-22T18:10:00Z",
            "reason": "BUY YES on Atlanta Braves: Edge +5.9%",
        },
    ]

    res = record_polymarket_orders(orders, data_root=tmp_path)
    assert res["status"] == "ok"
    assert res["recorded_count"] == 2
    assert res["skipped_duplicates"] == 0
    assert res["total_rows"] == 2

    # Idempotent re-record
    res_dup = record_polymarket_orders(orders, data_root=tmp_path)
    assert res_dup["recorded_count"] == 0
    assert res_dup["skipped_duplicates"] == 2

    rows = read_polymarket_ledger_rows(data_root=tmp_path)
    assert len(rows) == 2
    assert rows[0]["market_type"] == "moneyline"
    assert rows[0]["sportsbook"] == "polymarket_us"
    assert rows[0]["status"] == "open"


def test_dashboard_polymarket_picks_endpoint():
    handler = Handler.__new__(Handler)
    handler.path = "/api/polymarket-picks"
    handler.headers = {}
    handler.wfile = BytesIO()

    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()

    handler.do_GET()

    response_bytes = handler.wfile.getvalue()
    assert len(response_bytes) > 0
    data = json.loads(response_bytes.decode("utf-8"))
    assert isinstance(data, list)
