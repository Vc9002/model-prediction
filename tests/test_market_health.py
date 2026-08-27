"""Tests for the market-relative production diagnostics (market_health.py).

Synthetic settled-row dicts in the RuntimeLedgerStore.records shape pin
the skip rules (non-settled, push/void, missing probabilities) and the
per-market grouping. The battery itself is covered by test_market_eval.
"""

from __future__ import annotations

from model_prediction.market_health import market_relative_from_rows


def _row(
    sport: str,
    market_type: str,
    model_prob: float | None,
    market_prob: float | None,
    *,
    result: str = "win",
    status: str = "settled",
    pick_id: str = "p1",
    event_id: str = "e1",
) -> dict:
    return {
        "pick_id": pick_id,
        "sport": sport,
        "market_type": market_type,
        "model_probability": model_prob,
        "market_probability": market_prob,
        "result": result,
        "status": status,
        "event_id": event_id,
        "event_start_utc": "2026-08-01T23:00:00Z",
        "line": None,
    }


def test_groups_by_sport_market_and_reports():
    rows = [_row("wnba", "total", 0.6, 0.55, pick_id=f"p{i}", event_id=f"e{i}") for i in range(40)] + [
        _row("mlb", "moneyline", 0.7, 0.6, pick_id="m1", event_id="m1")
    ]
    report = market_relative_from_rows(rows)
    assert report["status"] == "ok"
    assert report["n_markets"] == 2
    assert "wnba:total" in report["by_market"]
    assert report["by_market"]["wnba:total"]["status"] == "ok"
    # One row is below the minimum sample for a full battery.
    assert report["by_market"]["mlb:moneyline"]["status"] == "insufficient_sample"


def test_skips_unsettled_push_and_missing_probabilities():
    rows = [
        *_good_rows(40),
        _row("wnba", "total", 0.6, 0.55, result="push", pick_id="x1"),
        _row("wnba", "total", 0.6, 0.55, status="open", pick_id="x2"),
        _row("wnba", "total", None, 0.55, pick_id="x3"),
        _row("wnba", "total", 0.6, None, pick_id="x4"),
    ]
    report = market_relative_from_rows(rows)
    assert report["by_market"]["wnba:total"]["n_bets"] == 40


def _good_rows(n: int) -> list[dict]:
    return [_row("wnba", "total", 0.6, 0.55, pick_id=f"p{i}", event_id=f"e{i}") for i in range(n)]


def test_empty_ledger_is_ok_with_no_markets():
    assert market_relative_from_rows([]) == {"status": "ok", "n_markets": 0, "by_market": {}}
