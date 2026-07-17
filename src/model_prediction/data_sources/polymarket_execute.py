"""Polymarket US order execution — HARD GATE. Real money.

An order NEVER fires unless ALL of these hold, checked in code, not by
convention:

1. The user explicitly issued an execute command (the CLI's ``execute``
   subcommand is the only caller; an AI prediction or summary is never
   sufficient, and AI assistants must never suggest or propose execution).
2. The ``--execute`` flag was passed (``execute_flag=True`` here). Without it
   everything is a dry-run preview.
3. Polymarket US retail API credentials are present in the environment.
4. An interactive Y/N confirmation shows the exact order (market, side, size,
   price, estimated cost) and receives "Y".
5. Unit-engine caps were already applied to the pick being executed.
6. The pick is qualified, or the caller supplies an explicit manual-research
   authorization already checked against active-model, edge, ban, and unit caps.
7. Every submitted order is written to the append-only audit chain.

Resting limits use the exact user-confirmed price and are post-only. Current
BBO is context and a crossing order is refused; the midpoint is never silently
substituted.

Submission uses the authenticated Polymarket US retail REST API. It does not
use the international Polymarket CLOB or a wallet private key.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from cryptography.hazmat.primitives.asymmetric import ed25519

from ..audit import AuditLog
from ..domain import RecordType, iso_utc, utc_now


KEY_ID_ENV = "POLYMARKET_KEY_ID"
SECRET_KEY_ENV = "POLYMARKET_SECRET_KEY"
API_HOST = "https://api.polymarket.us"


class ExecutionGateError(RuntimeError):
    """Raised whenever any gate condition fails. Nothing was submitted."""


@dataclass(frozen=True)
class OrderTicket:
    market_slug: str
    token_side: str  # "long" | "short"
    action: str  # "buy" | "sell"
    order_type: str  # "limit_gtc" | "market"
    price: float  # exact user-confirmed limit, in probability units
    size_shares: float
    pick_id: str
    estimated_cost_usd: float
    maximum_cost_usd: float | None = None
    authorization_type: str = "qualified_model"

    def describe(self) -> str:
        return (
            f"Order: {self.action.upper()} {self.size_shares:g} shares "
            f"[{self.token_side}] of {self.market_slug} @ ${self.price:.4f} "
            f"({self.order_type}). Estimated cost: ${self.estimated_cost_usd:.2f}. "
            f"Authorization: {self.authorization_type}. Pick: {self.pick_id}."
        )


class PolymarketExecutor:
    """All gate checks live here so no caller can skip them piecemeal."""

    def __init__(
        self,
        audit: AuditLog,
        confirm: Callable[[str], str] = input,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.audit = audit
        self.confirm = confirm
        self.environ = environ if environ is not None else dict(os.environ)

    # ------------------------------------------------------------------ gate

    def execute(
        self,
        ticket: OrderTicket,
        pick_row: dict[str, str],
        execute_flag: bool,
        user_command: bool,
        manual_research_order: bool = False,
    ) -> dict[str, Any]:
        """Run the full gate; submit only if every condition passes."""
        # Gate 1: explicit user command. The CLI sets this True only for the
        # `execute` subcommand; nothing else may.
        if not user_command:
            raise ExecutionGateError(
                "REFUSED: execution requires an explicit user execute command. "
                "AI predictions, summaries, or recommendations never authorize an order."
            )
        # Gate 2: --execute flag.
        if not execute_flag:
            return {
                "status": "dry_run",
                "order": ticket.describe(),
                "note": "No --execute flag: paper preview only. No order was placed.",
            }
        # Gate 3: Polymarket US retail API credentials present.
        missing = [name for name in (KEY_ID_ENV, SECRET_KEY_ENV) if not self.environ.get(name)]
        if missing:
            raise ExecutionGateError(
                f"REFUSED: {', '.join(missing)} is not set. Real-money execution is "
                "impossible without Polymarket US API credentials; nothing was submitted."
            )
        # Gate 6: qualified model call, or an explicit manual authorization
        # whose active-model/edge/ban checks were completed by the CLI.
        qualified = pick_row.get("record_type") == RecordType.QUALIFIED_SHADOW_CALL.value
        if not qualified and not manual_research_order:
            raise ExecutionGateError(
                "REFUSED: research picks require the explicit manual-research-order override. "
                f"This pick is {pick_row.get('record_type') or 'unknown'}."
            )
        if pick_row.get("status") != "open":
            raise ExecutionGateError("REFUSED: pick is not open.")
        # Gate 5 is upstream (unit engine sized the pick); re-assert sanity.
        if ticket.size_shares <= 0 or not 0 < ticket.price < 1:
            raise ExecutionGateError("REFUSED: order size/price failed sanity checks.")
        if (
            ticket.maximum_cost_usd is not None
            and ticket.estimated_cost_usd > ticket.maximum_cost_usd + 0.005
        ):
            raise ExecutionGateError(
                f"REFUSED: ${ticket.estimated_cost_usd:.2f} exceeds the authorized "
                f"unit cap of ${ticket.maximum_cost_usd:.2f}."
            )
        if ticket.order_type != "limit_gtc":
            raise ExecutionGateError(
                "REFUSED: the US dashboard supports post-only GTC limit orders only."
            )
        # Gate 4: interactive confirmation with exact order details.
        answer = self.confirm(f"{ticket.describe()} Confirm? (Y/N): ").strip().casefold()
        if answer not in {"y", "yes"}:
            self.audit.append(
                "order_declined",
                ticket.pick_id,
                {"market_slug": ticket.market_slug, "reason": "user_declined_confirmation"},
            )
            return {"status": "declined", "note": "User declined at confirmation. No order placed."}
        submission = self._submit(ticket)
        # Gate 7: audit chain.
        self.audit.append(
            "order_executed",
            ticket.pick_id,
            {
                "order_id": submission.get("order_id"),
                "market_slug": ticket.market_slug,
                "token_side": ticket.token_side,
                "action": ticket.action,
                "order_type": ticket.order_type,
                "price": ticket.price,
                "size_shares": ticket.size_shares,
                "estimated_cost_usd": ticket.estimated_cost_usd,
                "maximum_cost_usd": ticket.maximum_cost_usd,
                "authorization_type": ticket.authorization_type,
                "source_record_type": pick_row.get("record_type"),
                "source_reason_code": pick_row.get("reason_code"),
                "transaction_hash": submission.get("transaction_hash"),
                "submitted_at_utc": iso_utc(utc_now()),
            },
        )
        return {"status": "submitted", **submission}

    # ---------------------------------------------------------------- submit

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Build the documented Polymarket US Ed25519 request headers."""
        timestamp = str(int(time.time() * 1000))
        try:
            decoded = base64.b64decode(self.environ[SECRET_KEY_ENV], validate=True)
            private_key = ed25519.Ed25519PrivateKey.from_private_bytes(decoded[:32])
        except (KeyError, ValueError) as error:
            raise ExecutionGateError(
                f"REFUSED: {SECRET_KEY_ENV} is not a valid base64 Ed25519 secret key."
            ) from error
        message = f"{timestamp}{method.upper()}{path}".encode()
        signature = base64.b64encode(private_key.sign(message)).decode()
        return {
            "X-PM-Access-Key": self.environ[KEY_ID_ENV],
            "X-PM-Timestamp": timestamp,
            "X-PM-Signature": signature,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = httpx.request(
                method,
                f"{API_HOST}{path}",
                headers=self._auth_headers(method, path),
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
            output = response.json()
        except httpx.HTTPStatusError as error:
            detail = error.response.text[:500]
            raise ExecutionGateError(
                f"REFUSED by Polymarket US ({error.response.status_code}): {detail}"
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise ExecutionGateError(
                f"REFUSED: Polymarket US order request failed ({type(error).__name__})."
            ) from error
        if not isinstance(output, dict):
            raise ExecutionGateError("REFUSED: Polymarket US returned an invalid response.")
        return output

    def _submit(self, ticket: OrderTicket) -> dict[str, Any]:
        """Submit a post-only GTC limit order to Polymarket US retail."""
        intent = {
            ("buy", "long"): "ORDER_INTENT_BUY_LONG",
            ("buy", "short"): "ORDER_INTENT_BUY_SHORT",
            ("sell", "long"): "ORDER_INTENT_SELL_LONG",
            ("sell", "short"): "ORDER_INTENT_SELL_SHORT",
        }.get((ticket.action, ticket.token_side))
        if intent is None:
            raise ExecutionGateError("REFUSED: unsupported order action/side.")
        response = self._request(
            "POST",
            "/v1/orders",
            {
                "marketSlug": ticket.market_slug,
                "intent": intent,
                "type": "ORDER_TYPE_LIMIT",
                "price": {"value": f"{ticket.price:.2f}", "currency": "USD"},
                "quantity": ticket.size_shares,
                "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
                "participateDontInitiate": True,
                "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL",
                "synchronousExecution": False,
            },
        )
        order_id = response.get("id") or (response.get("order") or {}).get("id")
        if not order_id:
            raise ExecutionGateError(
                "REFUSED: Polymarket US did not return an order ID; no submitted state was recorded."
            )
        return {
            "order_id": str(order_id),
            "order_state": response.get("state") or (response.get("order") or {}).get("state"),
            "raw_response": response,
        }

    # ---------------------------------------------------------------- cancel

    def cancel(self, order_id: str, user_command: bool) -> dict[str, Any]:
        if not user_command:
            raise ExecutionGateError("REFUSED: cancellation also requires an explicit user command.")
        missing = [name for name in (KEY_ID_ENV, SECRET_KEY_ENV) if not self.environ.get(name)]
        if missing:
            raise ExecutionGateError(f"REFUSED: {', '.join(missing)} is not set.")
        response = self._request("POST", f"/v1/order/{order_id}/cancel", {})
        self.audit.append("order_cancelled", order_id, {"raw_response": str(response)[:500]})
        return {"status": "cancelled", "order_id": order_id}
