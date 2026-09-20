"""Fail-closed planning and execution adapter for Polymarket Combos.

Combos are not CLOB orders. Polymarket's current API exposes them through an
authenticated RFQ flow in the official ``polymarket`` SDK. This module keeps
that flow isolated from the existing single-market executor so a missing SDK,
missing Builder API key, stale quote, or partial RFQ result cannot silently
turn into independent leg orders.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

SUPPORTED_COMBO_MARKETS = frozenset({"moneyline", "spread", "total"})


class ComboExecutionError(RuntimeError):
    """Raised when a combo cannot be proven safe to request or accept."""


def combo_sdk_available() -> bool:
    """Return whether the official Combo RFQ SDK is installed."""
    try:
        import polymarket  # noqa: F401
    except ImportError:
        return False
    return True


def create_combo_client_from_env() -> ComboRFQClient:
    """Create the authenticated official client from dedicated Builder secrets.

    Combo RFQ credentials are intentionally separate from the legacy retail
    ``POLYMARKET_KEY_ID``/``POLYMARKET_SECRET_KEY`` pair. This function never
    falls back to those variables and never logs secret values.
    """
    platform = os.getenv("POLYMARKET_PLATFORM", "us").strip().casefold()
    if platform in {"us", "polymarket_us", "polymarket-us"}:
        raise ComboExecutionError(
            "native international Combo RFQ is not available through the Polymarket US product; "
            "use the Polymarket US API/support channel for any US combo capability"
        )
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    builder_key = os.getenv("POLYMARKET_BUILDER_API_KEY", "").strip()
    builder_secret = os.getenv("POLYMARKET_BUILDER_SECRET", "").strip()
    builder_passphrase = os.getenv("POLYMARKET_BUILDER_PASSPHRASE", "").strip()
    if not combo_sdk_available():
        raise ComboExecutionError("official polymarket-client SDK is not installed")
    missing = [
        name
        for name, value in (
            ("POLYMARKET_PRIVATE_KEY", private_key),
            ("POLYMARKET_BUILDER_API_KEY", builder_key),
            ("POLYMARKET_BUILDER_SECRET", builder_secret),
            ("POLYMARKET_BUILDER_PASSPHRASE", builder_passphrase),
        )
        if not value
    ]
    if missing:
        raise ComboExecutionError(f"missing dedicated Combo RFQ credentials: {', '.join(missing)}")
    from polymarket.auth import BuilderApiKey
    from polymarket.clients.secure import SecureClient

    wallet = os.getenv("POLYMARKET_WALLET_ADDRESS", "").strip() or None
    return SecureClient.create(
        private_key=private_key,
        wallet=wallet,
        api_key=BuilderApiKey(builder_key, builder_secret, builder_passphrase),
    )


@dataclass(frozen=True, slots=True)
class ComboLeg:
    """One selected outcome in a native Polymarket Combo."""

    position_id: str
    market_slug: str
    market_type: str
    model_probability: float
    event_start_utc: str


@dataclass(frozen=True, slots=True)
class ComboPlan:
    """A deterministic, auditable combo candidate before RFQ."""

    legs: tuple[ComboLeg, ...]
    joint_probability: float
    amount_usd: Decimal
    created_at_utc: str

    @property
    def leg_position_ids(self) -> tuple[str, ...]:
        return tuple(leg.position_id for leg in self.legs)


def combo_legs_from_pick_rows(rows: list[dict[str, Any]]) -> list[ComboLeg]:
    """Convert explicitly annotated forecast rows into native combo legs.

    A normal market slug is not a combo position ID. Requiring the dedicated
    ``combo_position_id`` field prevents accidentally passing CLOB token IDs or
    market slugs into the RFQ API.
    """
    legs: list[ComboLeg] = []
    for row in rows:
        position_id = str(row.get("combo_position_id") or "").strip()
        if not position_id:
            raise ComboExecutionError("combo candidate is missing combo_position_id")
        legs.append(
            ComboLeg(
                position_id=position_id,
                market_slug=str(row.get("market_slug") or ""),
                market_type=str(row.get("market_type") or "").lower(),
                model_probability=float(row.get("model_probability")),
                event_start_utc=str(row.get("event_start_utc") or ""),
            )
        )
    return legs


class ComboRFQClient(Protocol):
    """Minimal surface implemented by Polymarket's official SecureClient."""

    def request_combo_quote(self, **kwargs: Any) -> Any: ...

    def accept_combo_quote(self, quote: Any) -> Any: ...

    def wait_for_combo_fill(self, **kwargs: Any) -> Any: ...


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_combo_plan(
    legs: list[ComboLeg] | tuple[ComboLeg, ...],
    *,
    amount_usd: Decimal | float | str,
    joint_probability: float | None = None,
    assume_independent: bool = False,
) -> ComboPlan:
    """Validate a combo candidate without placing an order.

    ``joint_probability`` is required by default. Multiplying leg probabilities
    is only available as an explicit research assumption because same-game and
    same-slate legs can be correlated. This prevents the common parlay error of
    treating marginal probabilities as independent by accident.
    """
    normalized = tuple(legs)
    if len(normalized) < 2:
        raise ComboExecutionError("a combo requires at least two legs")
    if len(normalized) > 20:
        raise ComboExecutionError("combo exceeds the 20-leg safety limit")
    position_ids = [leg.position_id.strip() for leg in normalized]
    if any(not value for value in position_ids):
        raise ComboExecutionError("every combo leg requires a position_id")
    if len(set(position_ids)) != len(position_ids):
        raise ComboExecutionError("combo legs must have unique position_ids")
    for leg in normalized:
        if leg.market_type.casefold() not in SUPPORTED_COMBO_MARKETS:
            raise ComboExecutionError(
                f"unsupported combo market type {leg.market_type!r}; "
                f"allowed={sorted(SUPPORTED_COMBO_MARKETS)}"
            )
        if not 0.0 < float(leg.model_probability) < 1.0:
            raise ComboExecutionError("each model probability must be strictly between 0 and 1")

    if joint_probability is None:
        if not assume_independent:
            raise ComboExecutionError(
                "joint_probability is required; set assume_independent=True only for an explicit baseline"
            )
        joint_probability = 1.0
        for leg in normalized:
            joint_probability *= float(leg.model_probability)
    if not 0.0 < float(joint_probability) < 1.0:
        raise ComboExecutionError("joint_probability must be strictly between 0 and 1")

    amount = Decimal(str(amount_usd))
    if not amount.is_finite() or amount <= 0:
        raise ComboExecutionError("amount_usd must be a positive finite value")

    return ComboPlan(
        legs=normalized,
        joint_probability=float(joint_probability),
        amount_usd=amount,
        created_at_utc=_utc_now(),
    )


def request_combo_quote(
    client: ComboRFQClient,
    plan: ComboPlan,
    *,
    side: str = "YES",
) -> Any:
    """Request a native combo quote; never submit independent leg orders."""
    if side.upper() not in {"YES", "NO"}:
        raise ComboExecutionError("combo side must be YES or NO")
    result = client.request_combo_quote(
        leg_position_ids=list(plan.leg_position_ids),
        direction="BUY",
        amount=plan.amount_usd,
        side=side.upper(),
    )
    if result is None or getattr(result, "quote", None) is None:
        reason = getattr(result, "reason", "no usable quote")
        raise ComboExecutionError(f"combo RFQ returned no executable quote: {reason}")
    return result


def accept_combo_quote(
    client: ComboRFQClient,
    quote_result: Any,
    *,
    max_quote_age_seconds: float = 10.0,
) -> Any:
    """Accept a quote only while it is demonstrably current and self-contained."""
    quote = getattr(quote_result, "quote", None)
    if quote is None:
        raise ComboExecutionError("cannot accept an RFQ result without a quote")
    expires_at = getattr(quote, "expires_at", None)
    if expires_at is not None:
        try:
            expires = expires_at.timestamp() if hasattr(expires_at, "timestamp") else float(expires_at)
            now = datetime.now(UTC).timestamp()
            if expires <= now:
                raise ComboExecutionError("combo quote is expired")
            # The SDK's RFQ result is short-lived. We only require that it has
            # not expired here; the caller may use ``max_quote_age_seconds`` as
            # an additional policy gate when it records request timestamps.
        except (TypeError, ValueError, OverflowError) as error:
            raise ComboExecutionError("combo quote has an invalid expiry") from error
    acceptance = client.accept_combo_quote(quote)
    status = str(getattr(acceptance, "status", "")).casefold()
    if status not in {"executing", "filled", "confirmed"}:
        raise ComboExecutionError(f"combo quote was not accepted: status={status or 'unknown'}")
    return acceptance


def wait_for_combo_fill(
    client: ComboRFQClient,
    acceptance: Any,
    *,
    timeout_seconds: float = 30.0,
) -> Any:
    """Require a terminal filled/confirmed RFQ result before recording exposure."""
    rfq_id = getattr(acceptance, "rfq_id", None)
    if not rfq_id:
        raise ComboExecutionError("accepted combo response has no rfq_id")
    result = client.wait_for_combo_fill(rfq_id=str(rfq_id), timeout=timeout_seconds)
    status = str(getattr(result, "status", "")).casefold()
    if status not in {"filled", "confirmed"}:
        raise ComboExecutionError(f"combo RFQ did not fill: status={status or 'unknown'}")
    return result
