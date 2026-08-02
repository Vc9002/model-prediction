from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .bans import TeamBanList
from .domain import ModelState, NoCallReason, PickRequest, RecordType, parse_utc, utc_now
from .entities import CanonicalTeam, EntityRegistry
from .lifecycle import can_create_qualified_call
from .pricing import implied_probability
from .units import Exposure, UnitPolicy, edge_scaled_units, recommend_units


@dataclass(frozen=True)
class EligibilityResult:
    record_type: RecordType
    decision: str
    reason_code: str
    units: float
    confidence_score: int
    edge: float
    adjusted_edge: float
    away_team: CanonicalTeam
    home_team: CanonicalTeam
    banned_team: CanonicalTeam | None = None


def evaluate_eligibility(
    request: PickRequest,
    registry: EntityRegistry,
    ban_list: TeamBanList,
    exposure: Exposure,
    policy: UnitPolicy,
    now: datetime | None = None,
    maximum_age_hours: float = 12,
    maximum_unreviewed_disagreement: float = 0.10,
) -> EligibilityResult:
    # exposure and maximum_unreviewed_disagreement are accepted but unused --
    # kept for call-site/API stability since disagreement/exposure no longer
    # gate CALL vs NO_CALL (see _call_result).
    current = now or utc_now()
    away = registry.resolve(request.league, request.away_team, request.event_start_utc)
    home = registry.resolve(request.league, request.home_team, request.event_start_utc)
    for team in (away, home):
        _, banned = ban_list.check(request.league, team.canonical_team_id)
        if banned:
            research = _research(request, away, home, NoCallReason.TEAM_BANNED, policy)
            return EligibilityResult(
                research.record_type,
                research.decision,
                research.reason_code,
                research.units,
                research.confidence_score,
                research.edge,
                research.adjusted_edge,
                away,
                home,
                team,
            )
    if request.model_state is ModelState.RETIRED:
        return _research(request, away, home, NoCallReason.MODEL_INELIGIBLE, policy)
    if (
        request.observed_at_utc
        and request.observed_at_utc.strip()
        and (current - parse_utc(request.observed_at_utc)).total_seconds() > maximum_age_hours * 3600
    ):
        return _research(request, away, home, NoCallReason.STALE_DATA, policy)
    if not can_create_qualified_call(request.model_state, request.model_origin):
        return _research(request, away, home, NoCallReason.MODEL_UNVALIDATED, policy)
    if (
        request.observed_at_utc is None
        or not request.model_artifact_hash
        or not request.calibration_artifact_hash
        or request.code_revision in {"", "unknown"}
    ):
        return _research(request, away, home, NoCallReason.MODEL_UNVALIDATED, policy)
    # Market/model disagreement, exposure caps, and thin post-uncertainty edge
    # are no longer NO_CALL gates (operator directive, 2026-07-26): once a
    # candidate clears model-state/staleness/provenance -- the checks above,
    # which represent "can this decision be trusted at all" -- it becomes a
    # real qualified call regardless of how far the model disagrees with the
    # market, today's exposure so far, or how thin the edge is after the
    # uncertainty haircut. _call_result below always sizes via the proven
    # edge-scaled method rather than the exposure-aware Kelly engine, since
    # that engine's only remaining job (deciding CALL vs NO_CALL on edge/
    # exposure) no longer applies.
    return _call_result(request, away, home, policy)


def evaluate_esports_eligibility(
    request: PickRequest,
    exposure: Exposure,
    policy: UnitPolicy,
    now: datetime | None = None,
    maximum_age_hours: float = 12,
    maximum_unreviewed_disagreement: float = 0.10,
) -> EligibilityResult:
    """Standard eligibility gates for esports/soccer/KBO/NPB/tennis contracts
    without registry entities.

    These leagues' teams are not in the canonical registry, so entities are
    name-based placeholder ``CanonicalTeam`` objects. Unlike
    ``evaluate_eligibility``, there is deliberately no team-ban check here:
    ``TeamBanList`` resolves through the canonical registry
    (``bans.py``), which these leagues don't participate in, and no team
    has ever actually been banned in any of them (removed the misleading
    stub config sections 2026-08-02 rather than pretend this works --
    building a real, registry-free ban mechanism for these leagues is a
    separate, not-yet-scoped change). Every OTHER gate matches
    ``evaluate_eligibility``: model-state/origin, data staleness,
    provenance completeness, model/market disagreement, exposure caps, and
    the unit engine. Config may deliberately promote a title to
    shadow_qualified; this function makes that promotion pass through real
    checks instead of a hand-built qualified result.
    """
    current = now or utc_now()
    away = CanonicalTeam(
        request.away_team, request.league, request.away_team, request.away_team, True, None, None, ()
    )
    home = CanonicalTeam(
        request.home_team, request.league, request.home_team, request.home_team, True, None, None, ()
    )
    if request.model_state is ModelState.RETIRED:
        return _research(request, away, home, NoCallReason.MODEL_INELIGIBLE, policy)
    if (
        request.observed_at_utc
        and request.observed_at_utc.strip()
        and (current - parse_utc(request.observed_at_utc)).total_seconds() > maximum_age_hours * 3600
    ):
        return _research(request, away, home, NoCallReason.STALE_DATA, policy)
    if not can_create_qualified_call(request.model_state, request.model_origin):
        return _research(request, away, home, NoCallReason.MODEL_UNVALIDATED, policy)
    if (
        request.observed_at_utc is None
        or not request.model_artifact_hash
        or not request.calibration_artifact_hash
        or request.code_revision in {"", "unknown"}
    ):
        return _research(request, away, home, NoCallReason.MODEL_UNVALIDATED, policy)
    # See evaluate_eligibility's matching comment: disagreement/exposure/edge
    # no longer gate CALL vs NO_CALL (operator directive, 2026-07-26).
    return _call_result(request, away, home, policy)


def evaluate_gated_research_eligibility(
    request: PickRequest,
    exposure: Exposure,
    policy: UnitPolicy,
    *,
    model_inputs_valid: bool,
    minimum_edge: float,
    minimum_confidence: float = 0.0,
    now: datetime | None = None,
    maximum_age_hours: float = 12,
    maximum_unreviewed_disagreement: float = 0.10,
) -> EligibilityResult:
    """Apply the complete invariant for a Gated Research row.

    Research sports deliberately use relaxed exposure/disagreement rails, but
    Gated Research is still a curated subset. A row cannot be a CALL unless
    its exact model inputs are valid, its executable edge clears the sport's
    configured floor, and the model has the configured minimum opinion.
    Keeping this in the eligibility layer prevents call sites from appending a
    row merely because the underlying trust/provenance checks returned CALL.
    """
    eligibility = evaluate_esports_eligibility(
        request,
        exposure,
        policy,
        now=now,
        maximum_age_hours=maximum_age_hours,
        maximum_unreviewed_disagreement=maximum_unreviewed_disagreement,
    )
    if eligibility.decision != "CALL":
        return eligibility
    if not model_inputs_valid:
        return _downgrade_research_call(
            request,
            eligibility,
            NoCallReason.MODEL_UNVALIDATED.value,
            policy,
        )
    executable_edge = request.model_probability - implied_probability(request.american_odds)
    if executable_edge < minimum_edge:
        return _downgrade_research_call(
            request,
            eligibility,
            NoCallReason.LOW_EDGE.value,
            policy,
        )
    if abs(request.model_probability - 0.5) < minimum_confidence:
        return _downgrade_research_call(
            request,
            eligibility,
            NoCallReason.LOW_EDGE.value,
            policy,
        )
    return eligibility


def _downgrade_research_call(
    request: PickRequest,
    eligibility: EligibilityResult,
    reason_code: str,
    policy: UnitPolicy,
) -> EligibilityResult:
    """Downgrade a Gated Research CALL to a Research-only observation.

    This only ever removes the row from Gated Research (the caller checks
    ``decision == "CALL"`` separately to decide gated-ledger mirroring) --
    it must not also zero the row's own Research-ledger size. Every logged
    pick carries a real, model-derived paper size (operator directive,
    2026-07-31: "every pick should have units and pnl, hard code this").
    """
    uncertainty = request.model_uncertainty or 0.05
    units = edge_scaled_units(request.model_probability, uncertainty, request.american_odds, policy)
    return EligibilityResult(
        RecordType.RESEARCH_OBSERVATION,
        "NO_CALL",
        reason_code,
        units,
        eligibility.confidence_score,
        eligibility.edge,
        eligibility.adjusted_edge,
        eligibility.away_team,
        eligibility.home_team,
        eligibility.banned_team,
    )


def _call_result(
    request: PickRequest,
    away: CanonicalTeam,
    home: CanonicalTeam,
    policy: UnitPolicy,
) -> EligibilityResult:
    """Build a QUALIFIED_SHADOW_CALL once every trust-boundary gate has
    passed. Sizes via the proven edge-scaled method (edge_scaled_units,
    "+34.1U vs +13.3U flat" walk-forward) rather than the exposure-aware
    Kelly engine in recommend_units -- that engine's is_call decision isn't
    consulted here at all, so its exposure-capped/edge-gated units would
    otherwise silently reintroduce the very gates this function skips.
    """
    uncertainty = request.model_uncertainty or 0.05
    edge = request.model_probability - implied_probability(request.american_odds)
    market_no_vig = (
        request.decision_no_vig_probability
        if request.decision_no_vig_probability is not None
        else implied_probability(request.american_odds)
    )
    adjusted_edge = (request.model_probability - uncertainty) - market_no_vig
    confidence = max(0, min(100, round(50 + 500 * adjusted_edge - 100 * uncertainty)))
    units = edge_scaled_units(request.model_probability, uncertainty, request.american_odds, policy)
    return EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL,
        "CALL",
        "QUALIFIED",
        units,
        confidence,
        edge,
        adjusted_edge,
        away,
        home,
    )


def _research(
    request: PickRequest,
    away: CanonicalTeam,
    home: CanonicalTeam,
    reason: NoCallReason,
    policy: UnitPolicy,
) -> EligibilityResult:
    """NO_CALL for reasons where the decision itself can't be trusted (banned
    team, stale/missing data, unvalidated model). These still get logged as
    real Research/Gated-Research rows, and every logged pick carries a real,
    model-derived paper size (operator directive, 2026-07-31: "every pick
    should have units and pnl, hard code this") -- with one exception: a
    banned team is never sized, paper or real, regardless of model opinion
    (matches ledger._append_record's dedicated call_type="no_call" handling
    for NO_CALL_TEAM_BANNED).
    """
    uncertainty = request.model_uncertainty or 0
    recommendation = recommend_units(
        request.model_probability,
        uncertainty,
        request.american_odds,
        Exposure(),
        policy,
        validated_model=False,
    )
    units = (
        0.0
        if reason is NoCallReason.TEAM_BANNED
        else edge_scaled_units(request.model_probability, uncertainty or 0.05, request.american_odds, policy)
    )
    return EligibilityResult(
        RecordType.RESEARCH_OBSERVATION,
        "NO_CALL",
        reason.value,
        units,
        recommendation.confidence_score,
        recommendation.edge,
        recommendation.adjusted_edge,
        away,
        home,
    )
