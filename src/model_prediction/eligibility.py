from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .bans import TeamBanList
from .domain import ModelState, NoCallReason, PickRequest, RecordType, parse_utc, utc_now
from .entities import CanonicalTeam, EntityRegistry
from .lifecycle import can_create_qualified_call
from .pricing import implied_probability
from .units import Exposure, UnitPolicy, recommend_units


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
    # Disagreement gate compares against the de-vigged probability when available;
    # raw implied probability (with vig) is only a fallback.
    market_probability_for_disagreement = (
        request.decision_no_vig_probability
        if request.decision_no_vig_probability is not None
        else implied_probability(request.american_odds)
    )
    if abs(request.model_probability - market_probability_for_disagreement) > maximum_unreviewed_disagreement:
        return _research(request, away, home, NoCallReason.LARGE_DISAGREEMENT, policy)
    recommendation = recommend_units(
        request.model_probability,
        request.model_uncertainty,
        request.american_odds,
        exposure,
        policy,
        validated_model=True,
    )
    if not recommendation.is_call:
        reason = NoCallReason.EXPOSURE_LIMIT if "exposure" in recommendation.reason else NoCallReason.LOW_EDGE
        return EligibilityResult(
            RecordType.RESEARCH_OBSERVATION,
            "NO_CALL",
            reason.value,
            0,
            recommendation.confidence_score,
            recommendation.edge,
            recommendation.adjusted_edge,
            away,
            home,
        )
    return EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL,
        "CALL",
        "QUALIFIED",
        recommendation.units,
        recommendation.confidence_score,
        recommendation.edge,
        recommendation.adjusted_edge,
        away,
        home,
    )


def evaluate_esports_eligibility(
    request: PickRequest,
    exposure: Exposure,
    policy: UnitPolicy,
    now: datetime | None = None,
    maximum_age_hours: float = 12,
    maximum_unreviewed_disagreement: float = 0.10,
) -> EligibilityResult:
    """Standard eligibility gates for esports contracts without registry entities.

    Esports teams are not in the canonical registry yet, so entity and ban
    resolution is name-based (placeholder CanonicalTeams). Every OTHER gate is
    identical to ``evaluate_eligibility``: model-state/origin, data staleness,
    provenance completeness, model/market disagreement, exposure caps, and the
    unit engine. Config may deliberately promote a title to shadow_qualified;
    this function makes that promotion pass through real checks instead of a
    hand-built qualified result.
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
    market_probability = (
        request.decision_no_vig_probability
        if request.decision_no_vig_probability is not None
        else implied_probability(request.american_odds)
    )
    if abs(request.model_probability - market_probability) > maximum_unreviewed_disagreement:
        return _research(request, away, home, NoCallReason.LARGE_DISAGREEMENT, policy)
    recommendation = recommend_units(
        request.model_probability,
        request.model_uncertainty,
        request.american_odds,
        exposure,
        policy,
        validated_model=True,
    )
    if not recommendation.is_call:
        reason = NoCallReason.EXPOSURE_LIMIT if "exposure" in recommendation.reason else NoCallReason.LOW_EDGE
        return EligibilityResult(
            RecordType.RESEARCH_OBSERVATION,
            "NO_CALL",
            reason.value,
            0,
            recommendation.confidence_score,
            recommendation.edge,
            recommendation.adjusted_edge,
            away,
            home,
        )
    return EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL,
        "CALL",
        "QUALIFIED",
        recommendation.units,
        recommendation.confidence_score,
        recommendation.edge,
        recommendation.adjusted_edge,
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
    uncertainty = request.model_uncertainty or 0
    recommendation = recommend_units(
        request.model_probability,
        uncertainty,
        request.american_odds,
        Exposure(),
        policy,
        validated_model=False,
    )
    return EligibilityResult(
        RecordType.RESEARCH_OBSERVATION,
        "NO_CALL",
        reason.value,
        0,
        recommendation.confidence_score,
        recommendation.edge,
        recommendation.adjusted_edge,
        away,
        home,
    )
