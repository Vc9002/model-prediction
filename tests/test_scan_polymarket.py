"""Unit tests for Polymarket US Live Slate Scanner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from model_prediction.portfolio.polymarket_scanner import PolymarketSlateScanner


def test_parse_snapshot_line():
    scanner = PolymarketSlateScanner(bankroll=1000.0, min_edge=0.025)

    line = json.dumps(
        {
            "event_id": "1001",
            "event_title": "NY Yankees vs Boston Red Sox",
            "league": "MLB",
            "market_id": "m_1001",
            "market_type": "moneyline",
            "long": {"ask": 0.45, "bid": 0.43, "description": "NY Yankees"},
            "short": {"ask": 0.57, "bid": 0.55, "description": "Boston Red Sox"},
            "event_start_utc": "2026-08-22T00:00:00Z",
        }
    )

    req = scanner.parse_snapshot_line(line)
    assert req is not None
    assert req.market_id == "m_1001"
    assert req.league == "MLB"
    assert req.best_ask == 0.45
    assert req.best_bid == 0.43


def test_scan_file_filtering(tmp_path: Path):
    scanner = PolymarketSlateScanner(bankroll=1000.0, min_edge=0.03)

    f_path = tmp_path / "polymarket_snapshots.jsonl"
    lines = [
        # Line 1: Ask 0.40, Model 0.50 (Edge +10% -> qualifies)
        json.dumps(
            {
                "event_id": "1",
                "market_id": "m1",
                "market_type": "moneyline",
                "league": "CS2",
                "long": {"ask": 0.40, "bid": 0.38, "description": "NaVi"},
                "short": {"ask": 0.62, "bid": 0.60, "description": "FaZe"},
                "event_start_utc": "2026-08-22T10:00:00Z",
            }
        ),
        # Line 2: Ask 0.50, Model 0.50 (Edge 0% -> rejected)
        json.dumps(
            {
                "event_id": "2",
                "market_id": "m2",
                "market_type": "moneyline",
                "league": "CS2",
                "long": {"ask": 0.50, "bid": 0.48, "description": "G2"},
                "short": {"ask": 0.52, "bid": 0.50, "description": "Vitality"},
                "event_start_utc": "2026-08-22T12:00:00Z",
            }
        ),
    ]
    f_path.write_text("\n".join(lines))

    orders = scanner.scan_file(f_path)
    assert len(orders) == 1
    assert orders[0].market_id == "m1"
    assert orders[0].side == "BUY_YES"
    assert orders[0].edge == pytest.approx(0.10, abs=1e-4)
