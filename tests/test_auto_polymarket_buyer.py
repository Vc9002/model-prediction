"""Unit tests for Automated Polymarket Buyer."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from model_prediction.audit import AuditLog
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio.auto_executor import (
    AutoExecutionConfig,
    AutoPolymarketBuyer,
    load_auto_buyer_state,
    toggle_auto_buyer,
)


def test_buyer_filters_blacklist_models():
    now = utc_now()
    today_start = iso_utc(now + timedelta(hours=2))
    config = AutoExecutionConfig(
        whitelisted_models=("tennis-surface-elo-v1", "measured-edge-margin-v3"),
        blacklisted_models=("measured-edge-margin-v3",),
    )
    buyer = AutoPolymarketBuyer(
        config=config,
        live_quote_fn=lambda _slug: {"ask": 0.50, "market_slug": "test-slug", "side": "long"},
    )
    picks = [
        {
            "pick_id": "p_black",
            "model_id": "measured-edge-margin-v3",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.65,
            "market_probability": 0.50,
        }
    ]
    res = buyer.evaluate_and_execute(picks)
    assert res.rejected_blacklist == 1
    assert len(res.dry_run_orders) == 0


def test_buyer_filters_low_edge_and_past_games():
    now = utc_now()
    today_start = iso_utc(now + timedelta(hours=2))
    config = AutoExecutionConfig(min_edge=0.05)
    buyer = AutoPolymarketBuyer(
        config=config,
        live_quote_fn=lambda _slug: {"ask": 0.50, "market_slug": "test-slug", "side": "long"},
    )
    picks = [
        {
            "pick_id": "p_low_edge",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.53,
            "market_probability": 0.50,  # 3% edge < 5% min
        },
        {
            "pick_id": "p_past",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": "2020-01-01T00:00:00Z",  # already started
            "model_probability": 0.70,
            "market_probability": 0.50,
        },
    ]
    res = buyer.evaluate_and_execute(picks)
    assert res.rejected_low_edge == 1
    assert res.rejected_started == 1
    assert len(res.dry_run_orders) == 0


def test_buyer_rejects_tomorrow_and_future_games():
    now = utc_now()
    tomorrow_start = iso_utc(now + timedelta(days=1, hours=4))
    next_week_start = iso_utc(now + timedelta(days=7))
    config = AutoExecutionConfig(min_edge=0.03)
    buyer = AutoPolymarketBuyer(
        config=config,
        live_quote_fn=lambda _slug: {"ask": 0.50, "market_slug": "test-slug", "side": "long"},
    )
    picks = [
        {
            "pick_id": "p_tmr",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": tomorrow_start,
            "model_probability": 0.70,
            "market_probability": 0.50,
        },
        {
            "pick_id": "p_next_week",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": next_week_start,
            "model_probability": 0.70,
            "market_probability": 0.50,
        },
    ]
    res = buyer.evaluate_and_execute(picks)
    assert res.rejected_future_slate == 2
    assert len(res.dry_run_orders) == 0


def test_buyer_rejects_stale_quotes_and_closed_markets():
    now = utc_now()
    today_start = iso_utc(now + timedelta(hours=2))
    config = AutoExecutionConfig(min_edge=0.03)
    # Unmapped/None quote
    buyer_unmapped = AutoPolymarketBuyer(
        config=config,
        live_quote_fn=lambda _slug: (_ for _ in ()).throw(ValueError("not found")),
    )
    picks = [
        {
            "pick_id": "p_unmapped",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.70,
            "market_probability": 0.50,
        }
    ]
    res = buyer_unmapped.evaluate_and_execute(picks)
    assert res.rejected_unmapped_market == 1
    assert len(res.dry_run_orders) == 0


def test_buyer_sizes_fractional_units():
    now = utc_now()
    today_start = iso_utc(now + timedelta(hours=2))
    # 1U = $0.005 (0.5 cent)
    config = AutoExecutionConfig(
        unit_value_usd=0.005,
        min_edge=0.03,
        whitelisted_models=("tennis-surface-elo-v1",),
    )
    buyer = AutoPolymarketBuyer(
        config=config,
        live_quote_fn=lambda _slug: {"ask": 0.55, "market_slug": "test-slug", "side": "long"},
    )
    picks = [
        {
            "pick_id": "p_qual",
            "model_id": "tennis-surface-elo-v1",
            "sport": "tennis",
            "selection": "Alcaraz",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.65,
            "market_probability": 0.55,
            "units": 1.5,  # 1.5 U = $0.0075 -> at $0.55 min 1 share = $0.55
        }
    ]
    res = buyer.evaluate_and_execute(picks)
    assert len(res.dry_run_orders) == 1
    order = res.dry_run_orders[0]
    assert order["pick_id"] == "p_qual"
    assert order["limit_price"] == 0.55
    assert order["shares"] == 1.0
    assert order["cost_usd"] == 0.55
    assert order["edge"] == 0.10


def test_buyer_respects_daily_budget():
    now = utc_now()
    today_start = iso_utc(now + timedelta(hours=2))
    config = AutoExecutionConfig(
        max_daily_spend_usd=0.80,
        whitelisted_models=("tennis-surface-elo-v1",),
    )
    buyer = AutoPolymarketBuyer(
        config=config,
        live_quote_fn=lambda slug: {"ask": 0.50, "market_slug": slug, "side": "long"},
    )
    picks = [
        {
            "pick_id": "p_1",
            "market_slug": "slug-1",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.65,
            "market_probability": 0.50,  # cost $0.50
        },
        {
            "pick_id": "p_2",
            "market_slug": "slug-2",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.65,
            "market_probability": 0.50,  # cost $0.50 -> exceeds $0.80 budget
        },
    ]
    res = buyer.evaluate_and_execute(picks)
    assert len(res.dry_run_orders) == 1
    assert res.rejected_budget == 1
    assert res.total_spend_usd == 0.50


def test_auto_buyer_toggle_state(tmp_path: Path):
    test_state_file = tmp_path / "auto_buyer_state.json"
    with patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file):
        # Initial state should be disabled
        st = load_auto_buyer_state()
        assert st["enabled"] is False
        assert st["unit_value_usd"] == 0.005

        # Toggle to True
        toggled = toggle_auto_buyer(True)
        assert toggled["enabled"] is True
        assert load_auto_buyer_state()["enabled"] is True

        # Toggle to False
        toggled_off = toggle_auto_buyer(False)
        assert toggled_off["enabled"] is False
        assert load_auto_buyer_state()["enabled"] is False


def test_auto_buyer_dashboard_routes(tmp_path: Path):
    from io import BytesIO
    from unittest.mock import Mock

    from model_prediction.dashboard.common import _DASHBOARD_TOKEN
    from model_prediction.dashboard.routes import Handler

    test_state_file = tmp_path / "auto_buyer_state.json"
    with patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file):
        # 1. Test GET /api/auto-buyer/status
        handler_get = Handler.__new__(Handler)
        handler_get.path = "/api/auto-buyer/status"
        handler_get.headers = {}
        handler_get.wfile = BytesIO()
        handler_get.send_response = Mock()
        handler_get.send_header = Mock()
        handler_get.end_headers = Mock()

        handler_get.do_GET()
        res_get = json.loads(handler_get.wfile.getvalue().decode("utf-8"))
        assert res_get["enabled"] is False
        assert res_get["unit_value_usd"] == 0.005

        # 2. Test POST /api/auto-buyer/toggle
        payload = json.dumps({"confirm": True, "enabled": True}).encode("utf-8")
        handler_post = Handler.__new__(Handler)
        handler_post.path = "/api/auto-buyer/toggle"
        handler_post.headers = {
            "Content-Length": str(len(payload)),
            "X-Dashboard-Token": _DASHBOARD_TOKEN,
        }
        handler_post.rfile = BytesIO(payload)
        handler_post.wfile = BytesIO()
        handler_post.send_response = Mock()
        handler_post.send_header = Mock()
        handler_post.end_headers = Mock()

        handler_post.do_POST()
        res_post = json.loads(handler_post.wfile.getvalue().decode("utf-8"))
        assert res_post["enabled"] is True


def test_auto_buyer_multi_source_deduplication(tmp_path: Path):
    now = utc_now()
    today_start = iso_utc(now + timedelta(hours=2))
    config = AutoExecutionConfig(min_edge=0.03, whitelisted_models=("tennis-surface-elo-v1",))

    audit_file = tmp_path / "audit.jsonl"
    audit = AuditLog(audit_file)
    # Simulate an order already executed in audit log for pick_1
    audit.append(
        "order_executed",
        "p_already_bought_audit",
        {
            "action": "buy",
            "market_slug": "slug-audit-bought",
            "token_side": "long",
            "event_id": "ev_100",
            "selection": "home",
        },
    )

    buyer = AutoPolymarketBuyer(
        config=config,
        audit=audit,
        live_quote_fn=lambda slug: {
            "ask": 0.50,
            "market_slug": slug,
            "side": "long",
            "fresh": True,
            "market_state": "MARKET_STATE_OPEN",
        },
    )

    picks = [
        # 1. Matches pick_id in audit log
        {
            "pick_id": "p_already_bought_audit",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.70,
            "market_probability": 0.50,
        },
        # 2. Different pick_id, but matches market_slug + token_side in audit log
        {
            "pick_id": "p_different_id_same_market",
            "market_slug": "slug-audit-bought",
            "token_side": "long",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.70,
            "market_probability": 0.50,
        },
        # 3. Different pick_id, but matches event_id + selection in audit log
        {
            "pick_id": "p_different_id_same_event",
            "event_id": "ev_100",
            "selection": "home",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.70,
            "market_probability": 0.50,
        },
        # 4. Brand new unbought pick
        {
            "pick_id": "p_fresh_new_pick",
            "market_slug": "slug-fresh-unbought",
            "token_side": "long",
            "event_id": "ev_200",
            "selection": "away",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.70,
            "market_probability": 0.50,
        },
        # 5. Duplicate of pick 4 in the same batch
        {
            "pick_id": "p_fresh_new_pick_dupe_in_batch",
            "market_slug": "slug-fresh-unbought",
            "token_side": "long",
            "event_id": "ev_200",
            "selection": "away",
            "model_id": "tennis-surface-elo-v1",
            "status": "open",
            "event_start_utc": today_start,
            "model_probability": 0.70,
            "market_probability": 0.50,
        },
    ]

    res = buyer.evaluate_and_execute(picks)
    # Picks 1, 2, 3, and 5 should all be rejected by deduplication
    assert res.rejected_dedup == 4
    # Only Pick 4 should be accepted
    assert len(res.dry_run_orders) == 1
    assert res.dry_run_orders[0]["pick_id"] == "p_fresh_new_pick"


def test_auto_buyer_cycle_tracking(tmp_path: Path):
    from unittest.mock import patch

    from model_prediction.portfolio.auto_executor import (
        load_auto_buyer_state,
        run_auto_buyer_cycle,
        toggle_auto_buyer,
    )

    test_state_file = tmp_path / "auto_buyer_state.json"
    with patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file):
        toggle_auto_buyer(True)

        # Run cycle for 2026-08-30
        run1 = run_auto_buyer_cycle(forecast_date="2026-08-30")
        assert run1.get("mode") == "LIVE_EXECUTION"
        assert run1.get("forecast_date") == "2026-08-30"
        assert load_auto_buyer_state()["last_daily_date"] == "2026-08-30"
        assert "executed_at_utc" in run1


def test_auto_buyer_ledger_recording_and_backfill(tmp_path: Path):
    from model_prediction.portfolio.auto_buyer_ledger import (
        read_auto_buyer_ledger,
        record_auto_buy_execution,
    )
    from model_prediction.xlsx_ledger import read_xlsx_rows

    test_jsonl = tmp_path / "auto_buyer_ledger.jsonl"
    test_xlsx = tmp_path / "auto_buyer_picks.xlsx"

    # 1. Record an execution
    payload = {
        "order_id": "ORD_TEST_1",
        "pick_id": "p_test_1",
        "sport": "MLB",
        "market_slug": "tsc-mlb-sd-tb-2026-08-30-7pt5",
        "token_side": "long",
        "selection": "over",
        "limit_price": 0.47,
        "shares": 1.0,
        "cost_usd": 0.47,
        "edge": 0.048,
    }
    pick_row = {
        "pick_id": "p_test_1",
        "sport": "MLB",
        "away_team": "San Diego Padres",
        "home_team": "Tampa Bay Rays",
        "market_type": "total",
        "model_id": "measured-edge-totals-v3",
        "model_probability": 0.518,
        "market_implied_probability": 0.47,
    }

    rec = record_auto_buy_execution(
        order_payload=payload,
        order_id="ORD_TEST_1",
        order_state="FILLED",
        pick_row=pick_row,
        jsonl_path=test_jsonl,
        xlsx_path=test_xlsx,
    )

    assert rec["order_id"] == "ORD_TEST_1"
    assert rec["shares"] == 1.0
    assert rec["cost_usd"] == 0.47

    # Verify JSONL
    entries = read_auto_buyer_ledger(jsonl_path=test_jsonl)
    assert len(entries) == 1
    assert entries[0]["order_id"] == "ORD_TEST_1"
    assert entries[0]["away_team"] == "San Diego Padres"

    # Verify XLSX
    _headers, rows = read_xlsx_rows(test_xlsx)
    assert len(rows) == 1
    assert rows[0]["pick_id"] == "p_test_1"
    assert rows[0]["sportsbook"] == "polymarket_us"
    assert rows[0]["away_team"] == "San Diego Padres"


def test_summarize_auto_buyer_performance():
    from model_prediction.portfolio.auto_buyer_ledger import summarize_auto_buyer_performance

    sample_records = [
        {
            "order_id": "1",
            "status": "settled",
            "result": "win",
            "cost_usd": 0.50,
            "pnl_usd": 0.50,
            "pnl_units": 1.0,
            "edge": 0.10,
        },
        {
            "order_id": "2",
            "status": "settled",
            "result": "loss",
            "cost_usd": 0.50,
            "pnl_usd": -0.50,
            "pnl_units": -1.0,
            "edge": 0.08,
        },
        {
            "order_id": "3",
            "status": "settled",
            "result": "win",
            "cost_usd": 0.50,
            "pnl_usd": 0.50,
            "pnl_units": 1.0,
            "edge": 0.06,
        },
        {
            "order_id": "4",
            "status": "open",
            "result": "open",
            "cost_usd": 0.50,
            "pnl_usd": 0.0,
            "pnl_units": 0.0,
            "edge": 0.05,
        },
    ]

    summary = summarize_auto_buyer_performance(sample_records)
    assert summary["total_orders"] == 4
    assert summary["settled_orders"] == 3
    assert summary["open_orders"] == 1
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["win_rate_pct"] == 66.7
    assert summary["realized_pnl_usd"] == 0.50
    assert summary["realized_pnl_units"] == 1.0
    assert summary["realized_roi_pct"] == 33.3
    assert summary["avg_edge_pct"] == 8.0
