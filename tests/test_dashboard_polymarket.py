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
