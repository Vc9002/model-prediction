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


def test_paper_trial_records_cohort_member_after_price_drift(tmp_path):
    """Cohort membership is the SAVED decision-time edge, not a later price.

    The 2026-11-11 NC A&T pick saved at a 7.9% edge was excluded from the paper
    trial once its live ask moved and the *current* edge reached 8.9%, even
    though the live bucket had already filled it. Paper must record the full
    saved-edge cohort; only a phantom-edge ceiling may still apply.
    """
    from model_prediction.portfolio.ncaab_paper_trial import (
        _PAPER_EXECUTION_SANITY_MAX_EDGE,
        eligible_trial_pick,
    )

    now = utc_now()
    row = {
        "pick_id": "drifted",
        "event_id": "game-1",
        "model_id": "ncaab-spread-v6",
        "sport": "ncaab",
        "market_type": "spread",
        "selection": "away",
        "status": "open",
        "decision": "CALL",
        "reason_code": "FLAT_CALL",
        "edge": "0.078896",
        "line": 10.5,
        "model_probability": 0.588896,
        "units": 1.0,
        "away_team": "Away",
        "home_team": "Home",
        "created_at_utc": iso_utc(now),
        "event_start_utc": iso_utc(now + timedelta(hours=6)),
        "market_quote_provenance": "indicative_slate",
        "market_quote_timestamp_valid": "False",
    }
    assert eligible_trial_pick(row, min_edge=0.05, max_edge=0.08)

    base = dict(  # noqa: C408 - kwargs dict keeps the two buyer configs diff-able
        mode="paper",
        execute_live=False,
        paper_ncaab_spread_v6_trial=True,
        whitelisted_models=("ncaab-spread-v6",),
        blacklisted_models=(),
        edge_scaled_sizing_enabled=False,
        require_model_timestamp=True,
        capture_missing_slates=False,
        unit_value_usd=15.0,
        max_game_stake_usd=75.0,
        max_daily_spend_usd=750.0,
    )

    def _buyer(min_edge, max_edge):
        return AutoPolymarketBuyer(
            config=AutoExecutionConfig(min_edge=min_edge, max_edge=max_edge, **base),
            live_quote_fn=lambda slug: {"market_slug": slug, "ask": 0.50, "side": "long"},
            data_root=tmp_path,
        )

    # Re-applying the saved band to the drifted current edge (8.89%) drops it.
    dropped = _buyer(0.0, 0.08).evaluate_and_execute([dict(row)])
    assert dropped.rejected_unrealistic_edge == 1
    assert dropped.dry_run_orders == []

    # The sanity ceiling records it.
    kept = _buyer(0.0, _PAPER_EXECUTION_SANITY_MAX_EDGE).evaluate_and_execute([dict(row)])
    assert kept.rejected_unrealistic_edge == 0
    assert len(kept.dry_run_orders) == 1
    assert kept.dry_run_orders[0]["pick_id"] == "drifted"
