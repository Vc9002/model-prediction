from __future__ import annotations

from model_prediction.cli.daily import (
    DailyIntegrityError,
    _finalize_daily_report,
    _polymarket_order_payload,
)
from model_prediction.portfolio.polymarket_kelly import PolymarketOrderDecision


def test_polymarket_payload_uses_actual_expected_value_contract() -> None:
    decision = PolymarketOrderDecision(
        market_id="m1",
        side="BUY_YES",
        is_maker=False,
        order_price=0.55,
        model_probability=0.65,
        market_price=0.55,
        edge=0.10,
        expected_value_pct=18.18,
        kelly_fraction_full=0.2,
        kelly_fraction_recommended=0.05,
        stake_units=1.0,
        reason="qualified",
        question="A vs B",
        target_selection="A",
        target_side="YES",
        home_team="A",
        away_team="B",
        selection_label="A (BUY YES)",
        event_start_utc="2026-08-24T23:00:00Z",
        observed_at_utc="2026-08-24T12:00:00Z",
    )

    payload = _polymarket_order_payload(decision)

    assert payload["ev_pct"] == 18.18
    assert "expected_value_pct" not in payload


def test_material_substep_error_raises_after_fail_soft_completion() -> None:
    report = {
        "step1b_soccer_scores": {"status": "error"},  # enrichment remains fail-soft
        "step2_3_forecast_and_log": {"mlb": {"status": "ok"}},
        "step4_settlement": {"status": "ok"},
        "step7_flat_settlement": {"status": "ok"},
        "step8_research_settlement": {"status": "ok"},
        "step9_gated_research_settlement": {"status": "ok"},
        "step10_polymarket_edge_record": {"status": "error"},
        "step11_polymarket_edge_settle": {"status": "ok"},
    }

    try:
        _finalize_daily_report(report)
    except DailyIntegrityError as exc:
        assert exc.report["status"] == "error"
        assert exc.report["material_errors"] == ["step10_polymarket_edge_record"]
    else:
        raise AssertionError("material daily error must produce a non-green outcome")


def test_capture_only_error_remains_fail_soft() -> None:
    report = {
        "step1b_soccer_scores": {"status": "error"},
        "step2_3_forecast_and_log": {"mlb": {"status": "ok"}},
        "step4_settlement": {"status": "skipped"},
        "step7_flat_settlement": {"status": "skipped"},
        "step8_research_settlement": {"status": "skipped"},
        "step9_gated_research_settlement": {"status": "skipped"},
        "step10_polymarket_edge_record": {"status": "ok"},
        "step11_polymarket_edge_settle": {"status": "skipped"},
    }

    result = _finalize_daily_report(report)

    assert result["status"] == "ok"
    assert result["material_errors"] == []
