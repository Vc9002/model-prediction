from datetime import timedelta

from model_prediction.dashboard_cache import DashboardCache
from model_prediction.domain import iso_utc, utc_now
from model_prediction.portfolio.auto_executor import (
    AutoExecutionConfig,
    AutoPolymarketBuyer,
    _auto_buyer_start_order,
)


def test_auto_buyer_orders_earlier_events_first():
    rows = [
        {"pick_id": "late", "event_start_utc": "2026-11-12T02:00:00Z"},
        {"pick_id": "early", "event_start_utc": "2026-11-11T21:00:00Z"},
        {"pick_id": "missing", "event_start_utc": ""},
    ]
    assert [row["pick_id"] for row in sorted(rows, key=_auto_buyer_start_order)] == [
        "early",
        "late",
        "missing",
    ]


def test_dashboard_cache_keeps_quote_lineage_for_live_buyer():
    row = {
        "market_quote_provenance": "indicative_slate",
        "market_quote_timestamp_valid": "False",
        "market_quote_observed_at_utc": "",
        "market_snapshot_hash": "",
        "market_snapshot_archive_path": "",
    }
    cached = DashboardCache._dashboard_row(row)
    assert all(cached[key] == value for key, value in row.items())


def test_quote_fetched_after_cycle_start_is_not_stale(tmp_path, monkeypatch):
    pick = {
        "pick_id": "fresh-refresh",
        "model_id": "nba-spread-margin-v9",
        "status": "open",
        "decision": "CALL",
        "league": "NBA",
        "market_type": "spread",
        "selection": "home",
        "line": -3.5,
        "event_start_utc": iso_utc(utc_now() + timedelta(hours=2)),
        "model_probability": 0.55,
    }
    monkeypatch.setattr(
        "model_prediction.dashboard.orders._decorate_pick",
        lambda _row: {
            "quote": {
                "market_slug": "exact-spread",
                "side": "long",
                "ask": 0.5,
                "fresh": False,
                "age_seconds": 600,
                "market_state": "MARKET_STATE_OPEN",
            }
        },
    )
    monkeypatch.setattr(
        "model_prediction.data_sources.polymarket_us.PolymarketUSClient.snapshot",
        lambda _self, _slug: {
            "observed_at_utc": iso_utc(utc_now()),
            "market_state": "MARKET_STATE_OPEN",
            "long": {"ask": 0.5},
        },
    )
    buyer = AutoPolymarketBuyer(
        config=AutoExecutionConfig(
            whitelisted_models=("nba-spread-margin-v9",),
            blacklisted_models=(),
            mode="live",
            execute_live=True,
            min_model_probability=0.99,
            capture_missing_slates=False,
        ),
        data_root=tmp_path,
    )
    result = buyer.evaluate_and_execute([pick])
    assert result.rejected_stale_quote == 0
    assert result.rejected_below_win_probability == 1
