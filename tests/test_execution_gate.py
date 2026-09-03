from datetime import UTC, datetime, timedelta

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
    # market_type/home_team/away_team/selection/event_start_utc default to a
    # valid moneyline row (matching fresh_quote()'s long/short) so every
    # pre-existing test that isn't specifically about side verification --
    # single_order policy, cost caps, decline/confirm, submit conversion --
    # still clears the live side/timing gate with the default fresh_quote()
    # the executor() helper now supplies. Tests that care about a specific
    # market type override these fields via moneyline_row()/spread_row()/etc.
    return {
        "pick_id": "pick-1",
        "record_type": "QUALIFIED_SHADOW_CALL",
        "status": "open",
        "rationale": "Learned LR call at threshold 0.55; executable ask 0.6200 "
        "(market_slug=aec-mlb-nyy-bos-2026-07-17).",
        "market_type": "moneyline",
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "selection": "home",
        "event_start_utc": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
    }


def executor(tmp_path, answer="y", env=None, live_quote=None) -> PolymarketExecutor:
    return PolymarketExecutor(
        AuditLog(tmp_path / "events.jsonl"),
        confirm=lambda prompt: answer,
        environ=env if env is not None else {},
        # Defaults to a quote matching qualified_row()'s moneyline shape so
        # tests unrelated to side verification don't all need to configure
        # one just to clear the (now real, for every market type) live
        # side/timing gate. Explicit live_quote= still overrides normally.
        live_quote=live_quote or (lambda slug: fresh_quote()),
    )


def moneyline_row(**overrides) -> dict[str, str]:
    row = {**qualified_row()}
    row.update(overrides)
    return row


def spread_row(**overrides) -> dict[str, str]:
    row = {
        **qualified_row(),
        "market_type": "spread",
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "selection": "away",
        "line": "-1.5",
        "event_start_utc": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
    }
    row.update(overrides)
    return row


def total_row(**overrides) -> dict[str, str]:
    row = {
        **qualified_row(),
        "market_type": "total",
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "selection": "over",
        "line": "8.5",
        "event_start_utc": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
    }
    row.update(overrides)
    return row


def btts_row(**overrides) -> dict[str, str]:
    row = {
        **qualified_row(),
        "market_type": "btts",
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "selection": "yes",
        "line": "",
        "event_start_utc": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
    }
    row.update(overrides)
    return row


def nrfi_row(**overrides) -> dict[str, str]:
    row = {
        **qualified_row(),
        "market_type": "nrfi",
        "selection": "nrfi",
        "line": "0.5",
        "event_start_utc": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
    }
    row.update(overrides)
    return row


def spread_quote(**overrides) -> dict:
    # Real shape verified live 2026-08-03 against captured Polymarket
    # contracts: the market's own line/team describe the LONG side (here,
    # "New York Yankees -1.5"); short is always the exact negation, the
    # opponent's own +1.5 ("Boston Red Sox +1.5").
    quote = {
        "market_slug": "asc-mlb-nyy-bos-2026-07-17-neg-1pt5",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "market_state": "MARKET_STATE_OPEN",
        "line": -1.5,
        "team": "New York Yankees",
        "long": {"description": "-1.50"},
        "short": {"description": "+1.50"},
    }
    quote.update(overrides)
    return quote


def total_quote(**overrides) -> dict:
    quote = {
        "market_slug": "asc-mlb-nyy-bos-2026-07-17-total-8pt5",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "market_state": "MARKET_STATE_OPEN",
        "line": 8.5,
        "long": {"description": "Over"},
        "short": {"description": "Under"},
    }
    quote.update(overrides)
    return quote


def btts_quote(**overrides) -> dict:
    quote = {
        "market_slug": "asc-mlb-nyy-bos-2026-07-17-btts",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "market_state": "MARKET_STATE_OPEN",
        "long": {"description": "Yes"},
        "short": {"description": "No"},
    }
    quote.update(overrides)
    return quote


def nrfi_quote(**overrides) -> dict:
    quote = {
        "market_slug": "astatc-mlb-nyy-bos-2026-07-17-yrfi",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "market_state": "MARKET_STATE_OPEN",
        "long": {"description": "Yes"},
        "short": {"description": "No"},
    }
    quote.update(overrides)
    return quote


def fresh_quote(**overrides) -> dict:
    quote = {
        "market_slug": "aec-mlb-nyy-bos-2026-07-17",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "market_state": "MARKET_STATE_OPEN",
        "long": {"description": "Boston Red Sox"},
        "short": {"description": "New York Yankees"},
    }
    quote.update(overrides)
    return quote


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
        executor(tmp_path, env={}).execute(ticket(), qualified_row(), execute_flag=True, user_command=True)


def test_research_observation_can_submit_without_manual_override(tmp_path, monkeypatch) -> None:
    """Operator directive, 2026-08-02: execution is no longer restricted by
    record_type -- "no restrictions, up to my discretion". A RESEARCH_OBSERVATION
    row (no manual_research_order flag) must be submittable through every
    other still-enforced gate, not refused purely for its classification."""
    client = executor(tmp_path, env=US_CREDS)
    monkeypatch.setattr(
        client, "_request", lambda method, path, payload: {"id": "order-1", "state": "ORDER_STATE_NEW"}
    )
    row = {**moneyline_row(), "record_type": "RESEARCH_OBSERVATION"}

    result = client.execute(ticket(), row, execute_flag=True, user_command=True)

    assert result["status"] == "submitted"


def test_explicit_manual_research_override_can_submit(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, answer="y", env=US_CREDS)
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, payload: {"id": "manual-order-123", "state": "ORDER_STATE_NEW"},
    )
    row = {
        **moneyline_row(),
        "record_type": "RESEARCH_OBSERVATION",
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


def test_single_order_policy_refuses_a_second_buy_on_the_same_pick(tmp_path, monkeypatch) -> None:
    """Real gap fixed 2026-08-02: nothing stopped a caller from previewing +
    submitting a second, independent order against the same still-open
    pick_id -- each one cleared every check separately, so one approved
    order could silently become two exchange instructions. Checked against
    the audit chain (not orders.json, dashboard-only) so this protects the
    raw CLI `execute` path too, not just the dashboard's own nonce/expiry."""
    client = executor(tmp_path, env=US_CREDS)
    orders = iter(
        [{"id": "order-1", "state": "ORDER_STATE_NEW"}, {"id": "order-2", "state": "ORDER_STATE_NEW"}]
    )
    monkeypatch.setattr(client, "_request", lambda method, path, payload: next(orders))

    first = client.execute(ticket(), qualified_row(), execute_flag=True, user_command=True)
    assert first["status"] == "submitted"

    with pytest.raises(ExecutionGateError, match="single_order policy refuses a second buy"):
        client.execute(ticket(), qualified_row(), execute_flag=True, user_command=True)


def test_single_order_policy_does_not_block_a_sell_closing_the_position(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, env=US_CREDS)
    orders = iter(
        [{"id": "order-1", "state": "ORDER_STATE_NEW"}, {"id": "order-2", "state": "ORDER_STATE_NEW"}]
    )
    monkeypatch.setattr(client, "_request", lambda method, path, payload: next(orders))

    bought = client.execute(ticket(), qualified_row(), execute_flag=True, user_command=True)
    assert bought["status"] == "submitted"

    sell_ticket = OrderTicket(**{**ticket().__dict__, "action": "sell", "maximum_cost_usd": None})
    sold = client.execute(sell_ticket, qualified_row(), execute_flag=True, user_command=True)
    assert sold["status"] == "submitted"


def test_single_order_policy_is_scoped_to_one_pick_id(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, env=US_CREDS)
    orders = iter(
        [{"id": "order-1", "state": "ORDER_STATE_NEW"}, {"id": "order-2", "state": "ORDER_STATE_NEW"}]
    )
    monkeypatch.setattr(client, "_request", lambda method, path, payload: next(orders))

    first = client.execute(ticket(), qualified_row(), execute_flag=True, user_command=True)
    assert first["status"] == "submitted"

    other_ticket = OrderTicket(**{**ticket().__dict__, "pick_id": "pick-2"})
    other_row = {**qualified_row(), "pick_id": "pick-2"}
    second = client.execute(other_ticket, other_row, execute_flag=True, user_command=True)
    assert second["status"] == "submitted"


def test_live_side_check_accepts_a_ticket_matching_the_row_selection(tmp_path, monkeypatch) -> None:
    """Real bug fixed 2026-08-02: the executor bound pick_id and market_slug
    but never independently confirmed token_side matched the row's actual
    selection -- the dashboard's own preview flow derives side correctly
    before ever building a ticket, but the raw CLI `execute` command builds
    one straight from user-typed --side/--action args, and this executor is
    the one chokepoint both paths share. This verifies the matching case
    still submits normally once the live-quote check is wired in."""
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: fresh_quote())
    monkeypatch.setattr(
        client, "_request", lambda method, path, payload: {"id": "order-1", "state": "ORDER_STATE_NEW"}
    )

    result = client.execute(ticket(), moneyline_row(), execute_flag=True, user_command=True)

    assert result["status"] == "submitted"


def test_live_side_check_refuses_a_ticket_on_the_wrong_side(tmp_path) -> None:
    # ticket() is token_side="long"; the live quote's "long" side is the away
    # team (Yankees), but moneyline_row()'s selection is "home" (Red Sox) --
    # a real mismatch a raw --side long/short typo could produce.
    mismatched_quote = fresh_quote(
        long={"description": "New York Yankees"}, short={"description": "Boston Red Sox"}
    )
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: mismatched_quote)
    with pytest.raises(ExecutionGateError, match="does not match the live market side"):
        client.execute(ticket(), moneyline_row(), execute_flag=True, user_command=True)


def test_live_side_check_refuses_a_stale_quote(tmp_path) -> None:
    stale_quote = fresh_quote(observed_at_utc=(datetime.now(UTC) - timedelta(minutes=10)).isoformat())
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: stale_quote)
    with pytest.raises(ExecutionGateError, match="live quote is stale"):
        client.execute(ticket(), moneyline_row(), execute_flag=True, user_command=True)


def test_live_side_check_refuses_a_closed_market(tmp_path) -> None:
    closed_quote = fresh_quote(market_state="MARKET_STATE_CLOSED")
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: closed_quote)
    with pytest.raises(ExecutionGateError, match="market is not open"):
        client.execute(ticket(), moneyline_row(), execute_flag=True, user_command=True)


def test_live_side_check_refuses_after_event_start(tmp_path) -> None:
    started_row = moneyline_row(event_start_utc=(datetime.now(UTC) - timedelta(minutes=5)).isoformat())
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: fresh_quote())
    with pytest.raises(ExecutionGateError, match="event has already started"):
        client.execute(ticket(), started_row, execute_flag=True, user_command=True)


def test_live_side_check_refuses_an_unrecognized_market_type(tmp_path) -> None:
    """P0-1 (2026-08-03): previously an absent/unrecognized market_type
    silently SKIPPED the live side check entirely, relying only on the
    market_slug-from-rationale binding -- a malformed or unexpected row
    would submit unverified. Every real row from the pipeline always sets
    market_type (a required PickRequest field), so this must now fail
    closed rather than silently pass through."""
    client = executor(tmp_path, env=US_CREDS)
    row = {**qualified_row(), "market_type": "unknown_market_type"}
    with pytest.raises(ExecutionGateError, match="no live side resolver"):
        client.execute(ticket(), row, execute_flag=True, user_command=True)


def test_live_side_check_accepts_a_spread_ticket_matching_the_row_selection(tmp_path, monkeypatch) -> None:
    """Real gap closed 2026-08-03: spread/total/btts used to have no live
    side resolver at all and fell through the check unverified. A spread
    market's own line/team always describe its long side (verified live
    against real captured Polymarket contracts -- see _resolve_spread_side);
    the row here picks the away team (Yankees) at -1.5, matching the live
    quote's long side exactly."""
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: spread_quote())
    monkeypatch.setattr(
        client, "_request", lambda method, path, payload: {"id": "order-1", "state": "ORDER_STATE_NEW"}
    )
    row = spread_row(selection="away", line="-1.5")

    result = client.execute(ticket(), row, execute_flag=True, user_command=True)

    assert result["status"] == "submitted"


def test_live_side_check_refuses_a_spread_ticket_on_the_wrong_side(tmp_path) -> None:
    # ticket() is token_side="long"; the row picks the home team (Red Sox)
    # at +1.5, which is the live quote's SHORT side (the negation of the
    # market's own Yankees -1.5) -- a real --side typo this must catch.
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: spread_quote())
    row = spread_row(selection="home", line="1.5")
    with pytest.raises(ExecutionGateError, match="does not match the live market side"):
        client.execute(ticket(), row, execute_flag=True, user_command=True)


def test_live_side_check_refuses_a_spread_ticket_on_a_stale_line(tmp_path) -> None:
    """The market moved (or the ticket's line is simply wrong) -- Yankees
    -2.5 no longer matches the live -1.5 contract this ticket's market_slug
    actually resolves to. Must refuse rather than execute at the wrong
    number."""
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: spread_quote())
    row = spread_row(selection="away", line="-2.5")
    with pytest.raises(ExecutionGateError, match="could not unambiguously resolve"):
        client.execute(ticket(), row, execute_flag=True, user_command=True)


def test_live_side_check_accepts_a_total_ticket_matching_the_row_selection(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: total_quote())
    monkeypatch.setattr(
        client, "_request", lambda method, path, payload: {"id": "order-1", "state": "ORDER_STATE_NEW"}
    )
    row = total_row(selection="over", line="8.5")

    result = client.execute(ticket(), row, execute_flag=True, user_command=True)

    assert result["status"] == "submitted"


def test_live_side_check_refuses_a_total_ticket_on_the_wrong_side(tmp_path) -> None:
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: total_quote())
    row = total_row(selection="under", line="8.5")
    with pytest.raises(ExecutionGateError, match="does not match the live market side"):
        client.execute(ticket(), row, execute_flag=True, user_command=True)


def test_live_side_check_refuses_a_total_ticket_on_a_stale_line(tmp_path) -> None:
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: total_quote())
    row = total_row(selection="over", line="9.5")
    with pytest.raises(ExecutionGateError, match="does not match the live market's total line"):
        client.execute(ticket(), row, execute_flag=True, user_command=True)


def test_live_side_check_accepts_a_btts_ticket_matching_the_row_selection(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: btts_quote())
    monkeypatch.setattr(
        client, "_request", lambda method, path, payload: {"id": "order-1", "state": "ORDER_STATE_NEW"}
    )
    row = btts_row(selection="yes")

    result = client.execute(ticket(), row, execute_flag=True, user_command=True)

    assert result["status"] == "submitted"


def test_live_side_check_refuses_a_btts_ticket_on_the_wrong_side(tmp_path) -> None:
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: btts_quote())
    row = btts_row(selection="no")
    with pytest.raises(ExecutionGateError, match="does not match the live market side"):
        client.execute(ticket(), row, execute_flag=True, user_command=True)


def test_live_side_check_accepts_nrfi_on_the_no_side(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: nrfi_quote())
    monkeypatch.setattr(
        client, "_request", lambda method, path, payload: {"id": "order-1", "state": "ORDER_STATE_NEW"}
    )
    base_ticket = ticket()
    nrfi_ticket = OrderTicket(**{**base_ticket.__dict__, "token_side": "short"})

    result = client.execute(nrfi_ticket, nrfi_row(), execute_flag=True, user_command=True)

    assert result["status"] == "submitted"


def test_live_side_check_refuses_nrfi_on_the_yes_side(tmp_path) -> None:
    client = executor(tmp_path, env=US_CREDS, live_quote=lambda slug: nrfi_quote())
    with pytest.raises(ExecutionGateError, match="does not match the live market side"):
        client.execute(ticket(), nrfi_row(), execute_flag=True, user_command=True)


def test_config_override_qualified_call_can_submit_without_manual_order(tmp_path, monkeypatch) -> None:
    """Operator directive, 2026-08-02: a row reading QUALIFIED_SHADOW_CALL
    purely via config's qualification_override (artifact itself not
    genuinely qualified) is no longer refused for that reason -- execution
    restrictions based on record_type/artifact qualification are removed."""
    client = executor(tmp_path, env=US_CREDS)
    monkeypatch.setattr(
        client, "_request", lambda method, path, payload: {"id": "order-1", "state": "ORDER_STATE_NEW"}
    )

    result = client.execute(
        ticket(), qualified_row(), execute_flag=True, user_command=True, artifact_qualified=False
    )

    assert result["status"] == "submitted"


def test_config_override_qualified_call_can_submit_via_manual_order(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, answer="y", env=US_CREDS)
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, payload: {"id": "override-order-1", "state": "ORDER_STATE_NEW"},
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
        live_quote=lambda slug: fresh_quote(),
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

    # fresh_quote()'s long/short are Red Sox (home)/Yankees (away); the row's
    # selection must match whichever side this case is exercising, or the
    # live side check (real for every market type as of P0-1) would refuse
    # a "short" ticket paired with the row's default home/long selection.
    result = client.execute(
        selected_ticket,
        moneyline_row(selection="home" if token_side == "long" else "away"),
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


def test_ioc_partial_fill_rests_remainder_at_same_price_not_chased(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, answer="y", env=US_CREDS)
    post_payloads = []

    def fake_request(method, path, payload=None):
        assert path == "/v1/orders"
        post_payloads.append(payload)
        if len(post_payloads) == 1:
            return {"id": "primary-partial", "state": "ORDER_STATE_PARTIALLY_FILLED", "cumQuantity": 0.88}
        return {"id": "resting-remainder", "state": "ORDER_STATE_NEW", "cumQuantity": 0.0}

    monkeypatch.setattr(client, "_request", fake_request)
    partial_ticket = OrderTicket(
        **{
            **ticket().__dict__,
            "order_type": "limit_ioc",
            "price": 0.51,
            "size_shares": 12.25,
            "estimated_cost_usd": 6.25,
            "maximum_cost_usd": 6.35,
            "ioc_fallback_resting": True,
        }
    )

    result = client.execute(partial_ticket, qualified_row(), execute_flag=True, user_command=True)

    assert len(post_payloads) == 2
    # The fallback must rest at the SAME price as the original IOC -- never
    # a higher, "chased" price.
    assert post_payloads[0]["price"]["value"] == "0.51"
    assert post_payloads[1]["price"]["value"] == "0.51"
    assert post_payloads[1]["quantity"] == 11.37
    assert post_payloads[1]["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    assert post_payloads[1]["participateDontInitiate"] is True
    assert post_payloads[1]["synchronousExecution"] is False
    assert result["order_ids"] == ["primary-partial", "resting-remainder"]
    assert result["fallback_order_id"] == "resting-remainder"
    assert result["fallback_status"] == "resting"
    assert result["fallback_resting_shares"] == 11.37
    # Only the confirmed primary fill counts toward filled shares/cost --
    # the resting order hasn't filled yet.
    assert result["filled_size_shares"] == 0.88
    assert result["estimated_filled_cost_usd"] == 0.4488
    assert result["order_state"] == "ORDER_STATE_PARTIALLY_FILLED"


def test_ioc_zero_fill_rests_full_remainder(tmp_path, monkeypatch) -> None:
    client = executor(tmp_path, answer="y", env=US_CREDS)
    post_payloads = []

    def fake_request(method, path, payload=None):
        post_payloads.append(payload)
        if len(post_payloads) == 1:
            return {"id": "primary-expired", "state": "ORDER_STATE_EXPIRED"}
        return {"id": "resting-full", "state": "ORDER_STATE_NEW", "cumQuantity": 0.0}

    monkeypatch.setattr(client, "_request", fake_request)
    zero_fill_ticket = OrderTicket(
        **{
            **ticket().__dict__,
            "order_type": "limit_ioc",
            "price": 0.43,
            "size_shares": 14.88,
            "estimated_cost_usd": 6.4,
            "maximum_cost_usd": 6.5,
            "ioc_fallback_resting": True,
        }
    )

    result = client.execute(zero_fill_ticket, qualified_row(), execute_flag=True, user_command=True)

    assert result["fallback_order_id"] == "resting-full"
    assert result["fallback_resting_shares"] == 14.88
    assert result["filled_size_shares"] == 0.0
    assert result["estimated_filled_cost_usd"] == 0.0
    assert result["order_state"] == "ORDER_STATE_PARTIALLY_FILLED"


def test_ioc_partial_fill_without_resting_flag_leaves_remainder_unfilled(tmp_path, monkeypatch) -> None:
    """Opt-in only: a ticket that doesn't set ioc_fallback_resting never
    submits a second order, matching plain historical IOC behavior."""
    client = executor(tmp_path, answer="y", env=US_CREDS)
    post_payloads = []

    def fake_request(method, path, payload=None):
        post_payloads.append(payload)
        return {"id": "primary-partial", "state": "ORDER_STATE_PARTIALLY_FILLED", "cumQuantity": 0.88}

    monkeypatch.setattr(client, "_request", fake_request)
    partial_ticket = OrderTicket(
        **{
            **ticket().__dict__,
            "order_type": "limit_ioc",
            "price": 0.51,
            "size_shares": 12.25,
            "estimated_cost_usd": 6.25,
            "maximum_cost_usd": 6.35,
        }
    )

    result = client.execute(partial_ticket, qualified_row(), execute_flag=True, user_command=True)

    assert len(post_payloads) == 1
    assert result["order_ids"] == ["primary-partial"]
    assert result["fallback_order_id"] is None
    assert result["filled_size_shares"] == 0.88


def test_portfolio_snapshot_uses_exchange_positions_and_activity_endpoints(tmp_path, monkeypatch) -> None:
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
