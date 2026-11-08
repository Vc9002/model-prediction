from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from model_prediction.portfolio.auto_buyer_ledger import (
    resolve_auto_buyer_paths,
    settle_auto_buyer_ledger,
)


@pytest.mark.unit
def test_resolve_auto_buyer_paths_subfolder(tmp_path: Path) -> None:
    # 1. When root contains paper_auto_buyer/auto_buyer_ledger.jsonl
    sub = tmp_path / "paper_auto_buyer"
    sub.mkdir(parents=True)
    jsonl = sub / "auto_buyer_ledger.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")
    xlsx = sub / "auto_buyer_picks.xlsx"
    xlsx.write_text("dummy", encoding="utf-8")

    res_j, res_x = resolve_auto_buyer_paths(data_root=tmp_path)
    assert res_j == jsonl
    assert res_x == xlsx


@pytest.mark.unit
def test_resolve_auto_buyer_paths_direct(tmp_path: Path) -> None:
    # When root directly contains auto_buyer_ledger.jsonl
    jsonl = tmp_path / "auto_buyer_ledger.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")
    xlsx = tmp_path / "auto_buyer_picks.xlsx"
    xlsx.write_text("dummy", encoding="utf-8")

    res_j, res_x = resolve_auto_buyer_paths(data_root=tmp_path)
    assert res_j == jsonl
    assert res_x == xlsx


@pytest.mark.unit
def test_settle_auto_buyer_fair_market_price(tmp_path: Path) -> None:
    # Test settling a tennis match where Polymarket resolved to fair market price (non-binary)
    record = {
        "order_id": "PAPER-test1",
        "pick_id": "test_pick_1",
        "sport": "TENNIS",
        "market_slug": "aec-wta-test-match-2026-09-21",
        "token_side": "short",
        "shares": 10.0,
        "entry_price": 0.50,
        "cost_usd": 5.0,
        "status": "open",
        "result": "open",
        "event_start_utc": "2026-09-21T16:00Z",
        "unit_value_usd": 5.0,
    }
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    j_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    mock_pm = MagicMock()
    mock_pm.market.return_value = {
        "status": "MARKET_STATUS_RESOLVED",
        "description": "If match cancelled prior to first point, settles to fair market price.",
        "outcomePrices": ["0.51", "0.49"],
    }

    res = settle_auto_buyer_ledger(
        data_root=tmp_path,
        polymarket_client=mock_pm,
    )

    assert res["settled"] == 1
    # Verify updated record
    lines = j_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    updated = json.loads(lines[0])
    assert updated["status"] == "settled"
    assert updated["result"] == "push"
    assert updated["settlement_type"] == "fair_value"
    assert updated["settlement_price"] == 0.49
    # PnL: (0.49 - 0.50) * 10 = -0.10
    assert updated["pnl_usd"] == -0.10
