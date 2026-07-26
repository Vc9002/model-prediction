from datetime import UTC, datetime, timedelta

from model_prediction.domain import League, MarketType, PickRequest, RecordType
from model_prediction.eligibility import EligibilityResult
from model_prediction.entities import CanonicalTeam
from model_prediction.ledger import PickLedger
from model_prediction.units import edge_scaled_units


def request() -> PickRequest:
    return PickRequest(
        event_start_utc=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
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


AWAY = CanonicalTeam("mlb-nyy", League.MLB, "NYY", "NYY", True, None, None, ())
HOME = CanonicalTeam("mlb-bos", League.MLB, "BOS", "BOS", True, None, None, ())


def _observation(reason_code: str, units: float) -> EligibilityResult:
    return EligibilityResult(
        RecordType.RESEARCH_OBSERVATION, "NO_CALL", reason_code, units, 55, 0.05, 0.05, AWAY, HOME
    )


def _qualified_call(units: float) -> EligibilityResult:
    return EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", units, 70, 0.08, 0.08, AWAY, HOME
    )


def test_recompute_research_sizing_fills_in_a_zero_unit_sizable_reason(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_evaluated(request(), _observation("NO_CALL_LOW_EDGE", 0.0))
    assert float(row["units"]) == 0.0

    changed = ledger.recompute_research_sizing()
    assert changed == 1

    updated = ledger.rows()[0]
    expected = edge_scaled_units(0.59, 0.01, -110)
    assert float(updated["units"]) == expected


def test_recompute_research_sizing_zeroes_a_hard_zero_reason_that_had_leaked_units(tmp_path) -> None:
    """Simulates the too-broad mid-session bug: a structurally-untrustworthy
    reason (team banned) must never keep a non-zero size regardless of how
    it got there."""
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    ledger.append_evaluated(request(), _observation("NO_CALL_TEAM_BANNED", 1.5))

    changed = ledger.recompute_research_sizing()
    assert changed == 1
    assert float(ledger.rows()[0]["units"]) == 0.0


def test_recompute_research_sizing_never_touches_a_real_qualified_call(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    ledger.append_evaluated(request(), _qualified_call(1.75))

    changed = ledger.recompute_research_sizing()
    assert changed == 0
    assert float(ledger.rows()[0]["units"]) == 1.75


def test_recompute_research_sizing_recomputes_pnl_for_already_settled_rows(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    row = ledger.append_evaluated(request(), _observation("NO_CALL_LOW_EDGE", 0.0))
    settled = ledger.settle(row["pick_id"], away_score=5, home_score=5)  # total 10 > 8.5 -> over wins
    assert float(settled["pnl_units"]) == 0.0  # old bug: zero units -> zero pnl regardless of result

    ledger.recompute_research_sizing()
    updated = ledger.rows()[0]
    expected_units = edge_scaled_units(0.59, 0.01, -110)
    assert float(updated["units"]) == expected_units
    assert float(updated["pnl_units"]) > 0  # win, so pnl now reflects the corrected non-zero size


def test_recompute_research_sizing_is_idempotent(tmp_path) -> None:
    ledger = PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")
    ledger.append_evaluated(request(), _observation("NO_CALL_LOW_EDGE", 0.0))
    ledger.recompute_research_sizing()
    changed_again = ledger.recompute_research_sizing()
    assert changed_again == 0
