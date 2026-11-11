from model_prediction.dashboard_cache import DashboardCache
from model_prediction.portfolio.auto_executor import _auto_buyer_start_order


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
