"""Pre-start re-check of picks blocked by the hard edge gate's upper bound.

An edge above ``max_edge`` is usually a stale line or news the model has not
seen. Discarding it at forecast time throws away the cases where the market has
since moved toward the model. Close to first pitch/tip the lineups and injuries
are public and the quote is fresh, so the same gate can be re-applied once.

The rule is deliberately asymmetric. A fresh ask that still shows an edge above
``max_edge`` is NOT admitted: the market has had the late news and still
disagrees with the model, which is the signature of model error. Only a fresh
edge back inside ``[min_edge, max_edge]`` is admitted, at the fresh ask.

Pure functions only: no I/O, no ledger writes. The caller supplies the fresh
quotes and the clock, and decides what to do with the decisions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from .domain import parse_utc

DEFAULT_WINDOW = timedelta(minutes=60)
DEFAULT_MAX_QUOTE_AGE = timedelta(minutes=10)

ADMIT = "ADMIT"
STILL_ABOVE_MAX = "STILL_ABOVE_MAX"
BELOW_MIN = "BELOW_MIN"
TOO_EARLY = "TOO_EARLY"
STARTED = "STARTED"
NO_QUOTE = "NO_QUOTE"
STALE_QUOTE = "STALE_QUOTE"


@dataclass(frozen=True)
class BlockedPick:
    event_id: str
    model_probability: float
    event_start_utc: str
    first_edge: float


@dataclass(frozen=True)
class RecheckDecision:
    event_id: str
    decision: str
    fresh_edge: float | None
    fresh_ask: float | None
    first_edge: float
    reason: str


def recheck_blocked_picks(
    blocked: Iterable[BlockedPick],
    fresh_quotes: Mapping[str, Mapping[str, object]],
    now: datetime,
    *,
    min_edge: float = 0.02,
    max_edge: float = 0.08,
    window: timedelta = DEFAULT_WINDOW,
    max_quote_age: timedelta = DEFAULT_MAX_QUOTE_AGE,
) -> list[RecheckDecision]:
    """Re-apply the edge gate to over-max picks inside the pre-start window.

    ``fresh_quotes`` maps ``event_id`` to a quote with ``executable_ask`` and
    ``observed_at_utc``. Decisions are returned in input order.
    """
    decisions: list[RecheckDecision] = []
    for pick in blocked:
        start = parse_utc(pick.event_start_utc)
        until_start = start - now

        def decide(
            decision: str,
            reason: str,
            *,
            edge: float | None = None,
            ask: float | None = None,
            _pick: BlockedPick = pick,
        ) -> None:
            decisions.append(RecheckDecision(_pick.event_id, decision, edge, ask, _pick.first_edge, reason))

        if until_start <= timedelta(0):
            decide(STARTED, "event already started; no re-check")
            continue
        if until_start > window:
            decide(TOO_EARLY, f"{until_start} to start exceeds the {window} re-check window")
            continue
        quote = fresh_quotes.get(pick.event_id)
        if quote is None:
            decide(NO_QUOTE, "no fresh executable quote for this event")
            continue
        observed = quote.get("observed_at_utc")
        if not observed or now - parse_utc(str(observed)) > max_quote_age:
            decide(STALE_QUOTE, f"quote older than {max_quote_age} or untimestamped")
            continue
        ask = float(quote["executable_ask"])  # type: ignore[arg-type]
        edge = pick.model_probability - ask
        if edge > max_edge:
            decide(
                STILL_ABOVE_MAX,
                f"edge {edge:.4f} still above {max_edge:.2f} with late news public; likely model error",
                edge=edge,
                ask=ask,
            )
        elif edge < min_edge:
            decide(BELOW_MIN, f"edge {edge:.4f} fell below {min_edge:.2f}", edge=edge, ask=ask)
        else:
            decide(
                ADMIT,
                f"edge {edge:.4f} back inside [{min_edge:.2f}, {max_edge:.2f}] at fresh ask {ask:.4f}",
                edge=edge,
                ask=ask,
            )
    return decisions
