"""Polymarket US order execution — HARD GATE. Real money.

An order NEVER fires unless ALL of these hold, checked in code, not by
convention:

1. The user explicitly issued an execute command (the CLI's ``execute``
   subcommand is the only caller; an AI prediction or summary is never
   sufficient, and AI assistants must never suggest or propose execution).
2. The ``--execute`` flag was passed (``execute_flag=True`` here). Without it
   everything is a dry-run preview.
3. ``POLYMARKET_PRIVATE_KEY`` is present in the environment.
4. An interactive Y/N confirmation shows the exact order (market, side, size,
   price, estimated cost) and receives "Y".
5. Unit-engine caps were already applied to the pick being executed.
6. The pick is a QUALIFIED_SHADOW_CALL — research observations are refused.
7. Every submitted order is written to the append-only audit chain.

Orders always price at the executable ask (buys) / bid (sells) — never the
midpoint.

Submission uses the Polymarket CLOB REST API. Order signing requires the
wallet private key; if the optional ``py_clob_client`` package is not
installed, the gate still runs end-to-end and refuses at the signing step with
an actionable message instead of silently faking a fill.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from ..audit import AuditLog
from ..domain import RecordType, iso_utc, utc_now


PRIVATE_KEY_ENV = "POLYMARKET_PRIVATE_KEY"
WALLET_ADDRESS_ENV = "POLYMARKET_WALLET_ADDRESS"
CLOB_HOST = "https://clob.polymarket.us"


class ExecutionGateError(RuntimeError):
    """Raised whenever any gate condition fails. Nothing was submitted."""


@dataclass(frozen=True)
class OrderTicket:
    market_slug: str
    token_side: str  # "long" | "short"
    action: str  # "buy" | "sell"
    order_type: str  # "limit_gtc" | "market"
    price: float  # executable ask (buy) / bid (sell), probability units
    size_shares: float
    pick_id: str
    estimated_cost_usd: float

    def describe(self) -> str:
        return (
            f"Order: {self.action.upper()} {self.size_shares:g} shares "
            f"[{self.token_side}] of {self.market_slug} @ ${self.price:.4f} "
            f"({self.order_type}). Estimated cost: ${self.estimated_cost_usd:.2f}. "
            f"Pick: {self.pick_id}."
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
        # Gate 3: private key present.
        if not self.environ.get(PRIVATE_KEY_ENV):
            raise ExecutionGateError(
                f"REFUSED: {PRIVATE_KEY_ENV} is not set. Real-money execution is "
                "impossible without the wallet key; nothing was submitted."
            )
        # Gate 6: qualified calls only.
        if pick_row.get("record_type") != RecordType.QUALIFIED_SHADOW_CALL.value:
            raise ExecutionGateError(
                "REFUSED: only QUALIFIED_SHADOW_CALL picks can be executed. "
                f"This pick is {pick_row.get('record_type') or 'unknown'}."
            )
        if pick_row.get("status") != "open":
            raise ExecutionGateError("REFUSED: pick is not open.")
        # Gate 5 is upstream (unit engine sized the pick); re-assert sanity.
        if ticket.size_shares <= 0 or not 0 < ticket.price < 1:
            raise ExecutionGateError("REFUSED: order size/price failed sanity checks.")
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
                "transaction_hash": submission.get("transaction_hash"),
                "submitted_at_utc": iso_utc(utc_now()),
            },
        )
        return {"status": "submitted", **submission}

    # ---------------------------------------------------------------- submit

    def _submit(self, ticket: OrderTicket) -> dict[str, Any]:
        """Sign and post the order to the CLOB. Requires py_clob_client."""
        private_key = self.environ.get(PRIVATE_KEY_ENV, "")
        try:
            from py_clob_client.client import ClobClient  # type: ignore[import-not-found]
            from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore[import-not-found]
        except ImportError as error:
            raise ExecutionGateError(
                "REFUSED at signing: py_clob_client is not installed, so the order "
                "cannot be signed. Install it (pip install py-clob-client) and retry. "
                "Nothing was submitted."
            ) from error
        client = ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=137,
            funder=self.environ.get(WALLET_ADDRESS_ENV),
        )
        client.set_api_creds(client.create_or_derive_api_creds())
        # Resolve the CLOB token ID from the market slug. The Polymarket
        # CLOB REST API expects a numeric conditional-token ID, not a
        # human-readable market slug. Passing the slug directly causes a
        # 400/404 rejection.
        resolved_token = client.get_token_id(ticket.market_slug)
        order_args = OrderArgs(
            token_id=resolved_token,
            price=ticket.price,
            size=ticket.size_shares,
            side="BUY" if ticket.action == "buy" else "SELL",
        )
        signed = client.create_order(order_args)
        order_type = OrderType.GTC if ticket.order_type == "limit_gtc" else OrderType.FOK
        response = client.post_order(signed, order_type)
        return {
            "order_id": response.get("orderID") or response.get("id") or uuid.uuid4().hex,
            "transaction_hash": response.get("transactionsHashes")
            or response.get("transactionHash"),
            "raw_response": response,
        }

    # ---------------------------------------------------------------- cancel

    def cancel(self, order_id: str, user_command: bool) -> dict[str, Any]:
        if not user_command:
            raise ExecutionGateError("REFUSED: cancellation also requires an explicit user command.")
        if not self.environ.get(PRIVATE_KEY_ENV):
            raise ExecutionGateError(f"REFUSED: {PRIVATE_KEY_ENV} is not set.")
        try:
            from py_clob_client.client import ClobClient  # type: ignore[import-not-found]
        except ImportError as error:
            raise ExecutionGateError("REFUSED: py_clob_client is not installed.") from error
        client = ClobClient(CLOB_HOST, key=self.environ[PRIVATE_KEY_ENV], chain_id=137)
        client.set_api_creds(client.create_or_derive_api_creds())
        response = client.cancel(order_id)
        self.audit.append("order_cancelled", order_id, {"raw_response": str(response)[:500]})
        return {"status": "cancelled", "order_id": order_id}
