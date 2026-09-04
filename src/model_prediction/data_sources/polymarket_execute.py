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
import math
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


def _normalize_name_tokens(name: str) -> str:
    cleaned = re.sub(r"[(),.\-_]", " ", name)
    return " ".join(cleaned.casefold().split())


def _team_name_matches(team_name: str, side_description: str) -> bool:
    """Same loose match dashboard_server.py's _pick_quote already uses to
    resolve a row's home/away team to a market side's long/short
    description -- duplicated rather than imported because dashboard_server
    deliberately has zero imports from this package (see DEBUG.md)."""
    team = _normalize_name_tokens(team_name)
    description = _normalize_name_tokens(side_description)
    if not team or not description:
        return False
    if team == description:
        return True
    shorter, longer = (description, team) if len(description) <= len(team) else (team, description)
    return f" {shorter} " in f" {longer} "


def _tennis_player_matches(player_name: str, exchange_name: str) -> bool:
    """Match a tennis player when one source includes an omitted middle name or inverted names."""
    player_tokens = re.findall(r"\w+", player_name.casefold())
    exchange_tokens = re.findall(r"\w+", exchange_name.casefold())
    return (
        len(player_tokens) >= 2
        and len(exchange_tokens) >= 2
        and (
            (player_tokens[0] == exchange_tokens[0] and player_tokens[-1] == exchange_tokens[-1])
            or set(player_tokens) == set(exchange_tokens)
        )
    )


def _participant_matches(pick_row: dict[str, Any], participant: str, side_description: str) -> bool:
    if _team_name_matches(participant, side_description):
        return True
    sport = str(pick_row.get("league") or pick_row.get("sport") or "").casefold()
    return sport == "tennis" and _tennis_player_matches(participant, side_description)


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

    # Binary team-win markets (e.g. soccer "Will X win?" where long="Yes", short="No" and team=target_team)
    market_team = snapshot.get("team")
    long_desc = str((snapshot.get("long") or {}).get("description") or "").strip()
    short_desc = str((snapshot.get("short") or {}).get("description") or "").strip()
    if market_team and long_desc.casefold() == "yes" and short_desc.casefold() == "no":
        if _participant_matches(pick_row, selected_team, str(market_team)):
            return "long"
        raise ExecutionGateError(
            f"REFUSED: live market is a team-win contract for {market_team!r}, cannot match opposite pick {selected_team!r}."
        )

    matches_long = _participant_matches(pick_row, selected_team, long_desc)
    matches_short = _participant_matches(pick_row, selected_team, short_desc)
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
    is_market_team = _participant_matches(pick_row, selected_team, str(market_team))
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


def _resolve_nrfi_side(pick_row: dict[str, str], snapshot: dict[str, Any]) -> str:
    selection = str(pick_row.get("selection", "")).casefold().strip()
    expected_description = {"nrfi": "no", "yrfi": "yes"}.get(selection)
    if expected_description is None:
        raise ExecutionGateError(
            f"REFUSED: unrecognized selection {pick_row.get('selection')!r} for an NRFI/YRFI market."
        )
    return _resolve_exact_description_side(expected_description, snapshot)


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
    ioc_fallback_resting: bool = False

    def describe(self) -> str:
        fallback = (
            " If the IOC only partially fills, the unfilled remainder is "
            "placed as a resting GTC order at this same price (never a "
            "higher/chased price)."
            if self.ioc_fallback_resting
            else ""
        )
        return (
            f"Order: {self.action.upper()} {self.size_shares:g} shares "
            f"[{self.token_side}] of {self.market_slug} @ ${self.price:.4f} "
            f"({self.order_type}). Estimated cost: ${self.estimated_cost_usd:.2f}. "
            f"Authorization: {self.authorization_type}. Pick: {self.pick_id}.{fallback}"
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
        if ticket.ioc_fallback_resting and (ticket.order_type != "limit_ioc" or ticket.action != "buy"):
            raise ExecutionGateError("REFUSED: the resting fallback is valid only for IOC buy orders.")
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
                "order_ids": submission.get("order_ids") or [submission.get("order_id")],
                "filled_size_shares": submission.get("filled_size_shares"),
                "estimated_filled_cost_usd": submission.get("estimated_filled_cost_usd"),
                "ioc_fallback_resting": ticket.ioc_fallback_resting,
                "fallback_order_id": submission.get("fallback_order_id"),
                "fallback_status": submission.get("fallback_status"),
                "fallback_resting_shares": submission.get("fallback_resting_shares"),
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
            "nrfi": _resolve_nrfi_side,
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
        # The Ed25519 request signature covers the path WITHOUT the query
        # string (Polymarket US signature spec), while the request URL keeps
        # the full path including any pagination cursor/query parameters.
        request_path = path.split("?", 1)[0]
        for attempt in range(max_retries):
            try:
                response = httpx.request(
                    method,
                    f"{API_HOST}{path}",
                    headers=self._auth_headers(method, request_path),
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
        the exact user-confirmed price of the selected outcome. When an IOC
        buy only partially (or never) fills and ``ioc_fallback_resting`` is
        set, the unfilled remainder is placed as a second order -- but that
        second order is a GTC resting limit AT THE SAME PRICE, never a
        marketable order at a higher price. It waits on the book for a
        seller rather than paying up to force an immediate fill.
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

        def submit_order(order_type: str, quantity: float) -> tuple[dict[str, Any], str]:
            marketable_order = order_type == "limit_ioc"
            response = self._request(
                "POST",
                "/v1/orders",
                {
                    "marketSlug": ticket.market_slug,
                    "intent": intent,
                    "type": "ORDER_TYPE_LIMIT",
                    "price": {"value": f"{exchange_price:.2f}", "currency": "USD"},
                    "quantity": quantity,
                    "tif": (
                        "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
                        if marketable_order
                        else "TIME_IN_FORCE_GOOD_TILL_CANCEL"
                    ),
                    "participateDontInitiate": not marketable_order,
                    "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL",
                    "synchronousExecution": marketable_order,
                },
            )
            order_id = response.get("id") or (response.get("order") or {}).get("id")
            if not order_id:
                raise ExecutionGateError(
                    "REFUSED: Polymarket US did not return an order ID; no submitted state was recorded."
                )
            return response, str(order_id)

        def order_from(response: dict[str, Any]) -> dict[str, Any]:
            nested = response.get("order")
            return nested if isinstance(nested, dict) else response

        def fill_quantity(order: dict[str, Any], requested: float) -> float | None:
            raw = order.get("cumQuantity")
            if raw is None:
                value = None
            else:
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    value = None
            if value is None:
                state = str(order.get("state") or "").upper()
                if state == "ORDER_STATE_FILLED":
                    return requested
                if state in {"ORDER_STATE_CANCELED", "ORDER_STATE_EXPIRED", "ORDER_STATE_REJECTED"}:
                    return 0.0
                return None
            if not math.isfinite(value):
                return None
            return max(0.0, min(requested, float(value)))

        marketable = ticket.order_type == "limit_ioc"
        response, order_id = submit_order(ticket.order_type, ticket.size_shares)
        order = order_from(response)
        primary_filled = fill_quantity(order, ticket.size_shares)
        if marketable and primary_filled is None:
            # The synchronous IOC response can omit the fill quantity while the
            # order is still being processed. Poll the authoritative order
            # endpoint before giving up: a fill we fail to observe here becomes
            # an untracked live position, and a full/partial fill we assume away
            # becomes a phantom ledger row. Neither is acceptable.
            for attempt in range(3):
                try:
                    refreshed = self._request("GET", f"/v1/order/{order_id}", None)
                    order = order_from(refreshed)
                    primary_filled = fill_quantity(order, ticket.size_shares)
                    if primary_filled is not None:
                        break
                except ExecutionGateError:
                    pass
                time.sleep(0.5 * (attempt + 1))

        order_ids = [order_id]
        fallback_order_id: str | None = None
        fallback_status = "not_authorized"
        fallback_resting_shares = 0.0
        # A resting GTC order is legitimately "not yet filled", so unknown-fill
        # tracking only applies to marketable IOC orders whose fill we could not
        # observe at all.
        fill_known = (not marketable) or (primary_filled is not None)

        can_fallback = (
            marketable
            and ticket.action == "buy"
            and ticket.ioc_fallback_resting
            and primary_filled is not None
            and primary_filled + 1e-9 < ticket.size_shares
        )
        if can_fallback and primary_filled is not None:
            fallback_resting_shares = round(ticket.size_shares - primary_filled, 4)
            if fallback_resting_shares >= 0.01:
                _fallback_response, fallback_order_id = submit_order("limit_gtc", fallback_resting_shares)
                order_ids.append(fallback_order_id)
                fallback_status = "resting"
            else:
                fallback_status = "no_remainder"

        if not fill_known:
            # We could not determine whether the primary IOC filled. Do NOT
            # fabricate a zero fill or a full fill. Track the primary order as
            # a pending reconciliation target (reusing the fallback fields so
            # reconcile_pending_auto_buyer_fallbacks() restates shares/cost from
            # the exchange once the order reaches a terminal state) and place no
            # second order, which would risk a double buy.
            fallback_order_id = order_id
            fallback_resting_shares = round(ticket.size_shares, 4)
            fallback_status = "unknown_fill"
            order_ids = [order_id]

        known_primary_filled = (
            primary_filled if primary_filled is not None else (0.0 if marketable else ticket.size_shares)
        )
        total_filled = round(known_primary_filled, 4)
        estimated_filled_cost = round(known_primary_filled * ticket.price, 4)

        if not marketable:
            final_state = str(order.get("state") or "ORDER_STATE_NEW")
        elif not fill_known:
            final_state = "ORDER_STATE_UNKNOWN"
        elif total_filled + 1e-9 >= ticket.size_shares:
            final_state = "ORDER_STATE_FILLED"
        elif total_filled > 0 or fallback_order_id is not None:
            final_state = "ORDER_STATE_PARTIALLY_FILLED"
        else:
            final_state = str(order.get("state") or "ORDER_STATE_EXPIRED")

        return {
            "order_id": order_id,
            "order_ids": order_ids,
            "order_state": final_state,
            "exchange_price": round(exchange_price, 2),
            "filled_size_shares": total_filled,
            "estimated_filled_cost_usd": estimated_filled_cost,
            "fallback_order_id": fallback_order_id,
            "fallback_status": fallback_status,
            "fallback_resting_shares": fallback_resting_shares,
            "fill_known": fill_known,
            "raw_response": response,
        }

    # --------------------------------------------------------- portfolio read

    def _paginate(self, path: str, item_key: str, *, max_pages: int = 50) -> tuple[Any, bool]:
        """Walk a cursor-paginated Polymarket US endpoint until EOF.

        Returns ``(items, eof)``. When the endpoint's item container is a dict
        (e.g. ``positions`` keyed by slug) the result is a merged dict; when it
        is a list (e.g. ``activities``) the result is a concatenated list.
        ``max_pages`` bounds runaway pagination.
        """
        list_items: list[dict[str, Any]] = []
        dict_items: dict[str, Any] = {}
        saw_dict = False
        cursor: str | None = None
        eof = False
        for _ in range(max_pages):
            request_path = path if cursor is None else f"{path}?cursor={cursor}"
            response = self._request("GET", request_path)
            page = response.get(item_key)
            if isinstance(page, dict):
                saw_dict = True
                dict_items.update(page)
            elif isinstance(page, list):
                list_items.extend(page)
            eof = bool(response.get("eof", True))
            if eof:
                break
            next_cursor = response.get("nextCursor") or response.get("next_cursor")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = str(next_cursor)
            # Be polite between pages: the endpoint is Cloudflare-protected and
            # bursts of back-to-back cursor requests trigger 429 blocks.
            time.sleep(0.25)
        return (dict_items if saw_dict else list_items), eof

    def portfolio_snapshot(self) -> dict[str, Any]:
        """Read the authenticated live account without inferring fills.

        Submitted orders are deliberately not treated as positions. The
        exchange positions and activity endpoints are the source of truth for
        filled exposure, trades, and market resolutions. Both endpoints are
        cursor-paginated and are walked to EOF so older resolutions (which
        drive settlement) are not silently dropped past page one.
        """
        positions, positions_eof = self._paginate("/v1/portfolio/positions", "positions")
        activities, activities_eof = self._paginate("/v1/portfolio/activities", "activities")
        balances = self._request("GET", "/v1/account/balances")
        return {
            "status": "live",
            "source": "polymarket_us_authenticated_portfolio",
            "positions": positions,
            "activities": activities,
            "balances": balances.get("balances") or [],
            "positions_eof": positions_eof,
            "activities_eof": activities_eof,
            "observed_at_utc": iso_utc(utc_now()),
        }

    def order_snapshots(self, order_ids: list[str]) -> dict[str, Any]:
        """Read authoritative exchange state for previously submitted orders.

        Each order_id is looked up independently: one stale/purged/rate-
        limited order_id must not prevent every other order_id in the same
        batch from being reconciled. A failed lookup is reported in
        ``unavailable_order_ids`` rather than raising, so a caller
        reconciling many pending rows in one batch (e.g. the daily
        unattended settle cycle) can still make progress on the rest.
        """
        missing = [name for name in (KEY_ID_ENV, SECRET_KEY_ENV) if not self.environ.get(name)]
        if missing:
            raise ExecutionGateError(f"REFUSED: {', '.join(missing)} is not set.")
        orders = []
        unavailable: list[str] = []
        for order_id in dict.fromkeys(str(value) for value in order_ids if value):
            try:
                response = self._request("GET", f"/v1/order/{order_id}")
            except ExecutionGateError:
                unavailable.append(order_id)
                continue
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
            "unavailable_order_ids": unavailable,
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
