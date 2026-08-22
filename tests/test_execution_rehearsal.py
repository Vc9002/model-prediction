"""Unit tests for Paper-Trading Execution Rehearsal Engine."""

from __future__ import annotations

import json
from pathlib import Path

from model_prediction.portfolio.execution_rehearsal import ExecutionRehearsalRunner


def test_execution_rehearsal_empty_directory(tmp_path: Path):
    runner = ExecutionRehearsalRunner(base_dir=tmp_path)
    report = runner.run_rehearsal()

    assert report.pipeline_health == "HEALTHY"
    assert report.total_markets_scanned == 0
    assert report.actionable_orders_count == 0
    assert report.total_capital_staked_usd == 0.0
    assert len(report.tickets) == 0
    assert report.compliance_checks["all_prices_bounded"] is True


def test_execution_rehearsal_with_mock_snapshots(tmp_path: Path):
    odds_dir = tmp_path / "mlb" / "2026-08-22"
    odds_dir.mkdir(parents=True, exist_ok=True)
    snap_file = odds_dir / "polymarket_snapshots.jsonl"

    sample_snapshot = {
        "market_id": "m_test_1",
        "market_type": "moneyline",
        "league": "MLB",
        "event_title": "Boston Red Sox vs. New York Yankees",
        "long": {
            "bid": 0.40,
            "ask": 0.45,
            "description": "New York Yankees",
            "bid_size": 100.0,
            "ask_size": 100.0,
        },
        "short": {
            "bid": 0.53,
            "ask": 0.58,
            "description": "Boston Red Sox",
            "bid_size": 100.0,
            "ask_size": 100.0,
        },
        "event_start_utc": "2026-08-22T23:00:00Z",
    }

    with open(snap_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(sample_snapshot) + "\n")

    runner = ExecutionRehearsalRunner(base_dir=tmp_path, bankroll=1000.0, unit_value_usd=7.50)
    report = runner.run_rehearsal(require_model=False)

    assert report.pipeline_health == "HEALTHY"
    assert report.total_markets_scanned == 1
    assert report.actionable_orders_count == 1
    assert report.total_capital_staked_usd > 0
    assert len(report.tickets) == 1

    ticket = report.tickets[0]
    assert ticket.market_id == "m_test_1"
    assert ticket.target_side == "YES"
    assert ticket.order_price == 0.45
    assert ticket.validation_status == "VERIFIED_COMPLIANT"
    assert len(ticket.mock_signed_nonce) == 16
