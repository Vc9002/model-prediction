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
    return {
        "pick_id": "pick-1",
        "record_type": "QUALIFIED_SHADOW_CALL",
        "status": "open",
        "rationale": "Learned LR call at threshold 0.55; executable ask 0.6200 "
        "(market_slug=aec-mlb-nyy-bos-2026-07-17).",
    }


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


def test_ticket_pick_id_must_match_the_looked_up_row(tmp_path) -> None:
    """A ticket for a different pick_id than the row it's paired with must be
    refused -- otherwise a caller could pass a legitimately qualified row to
    clear the gate while submitting an unrelated ticket."""
    mismatched = OrderTicket(**{**ticket().__dict__, "pick_id": "pick-999"})
    with pytest.raises(ExecutionGateError, match="does not match the looked-up row"):
        executor(tmp_path, env=US_CREDS).execute(
            mismatched, qualified_row(), execute_flag=True, user_command=True
        )


def test_ticket_market_slug_must_match_what_the_row_was_priced_against(tmp_path) -> None:
    wrong_market = OrderTicket(**{**ticket().__dict__, "market_slug": "aec-mlb-lad-sf-2026-07-17"})
    with pytest.raises(ExecutionGateError, match="does not match the market this pick"):
        executor(tmp_path, env=US_CREDS).execute(
            wrong_market, qualified_row(), execute_flag=True, user_command=True
        )


def test_ticket_estimated_cost_is_recomputed_not_trusted(tmp_path) -> None:
    understated = OrderTicket(**{**ticket().__dict__, "estimated_cost_usd": 0.01})
    with pytest.raises(ExecutionGateError, match=r"does not match price \* size_shares"):
        executor(tmp_path, env=US_CREDS).execute(
            understated, qualified_row(), execute_flag=True, user_command=True
        )


def test_config_override_qualified_call_refused_without_manual_order(tmp_path) -> None:
    """A row can read QUALIFIED_SHADOW_CALL purely because config declares
    qualification_override: true (e.g. MLB v6, whose own artifact says
    qualified=false) -- real execution must not treat that the same as a
    genuinely validated artifact."""
    with pytest.raises(ExecutionGateError, match="backing model artifact itself is not qualified"):
        executor(tmp_path, env=US_CREDS).execute(
            ticket(), qualified_row(), execute_flag=True, user_command=True, artifact_qualified=False
        )


def test_config_override_qualified_call_can_submit_via_manual_order(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, answer="y", env=US_CREDS)
    monkeypatch.setattr(
        client, "_request", lambda method, path, payload: {"id": "override-order-1", "state": "ORDER_STATE_NEW"}
    )

    result = client.execute(
        ticket(),
        qualified_row(),
        execute_flag=True,
        user_command=True,
        manual_research_order=True,
        artifact_qualified=False,
    )

    assert result["status"] == "submitted"


def test_executor_enforces_authorized_dollar_cap(tmp_path) -> None:
    # size_shares raised (not just estimated_cost_usd) so price * size_shares
    # itself exceeds the cap -- the executor now recomputes cost server-side
    # rather than trusting a caller-supplied estimated_cost_usd.
    oversized = OrderTicket(
        **{
            **ticket().__dict__,
            "size_shares": 13,
            "estimated_cost_usd": 8.06,
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


@pytest.mark.parametrize(
    ("token_side", "confirmed_price", "expected_exchange_price", "expected_intent"),
    [
        ("long", 0.47, "0.47", "ORDER_INTENT_BUY_LONG"),
        ("short", 0.63, "0.37", "ORDER_INTENT_BUY_SHORT"),
        ("short", 0.56, "0.44", "ORDER_INTENT_BUY_SHORT"),
    ],
)
def test_submit_converts_selected_outcome_price_to_exchange_long_coordinate(
    tmp_path,
    monkeypatch,
    token_side,
    confirmed_price,
    expected_exchange_price,
    expected_intent,
) -> None:
    audit_path = tmp_path / "events.jsonl"
    client = PolymarketExecutor(
        AuditLog(audit_path),
        confirm=lambda prompt: "y",
        environ=US_CREDS,
    )
    payloads = []

    def fake_request(method, path, payload):
        payloads.append(payload)
        return {"id": "coordinate-order-123", "state": "ORDER_STATE_NEW"}

    monkeypatch.setattr(client, "_request", fake_request)
    selected_ticket = OrderTicket(
        **{
            **ticket().__dict__,
            "token_side": token_side,
            "price": confirmed_price,
            "estimated_cost_usd": confirmed_price * ticket().size_shares,
        }
    )

    result = client.execute(
        selected_ticket,
        qualified_row(),
        execute_flag=True,
        user_command=True,
    )

    assert payloads[-1]["intent"] == expected_intent
    assert payloads[-1]["price"] == {
        "value": expected_exchange_price,
        "currency": "USD",
    }
    assert result["exchange_price"] == float(expected_exchange_price)
    event = AuditLog(audit_path).events()[-1]
    assert event["payload"]["price"] == confirmed_price
    assert event["payload"]["price_basis"] == "selected_outcome_probability"
    assert event["payload"]["exchange_price"] == float(expected_exchange_price)
    assert event["payload"]["exchange_price_basis"] == "long_side_probability"


def test_marketable_limit_uses_ioc_and_can_take_liquidity(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, answer="y", env=US_CREDS)
    payloads = []

    def fake_request(method, path, payload):
        payloads.append(payload)
        return {"id": "us-ioc-123", "state": "ORDER_STATE_FILLED"}

    monkeypatch.setattr(client, "_request", fake_request)
    marketable = OrderTicket(**{**ticket().__dict__, "order_type": "limit_ioc"})

    result = client.execute(
        marketable,
        qualified_row(),
        execute_flag=True,
        user_command=True,
    )

    assert result["status"] == "submitted"
    assert payloads[-1]["type"] == "ORDER_TYPE_LIMIT"
    assert payloads[-1]["tif"] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
    assert payloads[-1]["participateDontInitiate"] is False
    assert payloads[-1]["synchronousExecution"] is True


def test_portfolio_snapshot_uses_exchange_positions_and_activity_endpoints(
    tmp_path, monkeypatch
) -> None:
    client = executor(tmp_path, env=US_CREDS)
    responses = {
        "/v1/portfolio/positions": {"positions": {"market-1": {"netPositionDecimal": "2"}}},
        "/v1/portfolio/activities": {"activities": [{"trade": {"id": "trade-1"}}]},
        "/v1/account/balances": {"balances": [{"currency": "USD", "buyingPower": 20}]},
    }
    called = []

    def fake_request(method, path, payload=None):
        called.append((method, path, payload))
        return responses[path]

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.portfolio_snapshot()

    assert result["status"] == "live"
    assert result["positions"]["market-1"]["netPositionDecimal"] == "2"
    assert result["activities"][0]["trade"]["id"] == "trade-1"
    assert called == [
        ("GET", "/v1/portfolio/positions", None),
        ("GET", "/v1/portfolio/activities", None),
        ("GET", "/v1/account/balances", None),
    ]


def test_order_snapshots_read_authoritative_exchange_state(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, env=US_CREDS)
    called = []

    def fake_request(method, path, payload=None):
        called.append((method, path, payload))
        return {
            "order": {
                "id": "order-canceled-1",
                "state": "ORDER_STATE_CANCELED",
                "marketSlug": "wnba-example",
                "cumQuantity": 0,
                "leavesQuantity": 0,
            }
        }

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.order_snapshots(["order-canceled-1"])

    assert called == [("GET", "/v1/order/order-canceled-1", None)]
    assert result["orders"][0]["order_state"] == "ORDER_STATE_CANCELED"
    assert result["orders"][0]["leaves_quantity"] == 0
