from datetime import datetime, timedelta, timezone

from model_prediction.domain import League, MarketType, PickRequest
from model_prediction.ledger import PickLedger


def request() -> PickRequest:
    return PickRequest(
        event_start_utc=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        event_id="event-1",
        league=League.MLB,
        away_team="NYY",
        home_team="BOS",
        market_type=MarketType.TOTAL,
        selection="over",
        line=8.5,
        sportsbook="ExampleBook",
        american_odds=-110,
        model_probability=0.59,
        model_uncertainty=0.01,
        model_version="mlb-test-v1",
        rationale="Test rationale",
        risks="Test risk",
    )


def test_call_settle_loss_and_review(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx")
    logged = ledger.append_call(request(), 0.25, 70)
    assert ledger.report()["open"] == 1

    settled = ledger.settle(
        logged["pick_id"], away_score=2, home_score=3, closing_line=9, closing_american_odds=-115
    )
    assert settled["result"] == "loss"
    assert settled["review_status"] == "review_required"
    assert float(settled["pnl_units"]) == -0.25
    assert float(settled["probability_clv"]) > 0

    reviewed = ledger.review_loss(
        logged["pick_id"],
        "bad_luck",
        "Low-tail outcome inside forecast distribution",
        "No change; monitor cohort",
    )
    assert reviewed["review_status"] == "complete"
    assert ledger.report()["loss_reviews_required"] == 0


def test_duplicate_call_is_rejected(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx")
    ledger.append_call(request(), 0.25, 70)
    try:
        ledger.append_call(request(), 0.25, 70)
    except ValueError as error:
        assert "duplicate" in str(error)
    else:
        raise AssertionError("duplicate call was accepted")


def test_verified_closing_can_be_added_after_result_without_mutating_decision(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    logged = ledger.append_call(request(), 0.25, 70)
    settled = ledger.settle(logged["pick_id"], away_score=2, home_score=3)
    decision_before = {
        field: settled[field]
        for field in (
            "model_probability",
            "decision_american_odds",
            "decision_line",
            "rationale",
            "created_at_utc",
        )
    }
    updated = ledger.update_closing(logged["pick_id"], 9, -115)
    assert updated["closing_line"] == "9"
    assert updated["closing_american_odds"] == "-115"
    assert updated["probability_clv"]
    assert {field: updated[field] for field in decision_before} == decision_before
