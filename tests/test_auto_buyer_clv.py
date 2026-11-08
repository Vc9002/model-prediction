"""Unit tests for Auto-Buyer CLV tracking and settlement."""

import json

from model_prediction.portfolio.auto_buyer_ledger import (
    _resolve_closing_probability_for_auto_buyer,
)


def test_auto_buyer_clv_resolution(tmp_path):
    """Verify Auto-Buyer closing snapshot lookup and CLV calculation."""
    snap_dir = tmp_path / "odds" / "nba" / "2026-11-08"
    snap_dir.mkdir(parents=True)
    snap_file = snap_dir / "polymarket_snapshots.jsonl"

    snap_data = {
        "event_id": "12345",
        "market_slug": "tsc-nba-bos-mia-2026-11-08-220pt5",
        "market_type": "total",
        "observed_at_utc": "2026-11-08T18:00:00Z",
        "long": {"ask": 0.55, "description": "Over"},
        "short": {"ask": 0.46, "description": "Under"},
    }
    snap_file.write_text(json.dumps(snap_data) + "\n", encoding="utf-8")

    close_p = _resolve_closing_probability_for_auto_buyer(
        data_root=tmp_path,
        sport="NBA",
        slug="tsc-nba-bos-mia-2026-11-08-220pt5",
        event_start_utc="2026-11-08T19:00:00Z",
        token_side="long",
        selection="over",
    )

    assert close_p == 0.55
    entry_p = 0.50
    clv = close_p - entry_p
    assert round(clv, 4) == 0.05
