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
liquidity up to that price and cancel any unfilled remainder. An IOC remainder
fallback rests only as a post-only GOOD_TILL_DATE order that expires before the
event starts. The midpoint is never silently substituted.

Every ``POST /v1/orders`` carries an ``Idempotency-Key`` (one per order leg),
the intent is audited before the POST, and an ambiguous outcome (sent, but no
usable answer) raises ``OrderSubmitUnknownError`` instead of being resent.

Submission uses the authenticated Polymarket US retail REST API. It does not
use the international Polymarket CLOB or a wallet private key.
"""

from __future__ import annotations

import base64
import logging
import math
import os
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric import ed25519

from ..audit import AuditLog
from ..domain import iso_utc, parse_utc, utc_now
from ..runtime_paths import RuntimePaths, load_dotenv_into_environ

_paths = RuntimePaths.resolve()
load_dotenv_into_environ(_paths.repo_root / ".env")

logger = logging.getLogger(__name__)

_MARKET_SLUG_RE = re.compile(r"market_slug=([a-z0-9_\-]+)")
_MARKET_SLUG_LEGACY_RE = re.compile(r"\(([a-z0-9_\-]+)\)\.?\s*$")
_LIVE_QUOTE_MAXIMUM_AGE_SECONDS = 300

# Operator decision 2026-11-25 (quick-261125-hih B1): an ambiguous order POST (sent, then a read
# timeout, a 5xx, a 409/ABORTED, or a 2xx with no order id) is NOT resent. The Polymarket US docs
# prescribe a same-key retry, but no test order may prove the Idempotency-Key header is honoured
# (D-07) and the docs record that the older `idempotencyKey` spelling was silently ignored. Flip only
# on a recorded operator decision; the same-key retry path below is tested either way.
ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY = False
_ORDER_POST_PATH = "/v1/orders"
# These fail before any byte of the request reaches the exchange, so a same-key resend is safe.
_CONNECT_PHASE_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_GOOD_TILL_DATE = "TIME_IN_FORCE_GOOD_TILL_DATE"
_IMMEDIATE_OR_CANCEL = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
# D-01: a fallback must outlive its own placement; an expiry this close is not worth resting.
_FALLBACK_MIN_LIFETIME_SECONDS = 60
# D-01: the exchange's own gameStartTime clamps the ticket's expiry to start minus this lead.
_FALLBACK_EXCHANGE_START_LEAD_SECONDS = 90
_GTD_READBACK_TOLERANCE_SECONDS = 2.0
# An adopted fallback must have been created at or after its intent, give or take clock skew.
_ADOPTION_CLOCK_SKEW_SECONDS = 5.0
_AUDIT_ERRORS = (OSError, TimeoutError, ValueError, TypeError, RuntimeError)


def _idempotency_key(intent_id: str, leg: str) -> str:
    """One key per placement intent and leg (``p`` primary, ``f`` fallback), under the 255-byte cap."""
    return f"mp-{intent_id}-{leg}"[:255]


def _format_good_till_time(value: datetime) -> str:
    """RFC 3339 UTC to the second, floored so the expiry never moves later than requested."""
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _earliest_event_start(pick_row: dict[str, Any], live_snapshot: dict[str, Any] | None) -> datetime | None:
    """The earlier of the row's and the exchange's event start; None when neither parses."""
    starts: list[datetime] = []
    for raw in (pick_row.get("event_start_utc"), (live_snapshot or {}).get("event_start_utc")):
        with suppress(TypeError, ValueError):
            starts.append(parse_utc(str(raw or "")))
    return min(starts) if starts else None


def _describe_prior_buy(event: dict[str, Any]) -> str:
    """Name the audit event that blocks a second buy: an order, an unknown outcome, or a dangling intent."""
    payload = event.get("payload") or {}
    event_type = str(event.get("event_type") or "")
    if event_type == "order_executed":
        return f"order {payload.get('order_id')} at {payload.get('submitted_at_utc')}"
    intent_id = payload.get("intent_id") or "unrecorded"
    if event_type == "order_submit_unknown":
        return f"order_submit_unknown for intent {intent_id}; the order may exist, check the exchange"
    created = payload.get("created_at_utc") or event.get("occurred_at_utc") or "an unrecorded time"
    return f"order_intent {intent_id} created at {created} with no recorded outcome"


def _response_order_id(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    nested = response.get("order")
    order_id = response.get("id") or (nested.get("id") if isinstance(nested, dict) else None)
    return str(order_id) if order_id else None


# Mirror of dashboard/backtests.py _TEAM_NAME_ALIASES: ESPN-style names that
# share no word with Polymarket US's name for the same team. Keep in sync.
_TEAM_NAME_ALIASES = {
    "app state mountaineers": "appalachian state",
    "app state": "appalachian state",
    "central connecticut blue devils": "central connecticut state",
    "central connecticut": "central connecticut state",
    "grambling tigers": "grambling state",
    "long island university sharks": "liu",
    "long island university": "liu",
    "massachusetts minutemen": "umass",
    "massachusetts": "umass",
    "miami hurricanes": "miami fl",
    "se louisiana lions": "southeastern louisiana",
    "san jose state spartans": "san jose state",
    "san jos� state spartans": "san jose state",
    "ul monroe warhawks": "louisiana monroe",
    "ul monroe": "louisiana monroe",
    "nm state": "new mexico state",
    "nm state aggies": "new mexico state",
    "k-state": "kansas state",
    "k state": "kansas state",
    "k state wildcats": "kansas state",
    "ole miss": "mississippi",
    "ole miss rebels": "mississippi",
    "sam houston": "sam houston state",
    "sam houston bearkats": "sam houston state",
    "jax state": "jacksonville state",
    "jax state gamecocks": "jacksonville state",
    "fiu": "florida international",
    "fiu panthers": "florida international",
    "fau": "florida atlantic",
    "fau owls": "florida atlantic",
    "usf": "south florida",
    "usf bulls": "south florida",
    "ucf": "central florida",
    "ucf knights": "central florida",
    "pitt": "pittsburgh",
    "pitt panthers": "pittsburgh",
    "la tech": "louisiana tech",
    "la tech bulldogs": "louisiana tech",
    "nc state": "north carolina state",
    "nc state wolfpack": "north carolina state",
    "uconn": "connecticut",
    "uconn huskies": "connecticut",
}

_DISQUALIFYING_TEAM_PREFIXES = frozenset(
    {"western", "eastern", "northern", "southern", "central", "middle", "north", "south", "east", "west"}
)
_DISQUALIFYING_TEAM_SUFFIXES = frozenset(
    {"state", "tech", "a&m", "am", "christian", "baptist", "methodist", "valley"}
)


def _normalize_name_tokens(name: str) -> str:
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch))
    cleaned = re.sub(r"[(),.\-_]", " ", folded)
    normalized = " ".join(cleaned.casefold().split())
    return _TEAM_NAME_ALIASES.get(normalized, normalized)


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
    l_tokens = longer.split()
    s_tokens = shorter.split()
    s_len = len(s_tokens)
    for i in range(len(l_tokens) - s_len + 1):
        if l_tokens[i : i + s_len] == s_tokens:
            if i > 0 and l_tokens[i - 1] in _DISQUALIFYING_TEAM_PREFIXES:
                continue
            if i + s_len < len(l_tokens) and l_tokens[i + s_len] in _DISQUALIFYING_TEAM_SUFFIXES:
                continue
            return True
    # Franchise mascot / distinctive token match (e.g. "DEN Nuggets" vs "Denver Nuggets", "GS Warriors" vs "Golden State Warriors")
    t_tokens = team.split()
    d_tokens = set(description.split())
    if t_tokens and d_tokens:
        mascot = t_tokens[-1]
        if (
            len(mascot) > 3
            and mascot not in {"club", "city", "team", "state", "united"}
            and mascot in d_tokens
        ):
            return True
    return False


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


class OrderSubmitUnknownError(RuntimeError):
    """An order POST was sent but its outcome is unknown: it may or may not exist on the exchange.

    Deliberately not an ExecutionGateError ("nothing was submitted"). Never resubmit under a new
    key; check the exchange (open orders, fills) for ``market_slug`` first.
    """

    def __init__(
        self,
        message: str,
        *,
        idempotency_key: str,
        market_slug: str,
        leg: str,
        detail: str = "",
        intent_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.idempotency_key = idempotency_key
        self.market_slug = market_slug
        self.leg = leg
        self.detail = detail or message
        self.intent_id = intent_id


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
    # D-01: the IOC remainder rests GOOD_TILL_DATE until this instant; None places no fallback.
    resting_expires_at_utc: str | None = None
    # B5: refuse when the live ask is more than this far from ``price`` (either way); None skips it.
    max_live_price_deviation: float | None = None

    def describe(self) -> str:
        fallback = (
            " If the IOC only partially fills, the unfilled remainder is "
            "placed as a post-only GOOD_TILL_DATE order at this same price "
            f"(never a higher/chased price), expiring by {self.resting_expires_at_utc}."
            if self.ioc_fallback_resting and self.resting_expires_at_utc
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
        # (Idempotency-Key, leg, event start) for the order POST in flight. An attribute rather than a
        # _request argument so the 3-positional-argument _request fakes in the tests keep working.
        self._order_post_context: tuple[str, str, datetime | None] | None = None

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
            prior_buy = self._prior_buy_event(ticket.pick_id)
            if prior_buy is not None:
                raise ExecutionGateError(
                    f"REFUSED: a buy order was already submitted for pick {ticket.pick_id} "
                    f"({_describe_prior_buy(prior_buy)}); single_order policy "
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
        live_snapshot = self._verify_live_side_and_timing(ticket, pick_row)
        # Gate 4: interactive confirmation with exact order details.
        answer = self.confirm(f"{ticket.describe()} Confirm? (Y/N): ").strip().casefold()
        if answer not in {"y", "yes"}:
            self.audit.append(
                "order_declined",
                ticket.pick_id,
                {"market_slug": ticket.market_slug, "reason": "user_declined_confirmation"},
            )
            return {"status": "declined", "note": "User declined at confirmation. No order placed."}
        # B2: the intent is audited BEFORE the POST, so an order the exchange accepted can never be
        # missing from the audit chain; if the intent cannot be written, nothing is sent.
        intent_id = uuid.uuid4().hex
        primary_key = _idempotency_key(intent_id, "p")
        fallback_key = _idempotency_key(intent_id, "f")
        event_fields = {
            "event_id": pick_row.get("event_id"),
            "selection": str(pick_row.get("selection") or "").casefold() or None,
            "market_type": pick_row.get("market_type"),
            "line": pick_row.get("line"),
        }
        try:
            self.audit.append(
                "order_intent",
                ticket.pick_id,
                {
                    "intent_id": intent_id,
                    "idempotency_key": primary_key,
                    "fallback_idempotency_key": fallback_key,
                    "market_slug": ticket.market_slug,
                    "token_side": ticket.token_side,
                    "action": ticket.action,
                    "order_type": ticket.order_type,
                    "price": ticket.price,
                    "size_shares": ticket.size_shares,
                    **event_fields,
                    "event_start_utc": pick_row.get("event_start_utc"),
                    "resting_expires_at_utc": ticket.resting_expires_at_utc,
                    "created_at_utc": iso_utc(utc_now()),
                },
            )
        except _AUDIT_ERRORS as error:
            raise ExecutionGateError(
                f"REFUSED: could not audit the order intent ({type(error).__name__}: {error}); nothing was sent."
            ) from error
        # The intent append can wait on the audit lock; the pregame check above ran before that wait.
        with suppress(TypeError, ValueError):
            if utc_now() >= parse_utc(str(pick_row.get("event_start_utc", ""))):
                with suppress(*_AUDIT_ERRORS):
                    self.audit.append(
                        "order_rejected", ticket.pick_id, {"intent_id": intent_id, "error": "event started"}
                    )
                raise ExecutionGateError(
                    "REFUSED: the event started while the order intent was being audited; nothing was sent."
                )
        try:
            submission = self._submit(
                ticket,
                intent_id=intent_id,
                idempotency_key=primary_key,
                fallback_idempotency_key=fallback_key,
                live_snapshot=live_snapshot,
                event_start=_earliest_event_start(pick_row, live_snapshot),
            )
        except ExecutionGateError as error:
            with suppress(*_AUDIT_ERRORS):
                self.audit.append(
                    "order_rejected", ticket.pick_id, {"intent_id": intent_id, "error": str(error)[:500]}
                )
            raise
        except OrderSubmitUnknownError as error:
            error.intent_id = intent_id
            with suppress(*_AUDIT_ERRORS):
                self.audit.append(
                    "order_submit_unknown",
                    ticket.pick_id,
                    {
                        "intent_id": intent_id,
                        "idempotency_key": error.idempotency_key,
                        "fallback_idempotency_key": fallback_key,
                        "leg": error.leg,
                        "detail": str(error.detail)[:500],
                        "market_slug": ticket.market_slug,
                        "token_side": ticket.token_side,
                        "action": ticket.action,
                        "price": ticket.price,
                        "size_shares": ticket.size_shares,
                        **event_fields,
                    },
                )
            raise
        # Gate 7: audit chain. The order exists now, so an audit failure is returned, never raised:
        # raising here would read as "refused" upstream and invite a duplicate buy.
        executed_payload = {
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
            "fallback_error": submission.get("fallback_error"),
            "good_till_time": submission.get("good_till_time"),
            "intent_id": intent_id,
            "idempotency_key": submission.get("idempotency_key"),
            "fallback_idempotency_key": submission.get("fallback_idempotency_key"),
            **event_fields,
            "submitted_at_utc": iso_utc(utc_now()),
        }
        try:
            self.audit.append("order_executed", ticket.pick_id, executed_payload)
        except _AUDIT_ERRORS as error:
            logger.error(
                "order %s for pick %s on %s was placed but its order_executed audit failed: %s",
                submission.get("order_ids"),
                ticket.pick_id,
                ticket.market_slug,
                error,
            )
            return {"status": "submitted", **submission, "audit_error": f"{type(error).__name__}: {error}"}
        return {"status": "submitted", **submission}

    def _prior_buy_event(self, pick_id: str) -> dict[str, Any] | None:
        """The newest audit event that means a buy may already exist for ``pick_id``.

        An executed buy, an unknown submission, or an intent whose outcome was never recorded (a
        crash mid-POST) all count. Only an intent later marked ``order_rejected`` does not.
        ``reversed_events`` yields newest first, so a rejection is seen before its own intent.
        """
        rejected_intents: set[str] = set()
        for event in self.audit.reversed_events():
            if event.get("subject_id") != pick_id:
                continue
            event_type = event.get("event_type")
            payload = event.get("payload") or {}
            if event_type == "order_rejected":
                rejected_intents.add(str(payload.get("intent_id") or ""))
                continue
            if event_type == "order_executed" and payload.get("action") == "buy":
                return event
            if (
                event_type in {"order_submit_unknown", "order_intent"}
                and payload.get("action", "buy") == "buy"
            ):
                if event_type == "order_intent" and str(payload.get("intent_id") or "") in rejected_intents:
                    continue
                return event
        return None

    # ------------------------------------------------------ live verification

    def _verify_live_side_and_timing(self, ticket: OrderTicket, pick_row: dict[str, str]) -> dict[str, Any]:
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
        if ticket.max_live_price_deviation is not None:
            # B5: the ticket was priced from a quote read earlier in the cycle; refuse when the live
            # ask has since moved more than the allowed deviation in either direction.
            raw_ask: Any = (snapshot.get(ticket.token_side) or {}).get("ask")
            try:
                live_ask = float(raw_ask)
            except (TypeError, ValueError):
                live_ask = math.nan
            if not math.isfinite(live_ask):
                raise ExecutionGateError(
                    f"REFUSED: the live ask for {ticket.token_side} is missing ({raw_ask!r})."
                )
            if abs(live_ask - ticket.price) > ticket.max_live_price_deviation + 1e-9:
                raise ExecutionGateError(
                    f"REFUSED: the live ask {live_ask:.2f} is more than {ticket.max_live_price_deviation:.2f} "
                    f"from the ticket price {ticket.price:.2f}; nothing was sent."
                )
        return snapshot

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
        if method.upper() == "POST" and path == _ORDER_POST_PATH:
            return self._post_order(payload)
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

    def _post_order(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """POST /v1/orders with this leg's Idempotency-Key (B1).

        Resent with the same key only when nothing reached the exchange (a connect-phase error, or a
        429 from the edge, which rejects before the API). Anything else that leaves the outcome open
        raises ``OrderSubmitUnknownError`` at once, unless ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY.
        A definite 4xx is REFUSED. Once any attempt was ambiguous, no later attempt can make the
        outcome REFUSED: that reads as "nothing was sent" and frees the pick for a second buy. No
        resend goes out once the event has started.
        """
        context = self._order_post_context
        if context is None or not context[0]:
            raise ExecutionGateError(
                "REFUSED: an order POST must carry an Idempotency-Key; nothing was sent."
            )
        key, leg, event_start = context
        market_slug = str((payload or {}).get("marketSlug") or "")
        max_attempts = 3
        backoff = 0.5
        last_unknown: OrderSubmitUnknownError | None = None
        for attempt in range(max_attempts):
            final_attempt = attempt == max_attempts - 1
            retry_ambiguous = ORDER_POST_RETRY_AMBIGUOUS_WITH_SAME_KEY and not final_attempt
            if attempt > 0 and event_start is not None and utc_now() >= event_start:
                # The back-off outlasted the pregame window: the resend must not go out.
                raise last_unknown or ExecutionGateError(
                    f"REFUSED: the event started before the {leg} order could be resent; it was never sent."
                )
            try:
                response = httpx.request(
                    "POST",
                    f"{API_HOST}{_ORDER_POST_PATH}",
                    headers={**self._auth_headers("POST", _ORDER_POST_PATH), "Idempotency-Key": key},
                    json=payload,
                    timeout=15,
                )
            except _CONNECT_PHASE_ERRORS as error:
                if not final_attempt:
                    time.sleep(backoff * (2**attempt))
                    continue
                if last_unknown is not None:
                    raise last_unknown from error
                raise ExecutionGateError(
                    f"REFUSED: could not connect to Polymarket US ({type(error).__name__}); the order was never sent."
                ) from error
            except httpx.HTTPError as error:
                last_unknown = OrderSubmitUnknownError(
                    f"order outcome unknown ({leg} leg, {market_slug}): {type(error).__name__} after sending",
                    idempotency_key=key,
                    market_slug=market_slug,
                    leg=leg,
                    detail=f"{type(error).__name__}: {error}",
                )
                if retry_ambiguous:
                    time.sleep(backoff * (2**attempt))
                    continue
                raise last_unknown from error
            status = response.status_code
            body_text = response.text[:500]
            if status == 429:
                if not final_attempt:
                    time.sleep(backoff * (2**attempt))
                    continue
                raise last_unknown or ExecutionGateError(
                    f"REFUSED by Polymarket US (429): rate limited on every attempt: {body_text}"
                )
            if 400 <= status < 500 and status != 409 and "ABORTED" not in body_text:
                raise last_unknown or ExecutionGateError(f"REFUSED by Polymarket US ({status}): {body_text}")
            output: Any = None
            if 200 <= status < 300:
                try:
                    output = response.json()
                except ValueError:
                    output = None
                if _response_order_id(output):
                    return output
            last_unknown = OrderSubmitUnknownError(
                f"order outcome unknown ({leg} leg, {market_slug}): HTTP {status} {body_text[:200]}",
                idempotency_key=key,
                market_slug=market_slug,
                leg=leg,
                detail=f"HTTP {status}: {body_text}",
            )
            if retry_ambiguous:
                time.sleep(backoff * (2**attempt))
                continue
            raise last_unknown
        raise last_unknown or ExecutionGateError(
            "REFUSED: Polymarket US order request exceeded retry attempts."
        )

    def _fallback_expiry(self, ticket: OrderTicket, live_snapshot: dict[str, Any] | None) -> datetime | None:
        """Ticket expiry, clamped to the exchange's own start minus 90 s when the live quote has one."""
        if not ticket.resting_expires_at_utc:
            return None
        try:
            expiry = parse_utc(str(ticket.resting_expires_at_utc))
        except ValueError:
            return None
        exchange_start = (live_snapshot or {}).get("event_start_utc")
        if exchange_start:
            with suppress(TypeError, ValueError):
                clamp = parse_utc(str(exchange_start)) - timedelta(
                    seconds=_FALLBACK_EXCHANGE_START_LEAD_SECONDS
                )
                expiry = min(expiry, clamp)
        return expiry

    def _gtd_readback_problem(self, order_id: str, good_till_time: str) -> str | None:
        """None when the exchange reports ``order_id`` as GOOD_TILL_DATE expiring as requested."""
        try:
            response = self._request("GET", f"/v1/order/{order_id}", None)
            nested = response.get("order")
            order = nested if isinstance(nested, dict) else response
            tif = str(order.get("tif") or order.get("timeInForce") or "")
            if tif != _GOOD_TILL_DATE:
                return f"read-back tif {tif or 'missing'}"
            reported = parse_utc(str(order.get("goodTillTime") or ""))
            drift = abs((reported - parse_utc(good_till_time)).total_seconds())
            if drift > _GTD_READBACK_TOLERANCE_SECONDS:
                return f"read-back goodTillTime {order.get('goodTillTime')} is {drift:.0f}s from {good_till_time}"
        except Exception as error:  # noqa: BLE001 -- an unreadable order is unverified, whatever the cause
            return f"read-back failed ({type(error).__name__}: {error})"
        return None

    def _adopt_open_fallback(
        self,
        ticket: OrderTicket,
        *,
        intent: str,
        exchange_price: float,
        quantity: float,
        placed_after: datetime,
    ) -> str | None:
        """The id of the single open GTD order that is this fallback, or None (zero or several matches).

        There is no client order id, so a lost fallback response is matched on intent, price,
        quantity, tif and creation time. An IOC never rests, so this applies to the fallback only.
        """
        try:
            orders = self.open_orders([ticket.market_slug])
        except Exception as error:  # noqa: BLE001 -- adoption is best effort; unknown is the safe default
            logger.warning("open-orders read for fallback adoption failed: %s", error)
            return None
        matches = []
        for order in orders:
            try:
                price: Any = order.get("price")
                raw_price: Any = price.get("value") if isinstance(price, dict) else price
                raw_quantity: Any = order.get("quantity")
                price_value = float(raw_price)
                created = parse_utc(str(order.get("createTime") or ""))
                same = (
                    str(order.get("marketSlug") or ticket.market_slug) == ticket.market_slug
                    and order.get("intent") == intent
                    and abs(price_value - round(exchange_price, 2)) < 1e-9
                    and abs(float(raw_quantity) - quantity) < 1e-6
                    and str(order.get("tif") or order.get("timeInForce") or "") == _GOOD_TILL_DATE
                    and created >= placed_after - timedelta(seconds=_ADOPTION_CLOCK_SKEW_SECONDS)
                )
            except (TypeError, ValueError, AttributeError):
                continue
            if same and order.get("id"):
                matches.append(str(order["id"]))
        return matches[0] if len(matches) == 1 else None

    def _submit(
        self,
        ticket: OrderTicket,
        *,
        intent_id: str | None = None,
        idempotency_key: str | None = None,
        fallback_idempotency_key: str | None = None,
        live_snapshot: dict[str, Any] | None = None,
        event_start: datetime | None = None,
    ) -> dict[str, Any]:
        """Submit a capped resting or immediately marketable limit order.

        Polymarket US always expects ``price.value`` in the market's long/YES
        coordinate, even for a short/NO intent. ``OrderTicket.price`` remains
        the exact user-confirmed price of the selected outcome. When an IOC
        buy only partially (or never) fills and ``ioc_fallback_resting`` is
        set, the unfilled remainder is placed as a second order -- a post-only
        GOOD_TILL_DATE limit AT THE SAME PRICE that expires before the event
        starts (D-01), never a marketable order at a higher price and never a
        GOOD_TILL_CANCEL. The fallback is read back by id and cancelled on any
        mismatch. Once the primary order exists nothing here raises: a failed
        fallback leg is reported in ``fallback_status`` / ``fallback_error``.
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
        intent_id = intent_id or uuid.uuid4().hex
        primary_key = idempotency_key or _idempotency_key(intent_id, "p")
        fallback_key = fallback_idempotency_key or _idempotency_key(intent_id, "f")

        def order_body(tif: str, quantity: float, good_till_time: str | None = None) -> dict[str, Any]:
            marketable_order = tif == _IMMEDIATE_OR_CANCEL
            body: dict[str, Any] = {
                "marketSlug": ticket.market_slug,
                "intent": intent,
                "type": "ORDER_TYPE_LIMIT",
                "price": {"value": f"{exchange_price:.2f}", "currency": "USD"},
                "quantity": quantity,
                "tif": tif,
                "participateDontInitiate": not marketable_order,
                "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL",
                "synchronousExecution": marketable_order,
            }
            if good_till_time is not None:
                body["goodTillTime"] = good_till_time
            return body

        def submit_order(body: dict[str, Any], key: str, leg: str) -> tuple[dict[str, Any], str]:
            self._order_post_context = (key, leg, event_start)
            try:
                response = self._request("POST", _ORDER_POST_PATH, body)
            finally:
                self._order_post_context = None
            order_id = _response_order_id(response)
            if not order_id:
                raise OrderSubmitUnknownError(
                    "Polymarket US did not return an order ID; the order may exist.",
                    idempotency_key=key,
                    market_slug=ticket.market_slug,
                    leg=leg,
                    detail="response without an order id",
                    intent_id=intent_id,
                )
            return response, order_id

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
        # A manual `limit_gtc` order keeps its GOOD_TILL_CANCEL; no auto-buyer order is ever one.
        primary_tif = _IMMEDIATE_OR_CANCEL if marketable else "TIME_IN_FORCE_GOOD_TILL_CANCEL"
        response, order_id = submit_order(order_body(primary_tif, ticket.size_shares), primary_key, "primary")
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
        fallback_error: str | None = None
        good_till_time: str | None = None
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
            remainder = round(ticket.size_shares - primary_filled, 4)
            expiry = self._fallback_expiry(ticket, live_snapshot)
            if remainder < 0.01:
                fallback_status = "no_remainder"
            elif expiry is None:
                fallback_status = "not_placed_no_expiry"
            elif expiry <= utc_now() + timedelta(seconds=_FALLBACK_MIN_LIFETIME_SECONDS):
                fallback_status = "not_placed_near_start"
            else:
                good_till_time = _format_good_till_time(expiry)
                placed_after = utc_now()
                try:
                    _fallback_response, fallback_order_id = submit_order(
                        order_body(_GOOD_TILL_DATE, remainder, good_till_time), fallback_key, "fallback"
                    )
                    fallback_status = "resting"
                except ExecutionGateError as error:
                    # The exchange refused the GTD leg (e.g. a payload format it rejects): the IOC
                    # result stands and nothing rests. Never retried as GOOD_TILL_CANCEL (D-01).
                    fallback_status = "rejected"
                    fallback_error = str(error)[:500]
                except Exception as error:  # noqa: BLE001 -- the IOC already exists; never lose it
                    fallback_error = f"{type(error).__name__}: {error}"[:500]
                    fallback_order_id = self._adopt_open_fallback(
                        ticket,
                        intent=intent,
                        exchange_price=exchange_price,
                        quantity=remainder,
                        placed_after=placed_after,
                    )
                    fallback_status = "resting" if fallback_order_id else "submit_unknown"
                if fallback_status in {"resting", "submit_unknown"}:
                    fallback_resting_shares = remainder
                if fallback_order_id:
                    order_ids.append(fallback_order_id)
                    problem = self._gtd_readback_problem(fallback_order_id, good_till_time)
                    if problem is not None:
                        fallback_error = problem
                        try:
                            self.cancel(fallback_order_id, user_command=True, market_slug=ticket.market_slug)
                            fallback_status = "cancelled_unverified_gtd"
                        except Exception as error:  # noqa: BLE001 -- reported; reconcile retries later
                            fallback_status = "unverified_gtd_cancel_failed"
                            fallback_error = f"{problem}; cancel failed ({type(error).__name__}: {error})"[
                                :500
                            ]

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
        elif total_filled > 0 or fallback_order_id is not None or fallback_status == "submit_unknown":
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
            "fallback_error": fallback_error,
            "good_till_time": good_till_time,
            "fill_known": fill_known,
            "intent_id": intent_id,
            "idempotency_key": primary_key,
            "fallback_idempotency_key": fallback_key,
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
                    "tif": order.get("tif") or order.get("timeInForce"),
                    "good_till_time": order.get("goodTillTime"),
                }
            )
        return {
            "status": "live",
            "orders": orders,
            "unavailable_order_ids": unavailable,
            "observed_at_utc": iso_utc(utc_now()),
        }

    def open_orders(self, slugs: list[str]) -> list[dict[str, Any]]:
        """Open orders for ``slugs`` (GET /v1/orders/open, repeated ``slugs=``; not paginated). Read only."""
        missing = [name for name in (KEY_ID_ENV, SECRET_KEY_ENV) if not self.environ.get(name)]
        if missing:
            raise ExecutionGateError(f"REFUSED: {', '.join(missing)} is not set.")
        query = "&".join(f"slugs={slug}" for slug in dict.fromkeys(str(s) for s in slugs if s))
        response = self._request("GET", f"/v1/orders/open?{query}", None)
        orders = response.get("orders")
        return [order for order in orders if isinstance(order, dict)] if isinstance(orders, list) else []

    def preview_order(self, request_body: dict[str, Any], user_command: bool) -> dict[str, Any]:
        """POST /v1/order/preview once, for the operator's optional GTD format probe (D-07).

        The buyer never calls this. The docs call it a preview "before submission" but never state
        that it places nothing, so it requires an explicit user command and is never retried: an
        ambiguous outcome raises ``OrderSubmitUnknownError``.
        """
        if not user_command:
            raise ExecutionGateError("REFUSED: an order preview requires an explicit user command.")
        missing = [name for name in (KEY_ID_ENV, SECRET_KEY_ENV) if not self.environ.get(name)]
        if missing:
            raise ExecutionGateError(f"REFUSED: {', '.join(missing)} is not set.")
        path = "/v1/order/preview"
        market_slug = str(request_body.get("marketSlug") or "")
        try:
            response = httpx.request(
                "POST",
                f"{API_HOST}{path}",
                headers=self._auth_headers("POST", path),
                json={"request": request_body},
                timeout=15,
            )
        except _CONNECT_PHASE_ERRORS as error:
            raise ExecutionGateError(
                f"REFUSED: could not connect ({type(error).__name__}); nothing was sent."
            ) from error
        except httpx.HTTPError as error:
            raise OrderSubmitUnknownError(
                f"preview outcome unknown ({type(error).__name__})",
                idempotency_key="",
                market_slug=market_slug,
                leg="preview",
            ) from error
        if 400 <= response.status_code < 500:
            raise ExecutionGateError(
                f"REFUSED by Polymarket US ({response.status_code}): {response.text[:500]}"
            )
        try:
            output = response.json()
        except ValueError:
            output = None
        if response.status_code >= 300 or not isinstance(output, dict):
            raise OrderSubmitUnknownError(
                f"preview outcome unknown (HTTP {response.status_code})",
                idempotency_key="",
                market_slug=market_slug,
                leg="preview",
                detail=response.text[:500],
            )
        return output

    # ---------------------------------------------------------------- cancel

    def cancel(
        self,
        order_id: str,
        user_command: bool,
        market_slug: str | None = None,
    ) -> dict[str, Any]:
        if not user_command:
            raise ExecutionGateError("REFUSED: cancellation also requires an explicit user command.")
        missing = [name for name in (KEY_ID_ENV, SECRET_KEY_ENV) if not self.environ.get(name)]
        if missing:
            raise ExecutionGateError(f"REFUSED: {', '.join(missing)} is not set.")
        payload: dict[str, Any] = {}
        if market_slug:
            payload["marketSlug"] = market_slug
        else:
            with suppress(httpx.HTTPError, KeyError, TypeError, ValueError, OSError, ExecutionGateError):
                order_info = self._request("GET", f"/v1/order/{order_id}").get("order", {})
                if order_info.get("marketSlug"):
                    payload["marketSlug"] = order_info["marketSlug"]
        response = self._request("POST", f"/v1/order/{order_id}/cancel", payload)
        try:
            self.audit.append("order_cancelled", order_id, {"raw_response": str(response)[:500]})
        except _AUDIT_ERRORS as error:
            # The cancel already went out; raising would report it as failed and invite a retry.
            logger.error("order %s was cancelled but its order_cancelled audit failed: %s", order_id, error)
            return {
                "status": "cancelled",
                "order_id": order_id,
                "audit_error": f"{type(error).__name__}: {error}",
            }
        return {"status": "cancelled", "order_id": order_id}
