from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from model_prediction.portfolio.polymarket_combos import (
    ComboExecutionError,
    ComboLeg,
    accept_combo_quote,
    build_combo_plan,
    combo_legs_from_pick_rows,
    create_combo_client_from_env,
    request_combo_quote,
    wait_for_combo_fill,
)


def _leg(position_id: str, probability: float = 0.7) -> ComboLeg:
    return ComboLeg(position_id, f"market-{position_id}", "moneyline", probability, "2026-10-01T00:00:00Z")


def test_combo_requires_explicit_joint_probability():
    with pytest.raises(ComboExecutionError, match="joint_probability"):
        build_combo_plan([_leg("a"), _leg("b")], amount_usd=5)


def test_combo_independence_is_explicit_and_deterministic():
    plan = build_combo_plan([_leg("a", 0.8), _leg("b", 0.5)], amount_usd=5, assume_independent=True)
    assert plan.joint_probability == pytest.approx(0.4)
    assert plan.amount_usd == Decimal(5)


def test_combo_rejects_duplicate_and_unsupported_legs():
    with pytest.raises(ComboExecutionError, match="unique"):
        build_combo_plan([_leg("a"), _leg("a")], amount_usd=5, joint_probability=0.4)
    with pytest.raises(ComboExecutionError, match="unsupported"):
        build_combo_plan(
            [_leg("a"), ComboLeg("b", "b", "nrfi", 0.5, "x")], amount_usd=5, joint_probability=0.4
        )


def test_combo_rows_require_native_position_ids():
    with pytest.raises(ComboExecutionError, match="combo_position_id"):
        combo_legs_from_pick_rows(
            [{"market_slug": "m", "market_type": "moneyline", "model_probability": 0.6}]
        )
    legs = combo_legs_from_pick_rows(
        [
            {
                "combo_position_id": "native-a",
                "market_slug": "m-a",
                "market_type": "moneyline",
                "model_probability": 0.6,
                "event_start_utc": "2026-10-01T00:00:00Z",
            }
        ]
    )
    assert legs[0].position_id == "native-a"


def test_combo_client_does_not_use_legacy_credentials(monkeypatch):
    monkeypatch.setenv("POLYMARKET_PLATFORM", "international")
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_BUILDER_API_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_BUILDER_SECRET", raising=False)
    monkeypatch.delenv("POLYMARKET_BUILDER_PASSPHRASE", raising=False)
    monkeypatch.setenv("POLYMARKET_KEY_ID", "legacy")
    with pytest.raises(ComboExecutionError, match="POLYMARKET_PRIVATE_KEY"):
        create_combo_client_from_env()


@dataclass
class Quote:
    expires_at: datetime


@dataclass
class Result:
    quote: Quote | None
    reason: str = ""


@dataclass
class Acceptance:
    status: str
    rfq_id: str = "rfq-1"


@dataclass
class Fill:
    status: str


class FakeClient:
    def __init__(self):
        self.calls = []

    def request_combo_quote(self, **kwargs):
        self.calls.append(("request", kwargs))
        return Result(Quote(datetime.now(UTC) + timedelta(seconds=5)))

    def accept_combo_quote(self, quote):
        self.calls.append(("accept", quote))
        return Acceptance("executing")

    def wait_for_combo_fill(self, **kwargs):
        self.calls.append(("wait", kwargs))
        return Fill("filled")


def test_combo_rfq_uses_native_joint_flow():
    client = FakeClient()
    plan = build_combo_plan([_leg("a"), _leg("b")], amount_usd=5, joint_probability=0.55)
    quoted = request_combo_quote(client, plan)
    accepted = accept_combo_quote(client, quoted)
    filled = wait_for_combo_fill(client, accepted)
    assert filled.status == "filled"
    assert [call[0] for call in client.calls] == ["request", "accept", "wait"]
    assert client.calls[0][1]["leg_position_ids"] == ["a", "b"]


def test_combo_does_not_accept_expired_quote():
    class Expired(FakeClient):
        def request_combo_quote(self, **kwargs):
            return Result(Quote(datetime.now(UTC) - timedelta(seconds=1)))

    client = Expired()
    plan = build_combo_plan([_leg("a"), _leg("b")], amount_usd=5, joint_probability=0.55)
    with pytest.raises(ComboExecutionError, match="expired"):
        accept_combo_quote(client, request_combo_quote(client, plan))
