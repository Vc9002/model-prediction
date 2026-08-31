"""Tests for the market-relative evaluator (market_eval.py).

Synthetic rows with hand-computable answers pin the metric definitions:
a model that equals the market must produce exact zero deltas; a model
with real edge must show positive ROI and CLV; the bootstrap CI must
contain the point estimate.
"""

from __future__ import annotations

import pytest

from model_prediction.market_eval import (
    MarketEvalRow,
    decide_sides,
    market_relative_report,
    no_vig,
)


def _row(
    event: str,
    model: float,
    market: float,
    *,
    price: float | None = None,
    closing: float | None = None,
    outcome: int = 0,
    day: str = "2026-08-01",
) -> MarketEvalRow:
    return MarketEvalRow(
        event_id=event,
        decision_utc=f"{day}T18:00:00Z",
        market_type="total",
        line=10.5,
        model_prob=model,
        entry_fair_prob=market,
        entry_ask=market if price is None else price,
        entry_price=market if price is None else price,
        closing_fair_prob=closing,
        outcome=outcome,
    )


def test_no_vig_removes_overround():
    # Long 0.55 / short 0.51 -> overround 1.06; fair long = 0.55/1.06.
    assert no_vig(0.55, 0.51) == pytest.approx(0.55 / 1.06)
    # Perfectly balanced with vig: 0.52 + 0.52 -> 0.5.
    assert no_vig(0.52, 0.52) == pytest.approx(0.5)


def test_market_as_model_yields_zero_deltas():
    rows = [
        _row("e1", model=0.6, market=0.6, outcome=1),
        _row("e2", model=0.3, market=0.3, outcome=0),
        _row("e3", model=0.5, market=0.5, outcome=1),
    ] * 15  # 45 rows, past the minimum-sample gate
    report = market_relative_report(rows)
    assert report["status"] == "ok"
    assert report["predictive"]["delta_logloss"] == pytest.approx(0.0, abs=1e-9)
    assert report["predictive"]["delta_brier"] == pytest.approx(0.0, abs=1e-9)


def test_perfect_model_reports_edge_and_positive_roi():
    # The model says 0.9 every time and it always wins; market sits at 0.6
    # but the bet was executable at 0.55 and closed at 0.60, so true CLV is +0.05 on every bet.
    rows = [_row(f"e{i}", model=0.9, market=0.6, price=0.55, closing=0.60, outcome=1) for i in range(40)]
    report = market_relative_report(rows)
    assert report["predictive"]["delta_logloss"] < 0
    assert report["predictive"]["delta_brier"] < 0
    assert report["economic"]["roi"] > 0
    assert report["economic"]["clv_rate"] == 1.0
    assert report["economic"]["clv"]["clv_available"] is True
    assert report["economic"]["clv"]["mean_clv"] == pytest.approx(0.05)
    # Unit-stake P&L: win pays (1-p)/p = 0.45/0.55 per unit.
    assert report["economic"]["roi"] == pytest.approx(0.45 / 0.55, rel=1e-6)


def test_always_losing_model_reports_negative_roi():
    rows = [_row(f"e{i}", model=0.9, market=0.6, outcome=0) for i in range(40)]
    report = market_relative_report(rows)
    assert report["economic"]["roi"] == pytest.approx(-1.0, rel=1e-6)
    assert report["economic"]["profit_factor"] == 0.0


def test_bootstrap_ci_contains_point_estimate():
    rows = [_row(f"e{i}", model=0.7, market=0.55, outcome=1) for i in range(50)]
    report = market_relative_report(rows)
    lo, hi = report["economic"]["roi_ci_95"]
    assert lo <= report["economic"]["roi"] <= hi


def test_decide_sides_picks_side_with_edge():
    by_event = {
        "e1": [
            _row("e1", model=0.6, market=0.5, outcome=1),
            _row("e1", model=0.4, market=0.5, outcome=0),
        ],
        "e2": [
            _row("e2", model=0.49, market=0.5, outcome=1),
            _row("e2", model=0.51, market=0.5, outcome=0),
        ],
    }
    chosen = decide_sides(by_event, min_edge=0.03)
    # e1's long side has edge 0.10; e2's best side has edge 0.01, so it
    # is skipped — no bet below the threshold.
    assert [r.event_id for r in chosen] == ["e1"]
    assert chosen[0].model_prob == 0.6


def test_insufficient_sample_status():
    report = market_relative_report([_row("e1", 0.6, 0.6, outcome=1)])
    assert report["status"] == "insufficient_sample"
    assert report["sample_size"] == 1


def test_no_vig_rejects_nonpositive_total():
    with pytest.raises(ValueError):
        no_vig(0.0, 0.0)


def test_calibration_survives_high_probs_with_few_wins():
    # Real failure shape from the WNBA market-residual harness: 89 bets at
    # 0.72-0.95 that mostly lost. IRLS diverges toward a huge negative
    # intercept and math.exp overflowed before the clamp in
    # calibration._logistic_calibration.
    from model_prediction.calibration import calibration_metrics

    probs = [0.75 + 0.002 * i for i in range(89)]
    outcomes = [1] * 25 + [0] * 64
    report = calibration_metrics(probs, outcomes)
    assert report["status"] == "ok"


def test_clv_requires_closing_quote():
    row = MarketEvalRow(
        event_id="e_noclv",
        decision_utc="2026-08-31T18:00:00Z",
        market_type="moneyline",
        line=None,
        model_prob=0.60,
        entry_fair_prob=0.55,
        entry_ask=0.56,
        entry_price=0.56,
        closing_fair_prob=None,
        outcome=1,
    )
    assert row.true_clv is None
    assert row.market_move is None


def test_static_market_has_zero_market_move():
    row = MarketEvalRow(
        event_id="e_static",
        decision_utc="2026-08-31T18:00:00Z",
        market_type="moneyline",
        line=None,
        model_prob=0.60,
        entry_fair_prob=0.54,
        entry_ask=0.55,
        entry_price=0.55,
        closing_fair_prob=0.54,
        outcome=1,
    )
    assert row.market_move == pytest.approx(0.0)
    assert row.true_clv == pytest.approx(0.54 - 0.55)


def test_bid_ask_spread_is_not_clv():
    row = MarketEvalRow(
        event_id="e_spread",
        decision_utc="2026-08-31T18:00:00Z",
        market_type="moneyline",
        line=None,
        model_prob=0.62,
        entry_fair_prob=0.50,
        entry_bid=0.48,
        entry_ask=0.52,
        entry_price=0.52,
        closing_fair_prob=0.56,
        outcome=1,
    )
    spread_concession = row.entry_fair_prob - row.entry_price  # -0.02
    true_clv = row.true_clv  # +0.04
    assert spread_concession != true_clv
    assert true_clv == pytest.approx(0.04)


def test_positive_model_disagreement_can_have_negative_executable_ev():
    row = MarketEvalRow(
        event_id="e_neg_exec",
        decision_utc="2026-08-31T18:00:00Z",
        market_type="moneyline",
        line=None,
        model_prob=0.57,
        entry_fair_prob=0.52,
        entry_ask=0.59,
        entry_price=0.59,
        outcome=1,
    )
    assert row.model_edge_vs_market == pytest.approx(0.05)  # +5% disagreement
    assert row.execution_edge == pytest.approx(-0.02)  # -2% execution edge
    assert row.expected_net_ev < 0  # negative EV
    assert row.expected_roi < 0  # negative ROI


def test_side_selection_uses_executable_ev():
    # Side A: Model 0.57, Fair 0.52, Ask 0.59 (disagreement +5%, execution edge -2% -> negative EV)
    # Side B: Model 0.43, Fair 0.48, Ask 0.40 (disagreement -5%, execution edge +3% -> positive EV)
    by_event = {
        "e_arb": [
            MarketEvalRow(
                "e_arb",
                "2026-08-31",
                "moneyline",
                None,
                0.57,
                entry_fair_prob=0.52,
                entry_ask=0.59,
                entry_price=0.59,
            ),
            MarketEvalRow(
                "e_arb",
                "2026-08-31",
                "moneyline",
                None,
                0.43,
                entry_fair_prob=0.48,
                entry_ask=0.40,
                entry_price=0.40,
            ),
        ]
    }
    chosen = decide_sides(by_event)
    assert len(chosen) == 1
    # Side B must be chosen because of positive executable EV, not Side A
    assert chosen[0].entry_ask == 0.40
    assert chosen[0].expected_net_ev > 0


def test_fees_can_turn_small_positive_edge_negative():
    row = MarketEvalRow(
        event_id="e_fee",
        decision_utc="2026-08-31T18:00:00Z",
        market_type="moneyline",
        line=None,
        model_prob=0.51,
        entry_fair_prob=0.50,
        entry_ask=0.50,
        entry_price=0.50,
        fee_rate=0.03,  # 3% fee
        outcome=1,
    )
    # Gross edge = 0.51 - 0.50 = +0.01 (+1%)
    # Net payoff = 0.51 * 0.97 - 0.50 = 0.4947 - 0.50 = -0.0053 (< 0)
    assert row.execution_edge == pytest.approx(0.01)
    assert row.expected_net_ev < 0
    assert row.expected_roi < 0


def test_missing_closing_quote_returns_clv_unavailable():
    rows = [
        MarketEvalRow(
            event_id=f"e{i}",
            decision_utc="2026-08-01T18:00:00Z",
            market_type="moneyline",
            line=None,
            model_prob=0.60,
            entry_fair_prob=0.50,
            entry_ask=0.50,
            entry_price=0.50,
            closing_fair_prob=None,
            outcome=1,
        )
        for i in range(40)
    ]
    report = market_relative_report(rows)
    assert report["status"] == "ok"
    clv = report["economic"]["clv"]
    assert clv["clv_available"] is False
    assert clv["clv_rate"] is None
    assert clv["mean_clv"] is None
    assert clv["reason"] == "closing_quotes_unavailable"
