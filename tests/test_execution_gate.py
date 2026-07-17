import pytest

from model_prediction.audit import AuditLog
from model_prediction.data_sources.polymarket_execute import (
    ExecutionGateError,
    OrderTicket,
    PolymarketExecutor,
)


def ticket() -> OrderTicket:
    return OrderTicket(
        market_slug="aec-mlb-nyy-bos-2026-07-17",
        token_side="long",
        action="buy",
        order_type="limit_gtc",
        price=0.62,
        size_shares=5,
        pick_id="pick-1",
        estimated_cost_usd=3.10,
    )


def qualified_row() -> dict[str, str]:
    return {"record_type": "QUALIFIED_SHADOW_CALL", "status": "open"}


def executor(tmp_path, answer="y", env=None) -> PolymarketExecutor:
    return PolymarketExecutor(
        AuditLog(tmp_path / "events.jsonl"),
        confirm=lambda prompt: answer,
        environ=env if env is not None else {},
    )


def test_ai_or_automation_can_never_fire_an_order(tmp_path) -> None:
    with pytest.raises(ExecutionGateError, match="explicit user execute command"):
        executor(tmp_path).execute(ticket(), qualified_row(), execute_flag=True, user_command=False)


def test_missing_execute_flag_is_a_dry_run(tmp_path) -> None:
    result = executor(tmp_path, env={"POLYMARKET_PRIVATE_KEY": "0xkey"}).execute(
        ticket(), qualified_row(), execute_flag=False, user_command=True
    )
    assert result["status"] == "dry_run"
    assert "No order was placed" in result["note"]


def test_missing_private_key_refuses(tmp_path) -> None:
    with pytest.raises(ExecutionGateError, match="POLYMARKET_PRIVATE_KEY"):
        executor(tmp_path, env={}).execute(
            ticket(), qualified_row(), execute_flag=True, user_command=True
        )


def test_research_observation_can_never_execute(tmp_path) -> None:
    row = {"record_type": "RESEARCH_OBSERVATION", "status": "open"}
    with pytest.raises(ExecutionGateError, match="QUALIFIED_SHADOW_CALL"):
        executor(tmp_path, env={"POLYMARKET_PRIVATE_KEY": "0xkey"}).execute(
            ticket(), row, execute_flag=True, user_command=True
        )


def test_user_decline_at_confirmation_places_nothing(tmp_path) -> None:
    result = executor(tmp_path, answer="n", env={"POLYMARKET_PRIVATE_KEY": "0xkey"}).execute(
        ticket(), qualified_row(), execute_flag=True, user_command=True
    )
    assert result["status"] == "declined"


def test_confirmed_order_without_signing_library_refuses_honestly(tmp_path) -> None:
    # py_clob_client is not installed in the test environment; the gate must
    # refuse at the signing step rather than pretend a fill happened.
    with pytest.raises(ExecutionGateError, match="py_clob_client"):
        executor(tmp_path, answer="y", env={"POLYMARKET_PRIVATE_KEY": "0xkey"}).execute(
            ticket(), qualified_row(), execute_flag=True, user_command=True
        )
