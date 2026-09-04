"""Unit tests for Automated Polymarket Buyer."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from model_prediction.audit import AuditLog
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio.auto_buyer_ledger import (
    read_auto_buyer_ledger,
    reconcile_pending_auto_buyer_fallbacks,
    record_auto_buy_execution,
)
from model_prediction.portfolio.auto_executor import (
    AutoExecutionConfig,
    AutoExecutionResult,
    AutoPolymarketBuyer,
    _capture_missing_active_snapshot_slates,
    load_auto_buyer_state,
    run_auto_buyer_cycle,
    set_auto_buyer_unit_value,
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


def test_buyer_categorically_rejects_mlb_moneyline_even_when_model_is_whitelisted():
    quote_calls = []

    def quote_lookup(slug):
        quote_calls.append(slug)
        return {"ask": 0.50, "market_slug": slug, "side": "long"}

    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            whitelisted_models=("mlb-moneyline-v99", "tennis-surface-elo-v1"),
            blacklisted_models=(),
        ),
        live_quote_fn=quote_lookup,
    )
    event_start = iso_utc(utc_now() + timedelta(hours=2))
    result = buyer.evaluate_and_execute(
        [
            {
                "pick_id": "mlb-ml-disabled",
                "model_id": "mlb-moneyline-v99",
                "status": "open",
                "league": "MLB",
                "market_type": "moneyline",
                "event_start_utc": event_start,
                "model_probability": 0.70,
                "market_probability": 0.50,
            },
            {
                "pick_id": "tennis-ml-allowed",
                "model_id": "tennis-surface-elo-v1",
                "status": "open",
                "league": "TENNIS",
                "market_type": "moneyline",
                "event_start_utc": event_start,
                "model_probability": 0.70,
                "market_probability": 0.50,
            },
        ]
    )

    assert result.rejected_disabled_sport_market == 1
    assert quote_calls == ["slug-tennis-ml-allowed"]
    assert [order["pick_id"] for order in result.dry_run_orders] == ["tennis-ml-allowed"]


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


def test_buyer_fails_closed_when_stale_quote_refresh_has_network_error():
    buyer = AutoPolymarketBuyer()
    stale_quote = {
        "quote": {
            "market_slug": "aec-tennis-example",
            "side": "long",
            "ask": 0.50,
            "fresh": False,
            "age_seconds": 600,
            "market_state": "MARKET_STATE_OPEN",
        }
    }
    with (
        patch("model_prediction.dashboard.orders._decorate_pick", return_value=stale_quote),
        patch(
            "model_prediction.data_sources.polymarket_us.PolymarketUSClient.snapshot",
            side_effect=httpx.ConnectError("offline"),
        ),
    ):
        result = buyer.evaluate_and_execute(
            [
                {
                    "pick_id": "stale-network",
                    "model_id": "tennis-surface-elo-v1",
                    "status": "open",
                    "market_type": "moneyline",
                    "event_start_utc": iso_utc(utc_now() + timedelta(hours=2)),
                }
            ]
        )

    assert result.rejected_stale_quote == 1
    assert result.dry_run_orders == []


def test_buyer_allows_nrfi_to_reach_exact_quote_lookup():
    calls = []

    def quote_lookup(slug):
        calls.append(slug)
        return {"market_slug": slug, "side": "short", "ask": 0.50}

    # NRFI market_type routing is being exercised here, independent of the
    # live production whitelist (MLB models pulled 2026-09-02 pending
    # qualification review) -- inject an explicit config that whitelists
    # this model so the test still isolates NRFI-slug quote lookup.
    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(whitelisted_models=("mlb-nrfi-v2",), blacklisted_models=()),
        live_quote_fn=quote_lookup,
    )
    result = buyer.evaluate_and_execute(
        [
            {
                "pick_id": "nrfi-supported",
                "model_id": "mlb-nrfi-v2",
                "status": "open",
                "league": "MLB",
                "market_type": "nrfi",
                "selection": "nrfi",
                "event_start_utc": iso_utc(utc_now() + timedelta(hours=2)),
                "model_probability": 0.60,
                "market_probability": 0.50,
                "units": 1.0,
                "market_slug": "astatc-mlb-example-yrfi",
            }
        ]
    )

    assert result.rejected_unsupported_market == 0
    assert result.rejected_unmapped_market == 0
    assert calls == ["astatc-mlb-example-yrfi"]
    assert len(result.dry_run_orders) == 1


def test_live_buyer_preflight_captures_missing_next_day_slate(tmp_path: Path):
    class FakeSlate:
        def __init__(self):
            self.events = {"CS2": [{"markets": []}]}
            self.errors = {}

    class FakeClient:
        def __init__(self):
            self.requests = []

        def sport_slate(self, sport, game_date, timezone_name="America/New_York"):
            self.requests.append((sport, game_date.isoformat(), timezone_name))
            return FakeSlate()

    client = FakeClient()
    captures = []

    def capture(_client, events, data_root, game_date):
        captures.append((events, Path(data_root), game_date))
        return {"status": "ok", "captured": 2}

    now = utc_now()
    event_start = now + timedelta(hours=14)
    result = _capture_missing_active_snapshot_slates(
        [
            {
                "model_id": "cs2-tiered-elo-v6",
                "status": "open",
                "league": "CS2",
                "market_type": "moneyline",
                "event_start_utc": iso_utc(event_start),
            }
        ],
        config=AutoExecutionConfig(execute_live=True),
        now=now,
        data_root=tmp_path,
        client=client,
        capture_fn=capture,
    )

    assert result == [
        {
            "sport": "esports",
            "game_date": event_start.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
            "status": "ok",
            "captured": 2,
            "league_errors": {},
        }
    ]
    assert len(client.requests) == 1
    assert len(captures) == 1


def test_buyer_sizes_fractional_units():
    now = utc_now()
    today_start = iso_utc(now + timedelta(hours=2))
    # 1U = $0.50 (50 cents)
    config = AutoExecutionConfig(
        unit_value_usd=0.50,
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
            "units": 1.5,  # 1.5U = $0.75 -> 1.36 shares at a $0.55 ask
        }
    ]
    res = buyer.evaluate_and_execute(picks)
    assert len(res.dry_run_orders) == 1
    order = res.dry_run_orders[0]
    assert order["pick_id"] == "p_qual"
    assert order["limit_price"] == 0.55
    assert order["shares"] == 1.36
    assert order["cost_usd"] == 0.75
    assert order["edge"] == 0.10


def test_live_buyer_sets_resting_fallback_and_records_actual_fill(monkeypatch, tmp_path):
    now = utc_now()
    captured = {}

    class FakeExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute(self, ticket, **_kwargs):
            captured["ticket"] = ticket
            return {
                "status": "submitted",
                "order_id": "primary-partial",
                "order_ids": ["primary-partial", "resting-remainder"],
                "order_state": "ORDER_STATE_PARTIALLY_FILLED",
                "filled_size_shares": 0.88,
                "estimated_filled_cost_usd": 0.4488,
                "fallback_order_id": "resting-remainder",
                "fallback_status": "resting",
                "fallback_resting_shares": 11.37,
            }

    recorded = []
    monkeypatch.setattr(
        AutoPolymarketBuyer,
        "_build_bought_index",
        lambda _self: {
            "pick_ids": set(),
            "market_sides": set(),
            "event_selections": set(),
            "held_slugs": set(),
        },
    )
    monkeypatch.setattr("model_prediction.portfolio.auto_executor.PolymarketExecutor", FakeExecutor)
    monkeypatch.setattr(
        "model_prediction.portfolio.auto_buyer_ledger.record_auto_buy_execution",
        lambda **kwargs: recorded.append(kwargs),
    )
    monkeypatch.setattr("model_prediction.portfolio.auto_executor.time.sleep", lambda _seconds: None)
    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            unit_value_usd=5.0,
            min_edge=0.035,
            max_game_stake_usd=25.0,
            max_daily_spend_usd=250.0,
            execute_live=True,
            whitelisted_models=("lol-tiered-elo-v6",),
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        live_quote_fn=lambda _slug: {
            "ask": 0.51,
            "market_slug": "aec-lol-lds-dv1-2026-09-03",
            "side": "long",
        },
    )

    result = buyer.evaluate_and_execute(
        [
            {
                "pick_id": "lodis",
                "model_id": "lol-tiered-elo-v6",
                "sport": "lol",
                "market_type": "moneyline",
                "selection": "home",
                "home_team": "Lodis",
                "away_team": "devils.one inStreamly",
                "status": "open",
                "event_start_utc": iso_utc(now + timedelta(hours=12)),
                "model_probability": 0.58,
                "market_probability": 0.51,
                "units": 1.25,
            }
        ]
    )

    # Every auto-buyer IOC ticket opts into the resting fallback.
    assert captured["ticket"].ioc_fallback_resting is True
    assert captured["ticket"].estimated_cost_usd == 6.25
    # Only the confirmed fill counts toward spend/ledger -- the resting
    # remainder isn't assumed complete.
    assert result.total_spend_usd == 0.45
    assert result.submitted_orders[0]["requested_cost_usd"] == 6.25
    assert result.submitted_orders[0]["cost_usd"] == 0.4488
    assert result.submitted_orders[0]["shares"] == 0.88
    assert result.submitted_orders[0]["fallback_order_id"] == "resting-remainder"
    assert result.submitted_orders[0]["order_ids"] == ["primary-partial", "resting-remainder"]
    assert recorded[0]["order_payload"]["cost_usd"] == 0.4488
    assert recorded[0]["order_payload"]["shares"] == 0.88


def test_live_buyer_records_zero_fill_primary_with_resting_fallback(monkeypatch, tmp_path):
    """A primary IOC that fills 0 shares still records a ledger row if a
    fallback order is resting, so reconcile_pending_auto_buyer_fallbacks()
    can find it later instead of it becoming an untracked live order."""
    now = utc_now()

    class FakeExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute(self, ticket, **_kwargs):
            return {
                "status": "submitted",
                "order_id": "primary-zero",
                "order_ids": ["primary-zero", "resting-full"],
                "order_state": "ORDER_STATE_PARTIALLY_FILLED",
                "filled_size_shares": 0.0,
                "estimated_filled_cost_usd": 0.0,
                "fallback_order_id": "resting-full",
                "fallback_status": "resting",
                "fallback_resting_shares": 12.25,
            }

    recorded = []
    monkeypatch.setattr(
        AutoPolymarketBuyer,
        "_build_bought_index",
        lambda _self: {
            "pick_ids": set(),
            "market_sides": set(),
            "event_selections": set(),
            "held_slugs": set(),
        },
    )
    monkeypatch.setattr("model_prediction.portfolio.auto_executor.PolymarketExecutor", FakeExecutor)
    monkeypatch.setattr(
        "model_prediction.portfolio.auto_buyer_ledger.record_auto_buy_execution",
        lambda **kwargs: recorded.append(kwargs),
    )
    monkeypatch.setattr("model_prediction.portfolio.auto_executor.time.sleep", lambda _seconds: None)
    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            unit_value_usd=5.0,
            min_edge=0.035,
            max_game_stake_usd=25.0,
            max_daily_spend_usd=250.0,
            execute_live=True,
            whitelisted_models=("lol-tiered-elo-v6",),
        ),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        live_quote_fn=lambda _slug: {
            "ask": 0.51,
            "market_slug": "aec-lol-lds-dv1-2026-09-03",
            "side": "long",
        },
    )

    result = buyer.evaluate_and_execute(
        [
            {
                "pick_id": "lodis_zero",
                "model_id": "lol-tiered-elo-v6",
                "sport": "lol",
                "market_type": "moneyline",
                "selection": "home",
                "home_team": "Lodis",
                "away_team": "devils.one inStreamly",
                "status": "open",
                "event_start_utc": iso_utc(now + timedelta(hours=12)),
                "model_probability": 0.58,
                "market_probability": 0.51,
                "units": 1.25,
            }
        ]
    )

    # A zero-fill primary contributes nothing to spend, but the fallback
    # order must still be recorded so it can be reconciled later.
    assert result.total_spend_usd == 0.0
    assert len(recorded) == 1
    assert recorded[0]["order_payload"]["shares"] == 0.0
    assert recorded[0]["order_payload"]["cost_usd"] == 0.0
    assert recorded[0]["order_payload"]["fallback_order_id"] == "resting-full"


def test_record_auto_buy_execution_does_not_fabricate_shares_on_zero_fill(tmp_path):
    """`shares`/`cost_usd` of 0.0 must not be treated as falsy and replaced
    with the pick's requested shares -- that would fabricate a fill that
    never happened."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"
    record = record_auto_buy_execution(
        order_payload={
            "order_id": "primary-zero",
            "order_ids": ["primary-zero", "resting-full"],
            "pick_id": "PICK_ZERO",
            "market_slug": "aec-lol-lds-dv1-2026-09-03",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.51,
            "cost_usd": 0.0,
            "shares": 0.0,
            "sport": "LOL",
            "event_start_utc": iso_utc(utc_now() + timedelta(hours=12)),
            "fallback_order_id": "resting-full",
            "fallback_resting_shares": 12.25,
        },
        pick_row={
            "away_team": "devils.one inStreamly",
            "home_team": "Lodis",
            "market_type": "moneyline",
            "units": 1.25,
            "shares": 12.25,  # requested shares -- must not leak into the record
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    assert record["shares"] == 0.0
    assert record["cost_usd"] == 0.0
    assert record["primary_filled_shares"] == 0.0
    assert record["fallback_resting_shares"] == 12.25


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
    with (
        patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file),
        patch("model_prediction.portfolio.auto_executor.DATA", tmp_path),
    ):
        # Initial state should be disabled
        st = load_auto_buyer_state()
        assert st["enabled"] is False
        assert st["unit_value_usd"] == 0.50

        # Toggle to True
        toggled = toggle_auto_buyer(True)
        assert toggled["enabled"] is True
        assert load_auto_buyer_state()["enabled"] is True

        # Toggle to False
        toggled_off = toggle_auto_buyer(False)
        assert toggled_off["enabled"] is False
        assert load_auto_buyer_state()["enabled"] is False


def test_auto_buyer_unit_value_is_persisted_and_used_by_future_cycles(tmp_path: Path):
    test_state_file = tmp_path / "auto_buyer_state.json"
    buyer_result = AutoExecutionResult()
    with (
        patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file),
        patch("model_prediction.portfolio.auto_executor.DATA", tmp_path),
        patch("model_prediction.portfolio.auto_executor.AutoPolymarketBuyer") as buyer_class,
    ):
        buyer_class.return_value.evaluate_and_execute.return_value = buyer_result

        updated = set_auto_buyer_unit_value(1.25)
        run_auto_buyer_cycle(execute_override=False)

    assert updated["status"] == "ok"
    assert updated["previous_unit_value_usd"] == 0.50
    assert updated["unit_value_usd"] == 1.25
    assert updated["max_game_stake_units"] == 5.0
    assert updated["max_game_stake_usd"] == 6.25
    assert updated["max_daily_spend_units"] == 50.0
    assert updated["max_daily_spend_usd"] == 62.5
    saved = json.loads(test_state_file.read_text(encoding="utf-8"))
    assert saved["unit_value_usd"] == 1.25
    assert saved["max_game_stake_usd"] == 6.25
    assert saved["max_daily_spend_usd"] == 62.5
    assert buyer_class.call_args.kwargs["config"].unit_value_usd == 1.25
    assert buyer_class.call_args.kwargs["config"].max_game_stake_usd == 6.25
    assert buyer_class.call_args.kwargs["config"].max_daily_spend_usd == 62.5


def test_auto_buyer_legacy_dollar_caps_migrate_to_unit_caps(tmp_path: Path):
    test_state_file = tmp_path / "auto_buyer_state.json"
    test_state_file.write_text(
        json.dumps(
            {
                "enabled": True,
                "unit_value_usd": 0.50,
                "max_game_stake_usd": 2.50,
                "max_daily_spend_usd": 25.0,
            }
        ),
        encoding="utf-8",
    )
    with patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file):
        state = load_auto_buyer_state()

    assert state["max_game_stake_units"] == 5.0
    assert state["max_daily_spend_units"] == 50.0
    assert state["max_game_stake_usd"] == 2.50
    assert state["max_daily_spend_usd"] == 25.0


def test_auto_buyer_unit_value_rejects_invalid_amount(tmp_path: Path):
    test_state_file = tmp_path / "auto_buyer_state.json"
    with (
        patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file),
        pytest.raises(ValueError, match="between"),
    ):
        set_auto_buyer_unit_value(0)


def test_auto_buyer_dashboard_routes(tmp_path: Path):
    from io import BytesIO
    from unittest.mock import Mock

    from model_prediction.dashboard.common import _DASHBOARD_TOKEN
    from model_prediction.dashboard.routes import Handler

    test_state_file = tmp_path / "auto_buyer_state.json"
    with (
        patch("model_prediction.portfolio.auto_executor.AUTO_BUYER_STATE_FILE", test_state_file),
        patch("model_prediction.portfolio.auto_executor.DATA", tmp_path),
    ):
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
        assert res_get["unit_value_usd"] == 0.50

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

        # 3. Test POST /api/auto-buyer/unit-value
        unit_payload = json.dumps({"confirm": True, "unit_value_usd": 1.25}).encode("utf-8")
        handler_unit = Handler.__new__(Handler)
        handler_unit.path = "/api/auto-buyer/unit-value"
        handler_unit.headers = {
            "Content-Length": str(len(unit_payload)),
            "X-Dashboard-Token": _DASHBOARD_TOKEN,
        }
        handler_unit.rfile = BytesIO(unit_payload)
        handler_unit.wfile = BytesIO()
        handler_unit.send_response = Mock()
        handler_unit.send_header = Mock()
        handler_unit.end_headers = Mock()

        handler_unit.do_POST()
        res_unit = json.loads(handler_unit.wfile.getvalue().decode("utf-8"))
        assert res_unit["status"] == "ok"
        assert res_unit["unit_value_usd"] == 1.25
        assert res_unit["max_game_stake_units"] == 5.0
        assert res_unit["max_game_stake_usd"] == 6.25
        assert res_unit["max_daily_spend_units"] == 50.0
        assert res_unit["max_daily_spend_usd"] == 62.5
        assert load_auto_buyer_state()["unit_value_usd"] == 1.25


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

    with patch("model_prediction.portfolio.auto_buyer_ledger.log_auto_buyer_event") as log_event:
        rec = record_auto_buy_execution(
            order_payload=payload,
            order_id="ORD_TEST_1",
            order_state="FILLED",
            pick_row=pick_row,
            jsonl_path=test_jsonl,
            xlsx_path=test_xlsx,
        )
    log_event.assert_not_called()

    assert rec["order_id"] == "ORD_TEST_1"
    assert rec["shares"] == 1.0
    assert rec["cost_usd"] == 0.47
    assert rec["units"] == 0.94
    assert rec["model_units"] == 1.0
    assert rec["unit_value_usd"] == 0.50

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


def _record_pending_fallback(tmp_path, event_start_utc, fallback_resting_shares=11.37):
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"
    record_auto_buy_execution(
        order_payload={
            "order_id": "primary-partial",
            "order_ids": ["primary-partial", "resting-remainder"],
            "pick_id": "PICK_LODIS",
            "market_slug": "aec-lol-lds-dv1-2026-09-03",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.51,
            "cost_usd": 0.4488,
            "shares": 0.88,
            "sport": "LOL",
            "event_start_utc": event_start_utc,
            "fallback_order_id": "resting-remainder",
            "fallback_resting_shares": fallback_resting_shares,
        },
        pick_row={
            "away_team": "devils.one inStreamly",
            "home_team": "Lodis",
            "market_type": "moneyline",
            "units": 1.25,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )
    return j_path, x_path


def test_reconcile_restates_unknown_primary_fill(tmp_path):
    """An IOC whose fill was unknown at submit time is restated from the
    exchange's terminal order state, never left as a 0-share phantom."""
    j_path = tmp_path / "auto_buyer_ledger.jsonl"
    x_path = tmp_path / "auto_buyer_picks.xlsx"
    record_auto_buy_execution(
        order_payload={
            "order_id": "primary-unknown",
            "order_ids": ["primary-unknown"],
            "pick_id": "PICK_UNKNOWN",
            "market_slug": "aec-cs2-inf-ntr-2026-09-04",
            "selection": "away",
            "token_side": "short",
            "limit_price": 0.69,
            "cost_usd": 0.0,
            "shares": 0.0,
            "sport": "CS2",
            "event_start_utc": iso_utc(utc_now() + timedelta(hours=12)),
            "fallback_order_id": "primary-unknown",
            "fallback_resting_shares": 7.25,
            "fill_known": False,
        },
        pick_row={
            "away_team": "Nuclear TigeRES",
            "home_team": "Infinite",
            "market_type": "moneyline",
            "units": 1.0,
        },
        jsonl_path=j_path,
        xlsx_path=x_path,
    )

    class FakeExecutor:
        def order_snapshots(self, order_ids):
            assert order_ids == ["primary-unknown"]
            return {
                "status": "live",
                "orders": [
                    {
                        "order_id": "primary-unknown",
                        "order_state": "ORDER_STATE_FILLED",
                        "cum_quantity": 7.25,
                    }
                ],
            }

    result = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=FakeExecutor())

    assert result["reconciled_filled"] == 1
    records = read_auto_buyer_ledger(j_path)
    assert records[0]["shares"] == 7.25
    assert records[0]["cost_usd"] == round(7.25 * 0.69, 4)
    assert records[0]["fallback_reconciled"] is True
    assert records[0]["order_state"] == "ORDER_STATE_FILLED"


def test_reconcile_adds_fallback_fill_on_top_of_primary_baseline(tmp_path):
    j_path, _ = _record_pending_fallback(tmp_path, event_start_utc=iso_utc(utc_now() + timedelta(hours=12)))

    class FakeExecutor:
        def order_snapshots(self, order_ids):
            assert order_ids == ["resting-remainder"]
            return {
                "status": "live",
                "orders": [
                    {
                        "order_id": "resting-remainder",
                        "order_state": "ORDER_STATE_PARTIALLY_FILLED",
                        "cum_quantity": 1.56,
                    }
                ],
            }

    result = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=FakeExecutor())

    assert result == {"reconciled_filled": 0, "cancelled_expired": 0, "still_pending": 1, "errors": 0}
    records = read_auto_buyer_ledger(j_path)
    assert records[0]["shares"] == 2.44
    assert records[0]["cost_usd"] == round(0.4488 + 1.56 * 0.51, 4)
    assert records[0]["fallback_reconciled"] is False


def test_reconcile_marks_terminal_fill_and_stops_reconciling(tmp_path):
    j_path, _ = _record_pending_fallback(tmp_path, event_start_utc=iso_utc(utc_now() + timedelta(hours=12)))

    class FakeExecutor:
        def order_snapshots(self, order_ids):
            return {
                "status": "live",
                "orders": [
                    {
                        "order_id": "resting-remainder",
                        "order_state": "ORDER_STATE_FILLED",
                        "cum_quantity": 11.37,
                    }
                ],
            }

    result = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=FakeExecutor())

    assert result["reconciled_filled"] == 1
    records = read_auto_buyer_ledger(j_path)
    assert records[0]["shares"] == round(0.88 + 11.37, 4)
    assert records[0]["fallback_reconciled"] is True

    # Idempotent: a second run should not touch an already-reconciled row.
    result_again = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=FakeExecutor())
    assert result_again == {"reconciled_filled": 0, "cancelled_expired": 0, "still_pending": 0, "errors": 0}


def test_reconcile_cancels_resting_order_once_event_has_started(tmp_path):
    j_path, _ = _record_pending_fallback(tmp_path, event_start_utc=iso_utc(utc_now() - timedelta(minutes=5)))
    cancelled = []

    class FakeExecutor:
        def order_snapshots(self, order_ids):
            return {
                "status": "live",
                "orders": [
                    {"order_id": "resting-remainder", "order_state": "ORDER_STATE_NEW", "cum_quantity": 0.0}
                ],
            }

        def cancel(self, order_id, user_command):
            assert user_command is True
            cancelled.append(order_id)
            return {"status": "cancelled", "order_id": order_id}

    result = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=FakeExecutor())

    assert cancelled == ["resting-remainder"]
    assert result["cancelled_expired"] == 1
    records = read_auto_buyer_ledger(j_path)
    assert records[0]["fallback_reconciled"] is True
    assert records[0]["shares"] == 0.88


def test_reconcile_one_unreachable_order_does_not_block_other_pending_rows(tmp_path):
    """A single order_id the exchange can't answer for (stale/purged/rate-
    limited past retries) must not stall reconciliation of every other
    pending fallback -- for unattended daily operation, one bad row blocking
    the whole batch would leave every other real position unreconciled
    indefinitely. PolymarketExecutor.order_snapshots() isolates per-id
    failures into `unavailable_order_ids` rather than raising, so this
    FakeExecutor mirrors that real contract."""
    j_path, _ = _record_pending_fallback(tmp_path, event_start_utc=iso_utc(utc_now() + timedelta(hours=12)))
    record_auto_buy_execution(
        order_payload={
            "order_id": "primary-healthy",
            "order_ids": ["primary-healthy", "resting-healthy"],
            "pick_id": "PICK_HEALTHY",
            "market_slug": "aec-cs2-alpha-beta-2026-09-04",
            "selection": "home",
            "token_side": "long",
            "limit_price": 0.50,
            "cost_usd": 0.0,
            "shares": 0.0,
            "sport": "CS2",
            "event_start_utc": iso_utc(utc_now() + timedelta(hours=12)),
            "fallback_order_id": "resting-healthy",
            "fallback_resting_shares": 5.0,
        },
        pick_row={
            "away_team": "Beta",
            "home_team": "Alpha",
            "market_type": "moneyline",
            "units": 1.0,
        },
        jsonl_path=j_path,
        xlsx_path=None,
    )

    class FlakyExecutor:
        """Mirrors PolymarketExecutor.order_snapshots' real contract: a bad
        id is reported via `unavailable_order_ids`, never raised, so the
        rest of the batch is still returned."""

        def order_snapshots(self, order_ids):
            return {
                "status": "live",
                "orders": [
                    {"order_id": "resting-healthy", "order_state": "ORDER_STATE_FILLED", "cum_quantity": 5.0}
                ],
                "unavailable_order_ids": ["resting-remainder"],
            }

    result = reconcile_pending_auto_buyer_fallbacks(data_root=tmp_path, executor=FlakyExecutor())

    records = {r["pick_id"]: r for r in read_auto_buyer_ledger(j_path)}
    # The unreachable order's own row stays pending (correct -- we don't know
    # its state), but the healthy sibling row must still get reconciled.
    assert records["PICK_HEALTHY"]["fallback_reconciled"] is True
    assert records["PICK_LODIS"]["fallback_reconciled"] is False
    assert result["reconciled_filled"] == 1
    assert result["still_pending"] == 1
