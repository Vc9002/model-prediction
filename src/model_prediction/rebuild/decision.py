"""Winner-first value-betting decision engine (CLAUDE.md's non-negotiable
betting strategy, Checkpoint 7).

The system is not an unrestricted "bet whichever side has the largest
apparent edge" engine. It answers three questions independently:

1. What is the sports-only probability? (SportsForecast — market-blind)
2. Is a specific, frozen-before-market side mispriced? (MarketEvaluation)
3. Is the disagreement large enough after costs? (edge_scaled_units)

Moneyline/spread: the candidate side must equal `predicted_winner`, decided
before any market price is inspected. An opponent priced attractively is
never a substitute recommendation — it is simply never evaluated at all.

Totals: independent of game-winner alignment. The more probable side (OVER
or UNDER) is frozen from the sports-only distribution for that exact line
before market inspection, then only that side may be bet.

`NO_BET` always carries exactly 0.0 units — there is no minimum-stake floor.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

from ..market_blend import MarketBlendBlockedError, MarketBlendPolicy
from .economic import SizeLimits, edge_scaled_units

SIZE_LIMITS_VERSION = "size_limits_v1"


@dataclass(frozen=True)
class SportsForecast:
    """Market-blind sports-only prediction for one event. Constructed once,
    from the model alone — nothing in the market layer may rewrite any field
    on this object."""

    event_id: str
    predicted_winner: Literal["home", "away"]
    raw_probabilities: dict[str, float]  # {"home": .., "away": ..}
    calibrated_probabilities: dict[str, float]
    probability_lower: dict[str, float]  # conservative/lower-bound
    probability_upper: dict[str, float]
    expected_home_score: float
    expected_away_score: float
    model_artifact_hash: str
    calibration_artifact_hash: str
    # {line: {"over": prob, "under": prob}} — the sports-only distribution's
    # own view per exact line, independent of any specific market's price.
    totals_probabilities: dict[float, dict[str, float]] = field(default_factory=dict)
    # {line: {"home": cover_prob, "away": cover_prob}} — real, verified bug
    # (see outputs/rebuild/takeover_status.md Checkpoint 9): without this,
    # decide_team_market() had no way to price a spread except by reusing
    # the moneyline win probability, which is not the probability of
    # covering a specific line (e.g. a 60%-to-win favorite covering -2.5 is
    # a materially different, usually lower, probability) — that produced
    # real, confirmed 20%+ fabricated "edges" on real spread markets.
    spread_probabilities: dict[float, dict[str, float]] = field(default_factory=dict)
    # Real, data-driven conservative (lower-bound) probability per exact
    # (line, side) -- optional, defaults empty. Before this field existed,
    # spread and total markets had *no* conservative haircut at all: their
    # cost_adjusted_edge was computed directly from the raw point-estimate
    # probability in `spread_probabilities`/`totals_probabilities`, unlike
    # moneyline, which always used `probability_lower`. When populated
    # (typically from BootstrapMLBEnsemble), decide_team_market()/
    # decide_total() prefer this value; when absent for a given (line, side)
    # they fall back to the point estimate, so callers that don't build a
    # bootstrap ensemble (e.g. most existing unit tests) keep working
    # unchanged.
    totals_probabilities_lower: dict[float, dict[str, float]] = field(default_factory=dict)
    spread_probabilities_lower: dict[float, dict[str, float]] = field(default_factory=dict)
    # Explicit outcome mass. The pricing probabilities above may split push
    # mass by contract convention; these fields never relabel a push as a win.
    totals_outcomes: dict[float, dict[str, float]] = field(default_factory=dict)
    spread_outcomes: dict[float, dict[str, float]] = field(default_factory=dict)
    # Multi-sport execution spec MLB-5: the full uncertainty decomposition
    # (uncertainty.py), wired into the live moneyline forecast. All default
    # to real "not computed" values (0.0 / empty / None), not fabricated
    # non-zero numbers, so callers/tests that don't populate them keep
    # working unchanged.
    model_disagreement: float = 0.0
    calibration_uncertainty: float = 0.0
    missingness_penalty: float = 0.0
    missing_flags: list[str] = field(default_factory=list)
    # None means "unavailable" (no real timestamp-valid lineup source) --
    # never fabricated as 0.0, which would silently claim zero real risk.
    lineup_uncertainty: float | None = None
    # Real composed conservative probability per side (bootstrap bound +
    # model_disagreement + calibration_uncertainty + missingness_penalty +
    # lineup_uncertainty, uncertainty.compose_conservative_probability()).
    # When populated, this is the real, preferred source for the decision
    # gate over the plainer `probability_lower` (see evaluate_game()/
    # decide_team_market() below); empty for callers that don't compute it,
    # which then fall back to `probability_lower` unchanged.
    conservative_probabilities: dict[str, float] = field(default_factory=dict)

    def frozen_totals_side(self, line: float) -> Literal["over", "under"] | None:
        """The more probable side for this exact line, decided from the
        sports-only distribution alone — call this before ever looking at a
        market price for that line."""
        probs = self.totals_probabilities.get(line)
        if probs is None:
            return None
        return "over" if probs.get("over", 0.0) >= probs.get("under", 0.0) else "under"


@dataclass(frozen=True)
class MarketEvaluation:
    """Real, executable market evidence for one specific contract."""

    market_id: str
    market_type: Literal["moneyline", "spread", "total"]
    team_or_side: str  # "home"/"away" for moneyline/spread, "over"/"under" for total
    line: float | None
    executable_ask: float
    depth_adjusted_price: float
    quote_age_seconds: float
    available_depth: float
    # CLAUDE.md Part 3 §2: "If the current Polymarket interface does not
    # provide depth: mark depth_available=false; do not fabricate depth."
    # Defaults True so existing callers/tests that construct a candidate
    # with a real, known depth value (or don't care about the depth gate)
    # are unaffected; the real MLB market-matching path sets this False
    # explicitly since the underlying source has no depth endpoint.
    depth_available: bool = True
    market_open: bool = True
    observed_at_utc: str | None = None
    event_start_utc: str | None = None
    event_match_ambiguous: bool = False
    contract_valid: bool = True
    # First-class market probability for an optional serving blend. It is
    # deliberately separate from executable/depth-adjusted price: a caller
    # must provide the probability it intends to blend rather than having the
    # policy infer or de-vig a price silently.
    market_probability: float | None = None


@dataclass(frozen=True)
class BetDecision:
    event_id: str
    action: Literal["BET", "NO_BET"]
    predicted_winner: str
    market_type: str
    selected_market: MarketEvaluation | None
    units: float
    reason_code: str
    cost_adjusted_edge: float | None = None
    # The exact candidate this decision was made about, whether or not it
    # was selected. Real audit-trail gap fixed here: NO_BET always set
    # selected_market=None, so a rejected market's exact market_id/side/
    # line/ask was silently discarded — a report couldn't show *what* was
    # rejected, only that something was. `selected_market` keeps its
    # CLAUDE.md-specified "what was bet, if anything" meaning (None on
    # NO_BET); `evaluated_market` is the audit trail's own record of what
    # was actually looked at, populated on every decision, not just BETs.
    evaluated_market: MarketEvaluation | None = None
    # Decision-boundary audit: the model output is never overwritten. When a
    # blend policy is active these fields persist p_model, p_market, p_blend,
    # the learned weight, and the exact policy/config identities together.
    model_probability: float | None = None
    market_probability: float | None = None
    serving_probability: float | None = None
    model_conservative_probability: float | None = None
    serving_conservative_probability: float | None = None
    blend_weight: float | None = None
    blend_policy_artifact_hash: str | None = None
    blend_experiment_spec_hash: str | None = None
    blend_config_hash: str | None = None
    serving_policy_block_reason: str | None = None
    fee_rate: float | None = None
    safety_margin: float | None = None
    size_limits_version: str | None = None
    size_limits_json: str | None = None
    decision_economics_hash: str | None = None


# Reason codes are deliberately explicit and stable — a dashboard/audit
# consumer should never need to parse a free-text message to know why a
# game produced NO_BET.
NOT_ALIGNED_WITH_PREDICTED_WINNER = "not_aligned_with_predicted_winner"
NOT_ALIGNED_WITH_FROZEN_TOTALS_SIDE = "not_aligned_with_frozen_totals_side"
NO_FORECAST_FOR_LINE = "no_forecast_for_line"
NO_EDGE_AFTER_COSTS = "no_edge_after_costs"
STALE_QUOTE = "stale_quote"
INSUFFICIENT_DEPTH = "insufficient_depth"
ZERO_SIZED = "zero_sized"
QUALIFIED = "qualified"
CLOSED_MARKET = "closed_market"
POST_START_QUOTE = "post_start_quote"
AMBIGUOUS_EVENT = "ambiguous_event"
INVALID_CONTRACT = "invalid_contract"
WRONG_MARKET_LINE = "wrong_market_line"
MARKET_BLEND_POLICY_BLOCKED = "market_blend_policy_blocked"


def _is_post_start(candidate: MarketEvaluation) -> bool:
    if candidate.observed_at_utc is None or candidate.event_start_utc is None:
        return False
    try:
        return datetime.fromisoformat(candidate.observed_at_utc) >= datetime.fromisoformat(
            candidate.event_start_utc
        )
    except (TypeError, ValueError):
        return True


def _quote_gate_reason(candidate: MarketEvaluation, limits: SizeLimits) -> str | None:
    """Freshness/depth are checked before alignment is even evaluated further
    into edge math — a stale or illiquid quote must fail closed regardless of
    how attractive its price looks."""
    if candidate.event_match_ambiguous:
        return AMBIGUOUS_EVENT
    if not candidate.contract_valid:
        return INVALID_CONTRACT
    if not candidate.market_open:
        return CLOSED_MARKET
    if _is_post_start(candidate):
        return POST_START_QUOTE
    if candidate.quote_age_seconds > limits.max_quote_age_seconds:
        return STALE_QUOTE
    if not candidate.depth_available or candidate.available_depth < limits.min_depth_units:
        return INSUFFICIENT_DEPTH
    return None


def _no_bet(
    forecast: SportsForecast,
    market_type: str,
    reason: str,
    edge: float | None = None,
    evaluated: MarketEvaluation | None = None,
    *,
    model_probability: float | None = None,
    market_probability: float | None = None,
    serving_probability: float | None = None,
    model_conservative_probability: float | None = None,
    serving_conservative_probability: float | None = None,
    blend_weight: float | None = None,
    blend_policy_artifact_hash: str | None = None,
    blend_experiment_spec_hash: str | None = None,
    blend_config_hash: str | None = None,
    serving_policy_block_reason: str | None = None,
    fee_rate: float | None = None,
    safety_margin: float | None = None,
    size_limits_version: str | None = None,
    size_limits_json: str | None = None,
    decision_economics_hash: str | None = None,
) -> BetDecision:
    return BetDecision(
        event_id=forecast.event_id,
        action="NO_BET",
        predicted_winner=forecast.predicted_winner,
        market_type=market_type,
        selected_market=None,
        units=0.0,
        reason_code=reason,
        cost_adjusted_edge=edge,
        evaluated_market=evaluated,
        model_probability=model_probability,
        market_probability=market_probability,
        serving_probability=serving_probability,
        blend_weight=blend_weight,
        model_conservative_probability=model_conservative_probability,
        serving_conservative_probability=serving_conservative_probability,
        blend_policy_artifact_hash=blend_policy_artifact_hash,
        blend_experiment_spec_hash=blend_experiment_spec_hash,
        blend_config_hash=blend_config_hash,
        serving_policy_block_reason=serving_policy_block_reason,
        fee_rate=fee_rate,
        safety_margin=safety_margin,
        size_limits_version=size_limits_version,
        size_limits_json=size_limits_json,
        decision_economics_hash=decision_economics_hash,
    )


def _economics_audit(limits: SizeLimits, fee_rate: float, safety_margin: float) -> dict[str, object]:
    limits_json = json.dumps(asdict(limits), sort_keys=True, separators=(",", ":"))
    payload = {
        "fee_rate": fee_rate,
        "safety_margin": safety_margin,
        "size_limits_version": SIZE_LIMITS_VERSION,
        "size_limits": json.loads(limits_json),
    }
    economics_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "fee_rate": fee_rate,
        "safety_margin": safety_margin,
        "size_limits_version": SIZE_LIMITS_VERSION,
        "size_limits_json": limits_json,
        "decision_economics_hash": economics_hash,
    }


def _serving_probability(
    *,
    model_probability: float,
    candidate: MarketEvaluation,
    market_type: str,
    forecast: SportsForecast,
    blend_policy: MarketBlendPolicy | None,
    sport: str | None,
    serving_config_hash: str | None,
) -> tuple[float, dict[str, object]]:
    """Return the probability used by the edge gate plus its audit fields."""
    if blend_policy is None:
        return model_probability, {
            "model_probability": model_probability,
            "market_probability": candidate.market_probability,
            "serving_probability": model_probability,
        }
    if sport is None:
        raise MarketBlendBlockedError("sport is required when a market-blend policy is active")
    audit = blend_policy.apply(
        sport=sport,
        market=market_type,
        model_probability=model_probability,
        market_probability=candidate.market_probability,
        model_artifact_hash=forecast.model_artifact_hash,
        config_hash=serving_config_hash,
    )
    return audit.blended_probability, {
        "model_probability": audit.model_probability,
        "market_probability": audit.market_probability,
        "serving_probability": audit.blended_probability,
        "blend_weight": audit.weight,
        "blend_policy_artifact_hash": audit.policy_artifact_hash,
        "blend_experiment_spec_hash": audit.experiment_spec_hash,
        "blend_config_hash": audit.config_hash,
    }


def decide_team_market(
    forecast: SportsForecast,
    candidate: MarketEvaluation,
    limits: SizeLimits | None = None,
    fee_rate: float = 0.0,
    safety_margin: float = 0.0,
    *,
    blend_policy: MarketBlendPolicy | None = None,
    sport: str | None = None,
    serving_config_hash: str | None = None,
) -> BetDecision:
    """Moneyline or spread decision. `candidate.team_or_side` must equal
    `forecast.predicted_winner` — market price never influences which side
    that is, and an opponent priced attractively is never evaluated as an
    alternative recommendation, no matter how large its apparent edge.

    Real, confirmed bug fixed here: a spread candidate was previously
    priced using the predicted winner's *moneyline* win probability, not
    its probability of covering that specific line — a materially
    different (usually lower) number. Verified live: a real spread market
    produced a fabricated +23.5% "edge" this way. Spread candidates now
    require `forecast.spread_probabilities[line][predicted_winner]`
    (populated by the caller before market inspection, same as
    `totals_probabilities`) and fail closed with `NO_FORECAST_FOR_LINE`
    when that line has no real spread forecast, rather than falling back
    to the moneyline number."""
    limits = limits if limits is not None else SizeLimits()
    economics = _economics_audit(limits, fee_rate, safety_margin)
    if candidate.market_type not in ("moneyline", "spread"):
        raise ValueError(f"decide_team_market got market_type={candidate.market_type!r}")
    if candidate.team_or_side != forecast.predicted_winner:
        return _no_bet(
            forecast,
            candidate.market_type,
            NOT_ALIGNED_WITH_PREDICTED_WINNER,
            evaluated=candidate,
            **economics,
        )

    gate_reason = _quote_gate_reason(candidate, limits)
    if gate_reason is not None:
        return _no_bet(forecast, candidate.market_type, gate_reason, evaluated=candidate, **economics)

    if candidate.market_type == "spread":
        line_probs = forecast.spread_probabilities.get(candidate.line) if candidate.line is not None else None
        if line_probs is None or forecast.predicted_winner not in line_probs:
            return _no_bet(
                forecast,
                candidate.market_type,
                WRONG_MARKET_LINE,
                evaluated=candidate,
                **economics,
            )
        model_prob = line_probs[forecast.predicted_winner]
        assert (
            candidate.line is not None
        )  # narrowed: line_probs above is None whenever candidate.line is None
        lower_line_probs = forecast.spread_probabilities_lower.get(candidate.line, {})
        conservative_prob = lower_line_probs.get(forecast.predicted_winner, model_prob)
    else:
        # MLB-5: prefer the real, fully-composed conservative probability
        # (bootstrap bound + model_disagreement + calibration_uncertainty
        # + missingness_penalty + lineup_uncertainty) when the forecast
        # populated it; fall back to the plainer bootstrap-only
        # probability_lower for any caller/sport/test that doesn't (never
        # an error -- conservative_probabilities defaults to an empty
        # dict, so this is a real, backward-compatible preference, not a
        # required field).
        conservative_prob = forecast.conservative_probabilities.get(
            forecast.predicted_winner,
            forecast.probability_lower[forecast.predicted_winner],
        )
        model_prob = forecast.calibrated_probabilities[forecast.predicted_winner]

    try:
        serving_prob, audit = _serving_probability(
            model_probability=model_prob,
            candidate=candidate,
            market_type=candidate.market_type,
            forecast=forecast,
            blend_policy=blend_policy,
            sport=sport,
            serving_config_hash=serving_config_hash,
        )
        serving_conservative_prob, _conservative_audit = _serving_probability(
            model_probability=conservative_prob,
            candidate=candidate,
            market_type=candidate.market_type,
            forecast=forecast,
            blend_policy=blend_policy,
            sport=sport,
            serving_config_hash=serving_config_hash,
        )
    except MarketBlendBlockedError as exc:
        return _no_bet(
            forecast,
            candidate.market_type,
            MARKET_BLEND_POLICY_BLOCKED,
            evaluated=candidate,
            model_probability=model_prob,
            market_probability=candidate.market_probability,
            model_conservative_probability=conservative_prob,
            blend_policy_artifact_hash=blend_policy.artifact_hash,
            blend_experiment_spec_hash=blend_policy.experiment_spec_hash_for(sport, candidate.market_type)
            if sport is not None
            else None,
            blend_config_hash=serving_config_hash,
            serving_policy_block_reason=str(exc),
            **economics,
        )

    audit["model_conservative_probability"] = conservative_prob
    audit["serving_conservative_probability"] = serving_conservative_prob
    audit.update(economics)

    cost_adjusted_edge = serving_conservative_prob - candidate.depth_adjusted_price - fee_rate - safety_margin
    if cost_adjusted_edge <= 0:
        return _no_bet(
            forecast,
            candidate.market_type,
            NO_EDGE_AFTER_COSTS,
            cost_adjusted_edge,
            evaluated=candidate,
            **audit,
        )

    sizing = edge_scaled_units(
        model_prob=serving_prob,
        conservative_prob=serving_conservative_prob,
        best_ask=candidate.executable_ask,
        limits=limits,
    )
    units = sizing.get("units", 0.0)
    if units <= 0:
        return _no_bet(
            forecast,
            candidate.market_type,
            str(sizing.get("reason", ZERO_SIZED)),
            cost_adjusted_edge,
            evaluated=candidate,
            **audit,
        )

    return BetDecision(
        event_id=forecast.event_id,
        action="BET",
        predicted_winner=forecast.predicted_winner,
        market_type=candidate.market_type,
        selected_market=candidate,
        units=units,
        reason_code=QUALIFIED,
        cost_adjusted_edge=cost_adjusted_edge,
        evaluated_market=candidate,
        **audit,
    )


def decide_total(
    forecast: SportsForecast,
    candidate: MarketEvaluation,
    limits: SizeLimits | None = None,
    fee_rate: float = 0.0,
    safety_margin: float = 0.0,
    *,
    blend_policy: MarketBlendPolicy | None = None,
    sport: str | None = None,
    serving_config_hash: str | None = None,
) -> BetDecision:
    """Totals decision. The frozen side is whichever of OVER/UNDER the
    sports-only distribution favors for this exact line — decided by
    `forecast.frozen_totals_side()`, before `candidate`'s market price is
    inspected here."""
    limits = limits if limits is not None else SizeLimits()
    economics = _economics_audit(limits, fee_rate, safety_margin)
    if candidate.market_type != "total":
        raise ValueError(f"decide_total got market_type={candidate.market_type!r}")
    if candidate.line is None:
        return _no_bet(forecast, "total", NO_FORECAST_FOR_LINE, evaluated=candidate, **economics)

    frozen_side = forecast.frozen_totals_side(candidate.line)
    if frozen_side is None:
        return _no_bet(forecast, "total", WRONG_MARKET_LINE, evaluated=candidate, **economics)
    if candidate.team_or_side != frozen_side:
        return _no_bet(
            forecast,
            "total",
            NOT_ALIGNED_WITH_FROZEN_TOTALS_SIDE,
            evaluated=candidate,
            **economics,
        )

    gate_reason = _quote_gate_reason(candidate, limits)
    if gate_reason is not None:
        return _no_bet(forecast, "total", gate_reason, evaluated=candidate, **economics)

    model_prob = forecast.totals_probabilities[candidate.line][frozen_side]
    lower_line_probs = forecast.totals_probabilities_lower.get(candidate.line, {})
    conservative_prob = lower_line_probs.get(frozen_side, model_prob)
    try:
        serving_prob, audit = _serving_probability(
            model_probability=model_prob,
            candidate=candidate,
            market_type="total",
            forecast=forecast,
            blend_policy=blend_policy,
            sport=sport,
            serving_config_hash=serving_config_hash,
        )
        serving_conservative_prob, _conservative_audit = _serving_probability(
            model_probability=conservative_prob,
            candidate=candidate,
            market_type="total",
            forecast=forecast,
            blend_policy=blend_policy,
            sport=sport,
            serving_config_hash=serving_config_hash,
        )
    except MarketBlendBlockedError as exc:
        return _no_bet(
            forecast,
            "total",
            MARKET_BLEND_POLICY_BLOCKED,
            evaluated=candidate,
            model_probability=model_prob,
            market_probability=candidate.market_probability,
            model_conservative_probability=conservative_prob,
            blend_policy_artifact_hash=blend_policy.artifact_hash,
            blend_experiment_spec_hash=blend_policy.experiment_spec_hash_for(sport, "total")
            if sport is not None
            else None,
            blend_config_hash=serving_config_hash,
            serving_policy_block_reason=str(exc),
            **economics,
        )

    audit["model_conservative_probability"] = conservative_prob
    audit["serving_conservative_probability"] = serving_conservative_prob
    audit.update(economics)

    cost_adjusted_edge = serving_conservative_prob - candidate.depth_adjusted_price - fee_rate - safety_margin
    if cost_adjusted_edge <= 0:
        return _no_bet(
            forecast,
            "total",
            NO_EDGE_AFTER_COSTS,
            cost_adjusted_edge,
            evaluated=candidate,
            **audit,
        )

    sizing = edge_scaled_units(
        model_prob=serving_prob,
        conservative_prob=serving_conservative_prob,
        best_ask=candidate.executable_ask,
        limits=limits,
    )
    units = sizing.get("units", 0.0)
    if units <= 0:
        return _no_bet(
            forecast,
            "total",
            str(sizing.get("reason", ZERO_SIZED)),
            cost_adjusted_edge,
            evaluated=candidate,
            **audit,
        )

    return BetDecision(
        event_id=forecast.event_id,
        action="BET",
        predicted_winner=forecast.predicted_winner,
        market_type="total",
        selected_market=candidate,
        units=units,
        reason_code=QUALIFIED,
        cost_adjusted_edge=cost_adjusted_edge,
        evaluated_market=candidate,
        **audit,
    )


def evaluate_game(
    forecast: SportsForecast,
    candidates: list[MarketEvaluation],
    limits: SizeLimits | None = None,
    *,
    blend_policy: MarketBlendPolicy | None = None,
    sport: str | None = None,
    serving_config_hash: str | None = None,
) -> list[BetDecision]:
    """Evaluate every candidate market for one game. Every candidate produces
    a decision — including NO_BET — so a caller can persist a full audit
    trail, not just the accepted bets (CLAUDE.md: "every event must persist
    a decision, including no-bets")."""
    limits = limits if limits is not None else SizeLimits()
    decisions = []
    for c in candidates:
        if c.market_type in ("moneyline", "spread"):
            decisions.append(
                decide_team_market(
                    forecast,
                    c,
                    limits,
                    blend_policy=blend_policy,
                    sport=sport,
                    serving_config_hash=serving_config_hash,
                )
            )
        elif c.market_type == "total":
            decisions.append(
                decide_total(
                    forecast,
                    c,
                    limits,
                    blend_policy=blend_policy,
                    sport=sport,
                    serving_config_hash=serving_config_hash,
                )
            )
    return decisions
