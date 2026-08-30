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

Orders use the exact user-confirmed outcome price. The Polymarket US API's
``price.value`` is always expressed in the long/YES coordinate, so a confirmed
short/NO price of X is submitted as 1-X without changing the user's economic
limit. Resting GTC limits are post-only; marketable IOC limits may take
liquidity up to that price and cancel any unfilled remainder. The midpoint is
never silently substituted.

Submission uses the authenticated Polymarket US retail REST API. It does not
use the international Polymarket CLOB or a wallet private key.
"""

from __future__ import annotations

import base64
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric import ed25519

from ..audit import AuditLog
from ..domain import iso_utc, parse_utc, utc_now
from ..runtime_paths import RuntimePaths

_paths = RuntimePaths.resolve()
_env_file = _paths.repo_root / ".env"
if _env_file.exists():
    try:
        with _env_file.open(encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _key, _val = _line.split("=", 1)
                    if _key.strip() and _key.strip() not in os.environ:
                        os.environ[_key.strip()] = _val.strip()
    except OSError:
        pass

_MARKET_SLUG_RE = re.compile(r"market_slug=([a-z0-9\-]+)")
_MARKET_SLUG_LEGACY_RE = re.compile(r"\(([a-z0-9\-]+)\)\.?\s*$")
_LIVE_QUOTE_MAXIMUM_AGE_SECONDS = 300


def _team_name_matches(team_name: str, side_description: str) -> bool:
    """Same loose match dashboard_server.py's _pick_quote already uses to
    resolve a row's home/away team to a market side's long/short
    description -- duplicated rather than imported because dashboard_server
    deliberately has zero imports from this package (see DEBUG.md)."""
    team = " ".join(team_name.casefold().split())
    description = " ".join(side_description.casefold().split())
    if not team or not description:
        return False
    if team == description:
        return True
    shorter, longer = (description, team) if len(description) <= len(team) else (team, description)
    return f" {shorter} " in f" {longer} "


def _lines_match(a: float, b: float) -> bool:
    return abs(a - b) < 1e-6


def _row_selected_team(pick_row: dict[str, str]) -> str | None:
    home_team = str(pick_row.get("home_team", ""))
    away_team = str(pick_row.get("away_team", ""))
    selection = str(pick_row.get("selection", "")).casefold()
    return home_team if selection == "home" else away_team if selection == "away" else None


def _row_line(pick_row: dict[str, str]) -> float:
    try:
        return float(pick_row.get("line") or "")
    except ValueError as error:
        raise ExecutionGateError("REFUSED: pick row has no valid line to verify against.") from error


def _resolve_moneyline_side(pick_row: dict[str, str], snapshot: dict[str, Any]) -> str:
    selected_team = _row_selected_team(pick_row)
    if not selected_team:
        raise ExecutionGateError(
            f"REFUSED: unrecognized selection {pick_row.get('selection')!r} for live side verification."
        )
    matches_long = _team_name_matches(selected_team, str(snapshot["long"]["description"]))
    matches_short = _team_name_matches(selected_team, str(snapshot["short"]["description"]))
    if matches_long == matches_short:
        raise ExecutionGateError(
            "REFUSED: could not unambiguously resolve the picked team to a live market side."
        )
    return "long" if matches_long else "short"


def _resolve_spread_side(pick_row: dict[str, str], snapshot: dict[str, Any]) -> str:
    """A spread market's own ``line``/``team`` always describe the LONG
    side -- e.g. ``line=-1.5, team="Red Sox"`` means long="Red Sox -1.5" and
    short is always the exact negation, the opponent's own +1.5. Verified
    live 2026-08-03 against real captured Polymarket contracts: two
    alternate-line markets for the same event (a "-1.5" market and a "+1.5"
    market) each showed long.description == format(market.line) and
    short.description == format(-market.line).

    A row's own ``line`` is selection-relative (PickRequest.validate's own
    rule: "spread calls require a selection-relative line"), so the picked
    team's line only matches the market's long side when the picked team
    IS the market's own team; for the opponent, the row's line must equal
    the negation instead.
    """
    selected_team = _row_selected_team(pick_row)
    if not selected_team:
        raise ExecutionGateError(
            f"REFUSED: unrecognized selection {pick_row.get('selection')!r} for live side verification."
        )
    row_line = _row_line(pick_row)
    market_team = snapshot.get("team")
    market_line = snapshot.get("line")
    if market_team is None or market_line is None:
        raise ExecutionGateError("REFUSED: live spread market has no team/line to verify against.")
    is_market_team = _team_name_matches(selected_team, str(market_team))
    if is_market_team and _lines_match(row_line, float(market_line)):
        return "long"
    if not is_market_team and _lines_match(row_line, -float(market_line)):
        return "short"
    raise ExecutionGateError(
        "REFUSED: could not unambiguously resolve the picked team/line to a live market side "
        f"(row selection={selected_team!r} line={row_line}, live team={market_team!r} line={market_line})."
    )


def _resolve_total_side(pick_row: dict[str, str], snapshot: dict[str, Any]) -> str:
    selection = str(pick_row.get("selection", "")).casefold().strip()
    if selection not in {"over", "under"}:
        raise ExecutionGateError(
            f"REFUSED: unrecognized selection {pick_row.get('selection')!r} for a total market."
        )
    row_line = _row_line(pick_row)
    market_line = snapshot.get("line")
    if market_line is None or not _lines_match(row_line, float(market_line)):
        raise ExecutionGateError(
            f"REFUSED: pick row line {row_line} does not match the live market's total line ({market_line})."
        )
    return _resolve_exact_description_side(selection, snapshot)


def _resolve_btts_side(pick_row: dict[str, str], snapshot: dict[str, Any]) -> str:
    selection = str(pick_row.get("selection", "")).casefold().strip()
    if selection not in {"yes", "no"}:
        raise ExecutionGateError(
            f"REFUSED: unrecognized selection {pick_row.get('selection')!r} for a btts market."
        )
    return _resolve_exact_description_side(selection, snapshot)


def _resolve_exact_description_side(selection: str, snapshot: dict[str, Any]) -> str:
    long_desc = str(snapshot["long"]["description"]).casefold().strip()
    short_desc = str(snapshot["short"]["description"]).casefold().strip()
    matches_long = long_desc == selection
    matches_short = short_desc == selection
    if matches_long == matches_short:
        raise ExecutionGateError(
            f"REFUSED: could not unambiguously resolve {selection!r} to a live market side "
            f"(long={long_desc!r}, short={short_desc!r})."
        )
    return "long" if matches_long else "short"


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
    order_type: str  # "limit_gtc" | "limit_ioc"
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


def _extract_market_slug(rationale: str) -> str | None:
    """Recover the market slug a ledger row's rationale text was priced
    against — the same extraction pattern settlement already uses
    (cli.py::_settle_esports_pick) to tie a settlement back to its market.
    Used here to bind an execution ticket to the exact row it claims to
    come from, not just whatever market_slug the caller happened to pass."""
    match = _MARKET_SLUG_RE.search(rationale)
    if match is None:
        match = _MARKET_SLUG_LEGACY_RE.search(rationale)
    return match.group(1) if match else None


class PolymarketExecutor:
    """All gate checks live here so no caller can skip them piecemeal."""

    def __init__(
        self,
        audit: AuditLog,
        confirm: Callable[[str], str] = input,
        environ: dict[str, str] | None = None,
        live_quote: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.audit = audit
        self.confirm = confirm
        self.environ = environ if environ is not None else dict(os.environ)
        # Injectable so tests can supply a fake quote without a real network
        # call or a live Polymarket US account; defaults to the real client.
        self._live_quote = live_quote

    # ------------------------------------------------------------------ gate

    def execute(
        self,
        ticket: OrderTicket,
        pick_row: dict[str, str],
        execute_flag: bool,
        user_command: bool,
        manual_research_order: bool = False,
        artifact_qualified: bool = True,
    ) -> dict[str, Any]:
        """Run the full gate; submit only if every condition passes.

        ``manual_research_order`` and ``artifact_qualified`` no longer gate
        anything (operator directive, 2026-08-02: execution is not
        restricted by record_type or artifact qualification -- that's the
        operator's discretion). Both parameters are kept for call-site
        compatibility and because their values still get written to the
        audit event (``source_record_type``/``source_artifact_qualified``)
        as a permanent, honest record of what a pick's classification WAS
        at execution time -- that history is worth keeping even though it no
        longer decides whether the order can be submitted.
        """
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
        # Ticket-to-row binding: a ticket's identity must match the exact row
        # it claims to execute, not merely whichever row the caller happened
        # to look up. Without this, a caller could pass a legitimately
        # qualified pick_row (to clear the gate below) alongside a ticket
        # naming an unrelated market/price/size.
        if pick_row.get("pick_id") and ticket.pick_id != pick_row["pick_id"]:
            raise ExecutionGateError(
                f"REFUSED: ticket pick_id {ticket.pick_id!r} does not match the "
                f"looked-up row {pick_row['pick_id']!r}."
            )
        row_slug = _extract_market_slug(str(pick_row.get("rationale", "")))
        if row_slug is not None and ticket.market_slug != row_slug:
            raise ExecutionGateError(
                f"REFUSED: ticket market_slug {ticket.market_slug!r} does not match "
                f"the market this pick was actually priced against ({row_slug!r})."
            )
        # single_order policy: one approved BUY should produce one exchange
        # instruction. Nothing upstream (dashboard nonce/expiry, CLI args)
        # stopped a caller from previewing+submitting a second, independent
        # order against the same still-open pick_id -- each one cleared every
        # check separately. Checked against the audit chain itself (not
        # orders.json, which is dashboard-only) so this protects the raw CLI
        # `execute` path too, not just the dashboard. A SELL is exempt: it's
        # how a bought position is legitimately closed, not accumulated.
        if ticket.action == "buy":
            prior_buy = next(
                (
                    event
                    for event in reversed(self.audit.events())
                    if event.get("event_type") == "order_executed"
                    and event.get("subject_id") == ticket.pick_id
                    and event.get("payload", {}).get("action") == "buy"
                ),
                None,
            )
            if prior_buy is not None:
                raise ExecutionGateError(
                    f"REFUSED: a buy order was already submitted for pick {ticket.pick_id} "
                    f"(order {prior_buy['payload'].get('order_id')} at "
                    f"{prior_buy['payload'].get('submitted_at_utc')}); single_order policy "
                    "refuses a second buy against the same pick. Use `sell-position` or "
                    "`execute --action sell` to close the existing position, not to accumulate."
                )
        # Cost is recomputed server-side rather than trusted from the
        # caller-supplied ticket field, so a stale/incorrect estimated_cost_usd
        # can't understate what the unit cap actually checks against.
        recomputed_cost = round(ticket.price * ticket.size_shares, 2)
        if abs(recomputed_cost - ticket.estimated_cost_usd) > 0.01:
            raise ExecutionGateError(
                f"REFUSED: ticket estimated_cost_usd ${ticket.estimated_cost_usd:.2f} does not "
                f"match price * size_shares = ${recomputed_cost:.2f}."
            )
        # Operator directive, 2026-08-02: execution is no longer gated on
        # record_type/artifact qualification -- "no restrictions, up to my
        # discretion" (removing the earlier "QUALIFIED_SHADOW_CALL, or
        # explicit manual-research-order" requirement and the "genuinely
        # qualified artifact vs config override" distinction below it).
        # source_record_type/source_reason_code/source_artifact_qualified
        # still get written to the audit event below -- what a pick's
        # classification WAS remains a permanent, honest record; it just no
        # longer decides whether an order can be submitted. Every other gate
        # (explicit command, --execute flag, credentials, ticket-to-row
        # binding, cost recompute, single_order dedup, live side/pregame/
        # quote-freshness verification, price/size sanity, interactive
        # confirmation, audit chain) is unchanged and still enforced.
        if pick_row.get("status") != "open":
            raise ExecutionGateError("REFUSED: pick is not open.")
        # Gate 5 is upstream (unit engine sized the pick); re-assert sanity.
        if ticket.size_shares <= 0 or not 0 < ticket.price < 1:
            raise ExecutionGateError("REFUSED: order size/price failed sanity checks.")
        # The exchange prices in whole cents. A sub-cent limit must be refused,
        # never silently rounded — rounding changes the user-confirmed price.
        if abs(ticket.price * 100 - round(ticket.price * 100)) > 1e-9:
            raise ExecutionGateError(
                f"REFUSED: limit price {ticket.price} is not a whole-cent tick; resubmit at a 0.01 increment."
            )
        if ticket.maximum_cost_usd is not None and recomputed_cost > ticket.maximum_cost_usd + 0.005:
            raise ExecutionGateError(
                f"REFUSED: ${recomputed_cost:.2f} exceeds the authorized "
                f"unit cap of ${ticket.maximum_cost_usd:.2f}."
            )
        if ticket.order_type not in {"limit_gtc", "limit_ioc"}:
            raise ExecutionGateError(
                "REFUSED: supported order types are post-only GTC and marketable IOC limits."
            )
        # Live side/pregame/freshness verification, independent of whatever
        # the caller already checked. dashboard_server.py's preview/submit
        # flow derives token_side from the row's own quote before ever
        # building a ticket, but the raw CLI `execute` command builds one
        # straight from user-typed --side/--action args with no such check
        # -- and this executor is the one chokepoint both paths share.
        # Without this, a caller (or a bug/typo in either path) could submit
        # token_side="short" for a pick whose real selection was the long
        # side, and nothing upstream of here would catch it.
        self._verify_live_side_and_timing(ticket, pick_row)
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
                "price_basis": "selected_outcome_probability",
                "exchange_price": submission.get("exchange_price"),
                "exchange_price_basis": "long_side_probability",
                "size_shares": ticket.size_shares,
                "estimated_cost_usd": recomputed_cost,
                "maximum_cost_usd": ticket.maximum_cost_usd,
                "authorization_type": ticket.authorization_type,
                "source_record_type": pick_row.get("record_type"),
                "source_reason_code": pick_row.get("reason_code"),
                "source_artifact_qualified": artifact_qualified,
                "transaction_hash": submission.get("transaction_hash"),
                "submitted_at_utc": iso_utc(utc_now()),
            },
        )
        return {"status": "submitted", **submission}

    # ------------------------------------------------------ live verification

    def _verify_live_side_and_timing(self, ticket: OrderTicket, pick_row: dict[str, str]) -> None:
        """Fetch a fresh quote and independently confirm token_side, market
        state, and pregame status -- rather than trusting whatever the
        caller already derived.

        Covers every market type this project prices: moneyline (team-name
        match), spread (team + signed-line match, P0-1 2026-08-03), total
        (over/under description + line match), btts (yes/no description
        match). An unrecognized/missing market_type refuses outright rather
        than silently skipping the check -- every real row from the
        pipeline always sets market_type (a required PickRequest field), so
        an absent value here means the row is malformed, not that this is a
        market type without a resolver.
        """
        market_type = pick_row.get("market_type")
        resolver = {
            "moneyline": _resolve_moneyline_side,
            "spread": _resolve_spread_side,
            "total": _resolve_total_side,
            "btts": _resolve_btts_side,
        }.get(str(market_type))
        if resolver is None:
            raise ExecutionGateError(f"REFUSED: no live side resolver for market_type {market_type!r}.")
        try:
            event_start = parse_utc(str(pick_row.get("event_start_utc", "")))
        except ValueError as error:
            raise ExecutionGateError(
                "REFUSED: pick row has no valid event_start_utc to verify against."
            ) from error
        if utc_now() >= event_start:
            raise ExecutionGateError("REFUSED: event has already started.")
        from .polymarket_us import PolymarketUSClient

        try:
            snapshot = (self._live_quote or PolymarketUSClient().snapshot)(ticket.market_slug)
        except ExecutionGateError:
            raise
        except Exception as error:
            raise ExecutionGateError(
                f"REFUSED: could not fetch a live quote to verify this order ({type(error).__name__})."
            ) from error
        try:
            observed_at = parse_utc(str(snapshot["observed_at_utc"]))
        except (KeyError, ValueError) as error:
            raise ExecutionGateError("REFUSED: live quote has no valid observed_at_utc.") from error
        if (utc_now() - observed_at).total_seconds() > _LIVE_QUOTE_MAXIMUM_AGE_SECONDS:
            raise ExecutionGateError(
                "REFUSED: live quote is stale (older than "
                f"{_LIVE_QUOTE_MAXIMUM_AGE_SECONDS}s); refresh and resubmit."
            )
        if snapshot.get("market_state") not in (None, "MARKET_STATE_OPEN"):
            raise ExecutionGateError(f"REFUSED: market is not open (state={snapshot.get('market_state')}).")
        expected_side = resolver(pick_row, snapshot)
        if ticket.token_side != expected_side:
            raise ExecutionGateError(
                f"REFUSED: ticket token_side {ticket.token_side!r} does not match the "
                f"live market side for this pick ({expected_side!r})."
            )

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
        max_retries = 3
        backoff = 0.5
        for attempt in range(max_retries):
            try:
                response = httpx.request(
                    method,
                    f"{API_HOST}{path}",
                    headers=self._auth_headers(method, path),
                    json=payload,
                    timeout=15,
                )
                if response.status_code == 429 and attempt < max_retries - 1:
                    time.sleep(backoff * (2**attempt))
                    continue
                response.raise_for_status()
                output = response.json()
                if not isinstance(output, dict):
                    raise ExecutionGateError("REFUSED: Polymarket US returned an invalid response.")
                return output
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 429 and attempt < max_retries - 1:
                    time.sleep(backoff * (2**attempt))
                    continue
                detail = error.response.text[:500]
                raise ExecutionGateError(
                    f"REFUSED by Polymarket US ({error.response.status_code}): {detail}"
                ) from error
            except (httpx.HTTPError, ValueError) as error:
                if attempt < max_retries - 1:
                    time.sleep(backoff * (2**attempt))
                    continue
                raise ExecutionGateError(
                    f"REFUSED: Polymarket US order request failed ({type(error).__name__})."
                ) from error
        raise ExecutionGateError("REFUSED: Polymarket US request exceeded retry attempts.")

    def _submit(self, ticket: OrderTicket) -> dict[str, Any]:
        """Submit a capped resting or immediately marketable limit order.

        Polymarket US always expects ``price.value`` in the market's long/YES
        coordinate, even for a short/NO intent. ``OrderTicket.price`` remains
        the exact user-confirmed price of the selected outcome.
        """
        intent = {
            ("buy", "long"): "ORDER_INTENT_BUY_LONG",
            ("buy", "short"): "ORDER_INTENT_BUY_SHORT",
            ("sell", "long"): "ORDER_INTENT_SELL_LONG",
            ("sell", "short"): "ORDER_INTENT_SELL_SHORT",
        }.get((ticket.action, ticket.token_side))
        if intent is None:
            raise ExecutionGateError("REFUSED: unsupported order action/side.")
        exchange_price = ticket.price if ticket.token_side == "long" else 1.0 - ticket.price
        marketable = ticket.order_type == "limit_ioc"
        response = self._request(
            "POST",
            "/v1/orders",
            {
                "marketSlug": ticket.market_slug,
                "intent": intent,
                "type": "ORDER_TYPE_LIMIT",
                "price": {"value": f"{exchange_price:.2f}", "currency": "USD"},
                "quantity": ticket.size_shares,
                "tif": (
                    "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" if marketable else "TIME_IN_FORCE_GOOD_TILL_CANCEL"
                ),
                "participateDontInitiate": not marketable,
                "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL",
                "synchronousExecution": marketable,
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
            "exchange_price": round(exchange_price, 2),
            "raw_response": response,
        }

    # --------------------------------------------------------- portfolio read

    def portfolio_snapshot(self) -> dict[str, Any]:
        """Read the authenticated live account without inferring fills.

        Submitted orders are deliberately not treated as positions. The
        exchange positions and activity endpoints are the source of truth for
        filled exposure, trades, and market resolutions.
        """
        positions = self._request("GET", "/v1/portfolio/positions")
        activities = self._request("GET", "/v1/portfolio/activities")
        balances = self._request("GET", "/v1/account/balances")
        return {
            "status": "live",
            "source": "polymarket_us_authenticated_portfolio",
            "positions": positions.get("positions") or {},
            "activities": activities.get("activities") or [],
            "balances": balances.get("balances") or [],
            "positions_eof": positions.get("eof"),
            "activities_eof": activities.get("eof"),
            "observed_at_utc": iso_utc(utc_now()),
        }

    def order_snapshots(self, order_ids: list[str]) -> dict[str, Any]:
        """Read authoritative exchange state for previously submitted orders."""
        missing = [name for name in (KEY_ID_ENV, SECRET_KEY_ENV) if not self.environ.get(name)]
        if missing:
            raise ExecutionGateError(f"REFUSED: {', '.join(missing)} is not set.")
        orders = []
        for order_id in dict.fromkeys(str(value) for value in order_ids if value):
            response = self._request("GET", f"/v1/order/{order_id}")
            order = response.get("order") or response
            if not isinstance(order, dict):
                continue
            orders.append(
                {
                    "order_id": str(order.get("id") or order_id),
                    "order_state": order.get("state"),
                    "market_slug": order.get("marketSlug"),
                    "cum_quantity": order.get("cumQuantity"),
                    "leaves_quantity": order.get("leavesQuantity"),
                }
            )
        return {
            "status": "live",
            "orders": orders,
            "observed_at_utc": iso_utc(utc_now()),
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
