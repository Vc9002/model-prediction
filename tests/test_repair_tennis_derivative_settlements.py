from __future__ import annotations

from scripts.repair_tennis_derivative_settlements import (
    has_binary_derivative_signature,
    plan_repairs,
)


def _row(**overrides):
    row = {
        "pick_id": "pick-1",
        "ledger_tier": "main",
        "sport": "tennis",
        "league": "TENNIS",
        "event_id": "event-1:competition-1",
        "event_start_utc": "2026-08-23T23:00:00Z",
        "market_type": "total",
        "selection": "over",
        "line": "17.5",
        "status": "settled",
        "result": "loss",
        "pnl_units": "-1.25",
        "away_score": "0",
        "home_score": "1",
    }
    row.update(overrides)
    return row


def test_bad_signature_is_identity_and_value_scoped() -> None:
    assert has_binary_derivative_signature(_row())
    assert not has_binary_derivative_signature(_row(market_type="moneyline"))
    assert not has_binary_derivative_signature(_row(away_score="6", home_score="12"))
    assert not has_binary_derivative_signature(_row(status="open"))
    assert not has_binary_derivative_signature(_row(result="push"))
    assert not has_binary_derivative_signature(_row(sport="mlb", league="MLB"))


def test_plan_repairs_uses_exact_result_identity_and_actual_games() -> None:
    result = {
        "completed": True,
        "source_result_id": "event-1:competition-1",
        "away_games": 6,
        "home_games": 12,
    }
    actions = plan_repairs([_row()], object(), result_finder=lambda *_: result)
    assert len(actions) == 1
    assert actions[0].action == "regrade"
    assert actions[0].away_games == 6
    assert actions[0].home_games == 12


def test_plan_repairs_voids_irregular_result() -> None:
    result = {
        "completed": True,
        "source_result_id": "event-1:competition-1",
        "derivative_ungradeable_reason": "irregular result marker 'retire'",
    }
    [action] = plan_repairs([_row()], object(), result_finder=lambda *_: result)
    assert action.action == "void"
    assert action.reason == "irregular result marker 'retire'"


def test_plan_repairs_rejects_name_only_result_match() -> None:
    result = {
        "completed": True,
        "source_result_id": "different-event:competition-1",
        "away_games": 6,
        "home_games": 12,
    }
    try:
        plan_repairs([_row()], object(), result_finder=lambda *_: result)
    except RuntimeError as error:
        assert "identity mismatch" in str(error)
    else:  # pragma: no cover - explicit failure keeps the assertion readable
        raise AssertionError("identity mismatch was accepted")
