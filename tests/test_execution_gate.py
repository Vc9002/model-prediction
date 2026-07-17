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


US_CREDS = {
    "POLYMARKET_KEY_ID": "test-key-id",
    "POLYMARKET_SECRET_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
}


def test_ai_or_automation_can_never_fire_an_order(tmp_path) -> None:
    with pytest.raises(ExecutionGateError, match="explicit user execute command"):
        executor(tmp_path).execute(ticket(), qualified_row(), execute_flag=True, user_command=False)


def test_missing_execute_flag_is_a_dry_run(tmp_path) -> None:
    result = executor(tmp_path, env=US_CREDS).execute(
        ticket(), qualified_row(), execute_flag=False, user_command=True
    )
    assert result["status"] == "dry_run"
    assert "No order was placed" in result["note"]


def test_missing_api_credentials_refuses(tmp_path) -> None:
    with pytest.raises(ExecutionGateError, match="POLYMARKET_KEY_ID"):
        executor(tmp_path, env={}).execute(
            ticket(), qualified_row(), execute_flag=True, user_command=True
        )


def test_research_observation_requires_explicit_manual_override(tmp_path) -> None:
    row = {"record_type": "RESEARCH_OBSERVATION", "status": "open"}
    with pytest.raises(ExecutionGateError, match="manual-research-order"):
        executor(tmp_path, env=US_CREDS).execute(
            ticket(), row, execute_flag=True, user_command=True
        )


def test_explicit_manual_research_override_can_submit(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, answer="y", env=US_CREDS)
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, payload: {"id": "manual-order-123", "state": "ORDER_STATE_NEW"},
    )
    row = {
        "record_type": "RESEARCH_OBSERVATION",
        "status": "open",
        "reason_code": "NO_CALL_MISSING_UNCERTAINTY",
    }
    manual_ticket = OrderTicket(
        **{
            **ticket().__dict__,
            "maximum_cost_usd": 7.5,
            "authorization_type": "manual_research_override",
        }
    )

    result = client.execute(
        manual_ticket,
        row,
        execute_flag=True,
        user_command=True,
        manual_research_order=True,
    )

    assert result["status"] == "submitted"
    assert result["order_id"] == "manual-order-123"


def test_executor_enforces_authorized_dollar_cap(tmp_path) -> None:
    oversized = OrderTicket(
        **{
            **ticket().__dict__,
            "estimated_cost_usd": 8.0,
            "maximum_cost_usd": 7.5,
        }
    )
    with pytest.raises(ExecutionGateError, match="authorized unit cap"):
        executor(tmp_path, env=US_CREDS).execute(
            oversized, qualified_row(), execute_flag=True, user_command=True
        )


def test_user_decline_at_confirmation_places_nothing(tmp_path) -> None:
    result = executor(tmp_path, answer="n", env=US_CREDS).execute(
        ticket(), qualified_row(), execute_flag=True, user_command=True
    )
    assert result["status"] == "declined"


def test_confirmed_order_records_real_exchange_id(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, answer="y", env=US_CREDS)
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, payload: {"id": "us-order-123", "state": "ORDER_STATE_NEW"},
    )

    result = client.execute(ticket(), qualified_row(), execute_flag=True, user_command=True)

    assert result["status"] == "submitted"
    assert result["order_id"] == "us-order-123"
    assert result["order_state"] == "ORDER_STATE_NEW"
