"""NBA market-anchored scaffold v10 (``nba-anchor-v10``), research only.

The 2026-11-20 NBA review found the team-level engines carry no information beyond the line,
and that raw model probability minus ask pays for noise. This scaffold prices every game
around a market anchor and lets the model move the price only by a fitted weight:

    pred = anchor_line + lambda * (model - anchor_line)        (margin and total space)
    P(side) = Normal(pred, sigma) evaluated at the CONTRACT line
    edge = P(side) - ask - taker_fee(ask)

``lambda`` per market is fitted on train (through-origin OLS of (actual - line) on
(model - line)), reported with a date-cluster bootstrap CI, and clipped at 0. ``sigma`` is the
RMS residual around the anchored prediction on train. Both live in the artifact
(``config/models/research/nba-anchor-v10.json``); nothing is hardcoded here.

Operator requirement: EVERY game gets a side and a probability (the Flat ledger calls
everything). Only Main eligibility depends on the blended edge: net edge at or above the
artifact's ``main_min_edge`` (set on validation; ``None`` means nothing is Main yet) with a
servable anchor. With lambda = 0 and the contract at the anchor line the anchored probability is
exactly 50%, so the edge is negative; the side then follows the raw model direction
(``ANCHORED_COIN_FLIP_SIDE_FROM_MODEL``). Lambda = 0 does not block Main by itself: when the
anchor (sportsbook consensus) sits off the Polymarket contract line, the anchored price can
still clear the ask, which is plan track T-B (``NO_EDGE_LAMBDA_ZERO`` is recorded either way).

Anchor sources are pluggable (``AnchorSource``). Archived closing lines carry no capture time
and are BENCHMARK-ONLY: ``price_game(..., serving=True)`` refuses them, and refuses any anchor
observed after the decision time (point-in-time invariant).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..domain import parse_utc
from ..research_incumbent_evaluation import POLICY

NBA_ANCHOR_V10_VERSION = "nba-anchor-v10"
MARKETS = ("spread", "total")

REASON_NO_ANCHOR = "NO_ANCHOR_MODEL_ONLY"
REASON_LAMBDA_ZERO = "NO_EDGE_LAMBDA_ZERO"
REASON_COIN_FLIP = "ANCHORED_COIN_FLIP_SIDE_FROM_MODEL"
REASON_NO_ASK = "NO_EXECUTABLE_ASK"
REASON_NO_THRESHOLD = "NO_MAIN_THRESHOLD"
REASON_BELOW_THRESHOLD = "EDGE_BELOW_MAIN_THRESHOLD"


@dataclass(frozen=True)
class AnchorQuote:
    """A market line used as the anchor.

    ``line`` is the home-margin line for spreads (+6.5 = home favoured by 6.5; away covers iff
    the home margin finishes below it) and the points total for totals.
    """

    event_id: str
    market: str
    line: float
    source: str
    observed_at_utc: str | None
    benchmark_only: bool


class AnchorSource(Protocol):
    def anchor(self, event_id: str, market: str, decision_time: datetime) -> AnchorQuote | None: ...


@dataclass
class ClosingLineAnchorSource:
    """Archived closing lines (benchmark only: no capture timestamp, never a decision input)."""

    lines: Mapping[str, tuple[float, float]]  # event_id -> (home_margin_line, total_line)

    def anchor(self, event_id: str, market: str, decision_time: datetime) -> AnchorQuote | None:
        pair = self.lines.get(event_id)
        if pair is None:
            return None
        line = pair[0] if market == "spread" else pair[1]
        return AnchorQuote(event_id, market, line, "closing_line_archive", None, benchmark_only=True)


@dataclass
class CapturedAnchorSource:
    """Decision-time captures (devigged consensus or Polymarket mid) with observed times.

    ``quotes`` holds every capture per (event_id, market); the latest one observed at or before
    the decision time is used, so a capture taken after the decision can never leak in.
    """

    quotes: Mapping[tuple[str, str], Sequence[AnchorQuote]]

    def anchor(self, event_id: str, market: str, decision_time: datetime) -> AnchorQuote | None:
        eligible = [
            q
            for q in self.quotes.get((event_id, market), ())
            if q.observed_at_utc is not None and parse_utc(q.observed_at_utc) <= decision_time
        ]
        return max(eligible, key=lambda q: parse_utc(q.observed_at_utc or ""), default=None)


@dataclass
class FallbackAnchorSource:
    """First source with an anchor wins (plan: devigged consensus, else Polymarket mid)."""

    sources: Sequence[AnchorSource]

    def anchor(self, event_id: str, market: str, decision_time: datetime) -> AnchorQuote | None:
        for source in self.sources:
            quote = source.anchor(event_id, market, decision_time)
            if quote is not None:
                return quote
        return None


@dataclass(frozen=True)
class AnchorMarketParams:
    lambda_: float  # clipped at 0
    sigma: float
    main_min_edge: float | None  # None: no Main eligibility until set on validation


@dataclass(frozen=True)
class AnchorParams:
    markets: Mapping[str, AnchorMarketParams]
    fee_coefficient: float
    model_version: str = NBA_ANCHOR_V10_VERSION

    @classmethod
    def from_artifact(cls, artifact: Mapping[str, Any]) -> AnchorParams:
        markets = {}
        for market in MARKETS:
            block = artifact["markets"][market]
            raw = block.get("main_min_edge")
            markets[market] = AnchorMarketParams(
                lambda_=max(0.0, float(block["lambda"])),
                sigma=float(block["sigma"]),
                main_min_edge=None if raw is None else float(raw),
            )
        return cls(
            markets=markets,
            fee_coefficient=float(artifact.get("fee_coefficient", POLICY["fee_rate"])),
            model_version=str(artifact.get("model_version", NBA_ANCHOR_V10_VERSION)),
        )


@dataclass(frozen=True)
class ContractQuote:
    """Executable contract for one market: its line and the asks per side, in probability."""

    line: float
    asks: Mapping[str, float | None]  # spread: "home"/"away"; total: "over"/"under"


@dataclass(frozen=True)
class AnchorCall:
    event_id: str
    market: str
    side: str
    probability: float  # anchored probability of ``side``
    contract_line: float
    blended_mean: float
    model_mean: float
    anchor_line: float | None
    anchor_source: str | None
    anchor_observed_at_utc: str | None
    lambda_: float
    sigma: float
    ask: float | None
    fee: float | None
    edge: float | None  # probability - ask - fee
    main_eligible: bool
    reason_codes: tuple[str, ...] = field(default_factory=tuple)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def taker_fee_per_contract(price: float, coefficient: float) -> float:
    """Polymarket US taker fee per $1 contract: coefficient * p * (1 - p) (see
    ``research_incumbent_evaluation.taker_fee``; that version rounds per fill)."""
    return coefficient * price * (1.0 - price)


def anchored_mean(model: float, line: float, lambda_: float) -> float:
    return line + max(0.0, lambda_) * (model - line)


def price_market(
    event_id: str,
    market: str,
    model_mean: float,
    anchor: AnchorQuote | None,
    contract: ContractQuote,
    params: AnchorParams,
    *,
    decision_time: datetime,
    serving: bool,
) -> AnchorCall:
    """One market, one game: always returns a side and a probability."""
    if market not in MARKETS:
        raise ValueError(f"unknown market {market!r}")
    mp = params.markets[market]
    reasons: list[str] = []
    if anchor is not None:
        if serving and anchor.benchmark_only:
            raise ValueError(f"{event_id}/{market}: benchmark-only anchor {anchor.source!r} cannot serve")
        if anchor.observed_at_utc is not None and parse_utc(anchor.observed_at_utc) > decision_time:
            raise ValueError(f"{event_id}/{market}: anchor observed after decision time")
        if serving and anchor.observed_at_utc is None:
            raise ValueError(f"{event_id}/{market}: serving anchor has no observed_at_utc")
        mean = anchored_mean(model_mean, anchor.line, mp.lambda_)
    else:
        # Model-only fallback keeps the Flat call; never Main.
        reasons.append(REASON_NO_ANCHOR)
        mean = model_mean

    # P(away covers) = P(home margin < line); P(over) = P(total > line).
    p_low = _phi((contract.line - mean) / mp.sigma)
    first, second = ("away", "home") if market == "spread" else ("under", "over")
    probs = {first: p_low, second: 1.0 - p_low}
    if abs(probs[first] - 0.5) < 1e-12:
        # Anchored price is a coin flip: the side follows the raw model direction.
        side = first if model_mean < contract.line else second
        reasons.append(REASON_COIN_FLIP)
    else:
        side = first if probs[first] > 0.5 else second
    probability = probs[side]

    ask = contract.asks.get(side)
    fee = edge = None
    if ask is None:
        reasons.append(REASON_NO_ASK)
    else:
        fee = taker_fee_per_contract(ask, params.fee_coefficient)
        edge = probability - ask - fee
    if anchor is not None and mp.lambda_ == 0.0:
        reasons.append(REASON_LAMBDA_ZERO)
    if mp.main_min_edge is None:
        reasons.append(REASON_NO_THRESHOLD)
    elif edge is not None and edge < mp.main_min_edge:
        reasons.append(REASON_BELOW_THRESHOLD)
    main_eligible = (
        anchor is not None
        and not anchor.benchmark_only
        and mp.main_min_edge is not None
        and edge is not None
        and edge >= mp.main_min_edge
    )
    return AnchorCall(
        event_id=event_id,
        market=market,
        side=side,
        probability=probability,
        contract_line=contract.line,
        blended_mean=mean,
        model_mean=model_mean,
        anchor_line=anchor.line if anchor else None,
        anchor_source=anchor.source if anchor else None,
        anchor_observed_at_utc=anchor.observed_at_utc if anchor else None,
        lambda_=mp.lambda_,
        sigma=mp.sigma,
        ask=ask,
        fee=fee,
        edge=edge,
        main_eligible=main_eligible,
        reason_codes=tuple(reasons),
    )


def price_game(
    event_id: str,
    model_means: Mapping[str, float],
    contracts: Mapping[str, ContractQuote],
    source: AnchorSource,
    params: AnchorParams,
    *,
    decision_time: datetime,
    serving: bool = True,
) -> list[AnchorCall]:
    """Anchored calls for every market that has both a model mean and a contract.

    ``model_means``: {"spread": home-margin mean, "total": total mean} from the underlying
    engine (v9 / v9.1 ``inference_inputs["mean"]``).
    """
    calls = []
    for market in MARKETS:
        if market not in model_means or market not in contracts:
            continue
        anchor = source.anchor(event_id, market, decision_time)
        calls.append(
            price_market(
                event_id,
                market,
                float(model_means[market]),
                anchor,
                contracts[market],
                params,
                decision_time=decision_time,
                serving=serving,
            )
        )
    return calls
