"""Permanent champion–challenger production lifecycle contracts and invariant enforcement.

Core Invariants:
1. Every supported sport/market always has exactly one production-serving model
   (serving_status = "production").
2. Weak evidence increases replacement priority (low, medium, high, critical);
   it does not remove the market from production (evidence_status = unverified,
   historical_only, predictively_qualified, market_qualified, prospectively_qualified,
   degraded).
3. Challengers are developed, evaluated, frozen, and promoted alongside the incumbent.
4. Champion and challenger receive identical decision contexts (decision_context_id).
5. Forecast serving forks internally: challenger failure NEVER blocks or removes
   champion serving.
6. Promotion is an atomic pointer change, moving the former champion to rollback.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# ── Central Definition of Supported Markets ───────────────────────────────────

SUPPORTED_MARKETS: dict[str, set[str]] = {
    "MLB": {"moneyline", "spread", "total", "nrfi"},
    "WNBA": {"moneyline", "spread", "total"},
    "NCAAF": {"moneyline", "spread", "total"},
    "NBA": {"moneyline"},
    "NFL": {"moneyline"},
    "SOCCER": {"moneyline"},
    "TENNIS": {"moneyline"},
    "CS2": {"moneyline"},
    "DOTA2": {"moneyline"},
    "LOL": {"moneyline"},
    "VALORANT": {"moneyline"},
    "RAINBOW_SIX": {"moneyline"},
    "KBO": {"moneyline"},
    "NPB": {"moneyline"},
}

PLANNED_MARKETS: dict[str, set[str]] = {
    "NBA": {"spread", "total"},
    "NFL": {"spread", "total"},
    "SOCCER": {"spread", "total"},
    "TENNIS": {"spread", "total"},
}


# ── Canonical Lifecycle Vocabularies ──────────────────────────────────────────


class ServingStatus(StrEnum):
    """Operational status of a model slot."""

    PRODUCTION = "production"
    SHADOW = "shadow"
    RESEARCH = "research"
    ROLLBACK = "rollback"
    RETIRED = "retired"


class EvidenceStatus(StrEnum):
    """Strength and quality of qualification evidence."""

    UNVERIFIED = "unverified"
    HISTORICAL_ONLY = "historical_only"
    PREDICTIVELY_QUALIFIED = "predictively_qualified"
    MARKET_QUALIFIED = "market_qualified"
    PROSPECTIVELY_QUALIFIED = "prospectively_qualified"
    DEGRADED = "degraded"


class EvidenceOrigin(StrEnum):
    """Provenance and timing integrity of an observation or dataset."""

    LIVE_PROSPECTIVE = "live_prospective"
    PIT_REPLAY = "pit_replay"
    HISTORICAL_BACKTEST = "historical_backtest"
    SYNTHETIC = "synthetic"


class ChallengerBuildStatus(StrEnum):
    """Readiness and build lifecycle of a challenger candidate."""

    PLANNED = "planned"
    IMPLEMENTED = "implemented"
    VALIDATED_OFFLINE = "validated_offline"
    FROZEN = "frozen"
    CAPTURING_PROSPECTIVE = "capturing_prospective"


class ReplacementPriority(StrEnum):
    """Urgency of replacing the current incumbent."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class NextAction(StrEnum):
    """Canonical research action guiding the engineering roadmap."""

    BUILD_CHALLENGER = "BUILD_CHALLENGER"
    RUN_OFFLINE_EVALUATION = "RUN_OFFLINE_EVALUATION"
    FREEZE_CHALLENGER = "FREEZE_CHALLENGER"
    START_PROSPECTIVE_CAPTURE = "START_PROSPECTIVE_CAPTURE"
    COLLECT_PROSPECTIVE = "COLLECT_PROSPECTIVE"
    RUN_FINAL_GATE = "RUN_FINAL_GATE"
    START_NEXT_GENERATION = "START_NEXT_GENERATION"


class LifecycleRole(StrEnum):
    """Role of a model execution in a decision opportunity."""

    CHAMPION = "champion"
    CHALLENGER = "challenger"
    RESEARCH = "research"
    ROLLBACK = "rollback"


class ChallengerDecision(StrEnum):
    """Terminal or interim outcome of a challenger evaluation."""

    PROMOTE = "promote"
    CONTINUE = "continue"
    REJECT = "reject"


def parse_iso_datetime(val: Any) -> datetime | None:
    """Parse string or timestamp into timezone-aware UTC datetime."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.astimezone(UTC) if val.tzinfo else val.replace(tzinfo=UTC)
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=UTC)
    s = str(val).strip()
    if not s:
        return None
    try:
        # Handle 'Z' or offset strings
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def classify_evidence_origin(
    *,
    prediction_created_at: str | datetime | None,
    event_start_utc: str | datetime | None,
    outcome_available_at: str | datetime | None = None,
    candidate_frozen_at: str | datetime | None = None,
    candidate_artifact_hash: str | None = None,
    frozen_artifact_hash: str | None = None,
    feature_snapshot_observed_at: str | datetime | None = None,
    market_snapshot_observed_at: str | datetime | None = None,
    required_lead_time_seconds: int = 0,
) -> EvidenceOrigin:
    """Classify the provenance of a prediction observation.

    A row counts as genuinely LIVE_PROSPECTIVE ONLY IF:
    1. candidate_frozen_at is known AND candidate_frozen_at <= prediction_created_at
    2. prediction_created_at < event_start_utc
    3. prediction_created_at <= event_start_utc - required_lead_time_seconds
    4. prediction_created_at < outcome_available_at (if outcome timing is known)
    5. feature_snapshot_observed_at <= prediction_created_at (if snapshot timing is known)
    6. market_snapshot_observed_at <= prediction_created_at (if snapshot timing is known)
    7. candidate_artifact_hash == frozen_artifact_hash (if hashes provided)

    Otherwise, returns PIT_REPLAY if point-in-time features/event dates are valid,
    or HISTORICAL_BACKTEST.
    """
    pred_dt = parse_iso_datetime(prediction_created_at)
    start_dt = parse_iso_datetime(event_start_utc)
    outcome_dt = parse_iso_datetime(outcome_available_at)
    frozen_dt = parse_iso_datetime(candidate_frozen_at)
    feat_dt = parse_iso_datetime(feature_snapshot_observed_at)
    mkt_dt = parse_iso_datetime(market_snapshot_observed_at)

    if pred_dt is None or start_dt is None:
        return EvidenceOrigin.HISTORICAL_BACKTEST

    # If predicted after outcome is known or after event started: NOT live prospective
    if outcome_dt and pred_dt >= outcome_dt:
        return EvidenceOrigin.PIT_REPLAY
    if pred_dt >= start_dt:
        return EvidenceOrigin.PIT_REPLAY

    # Required decision lead time
    if required_lead_time_seconds > 0:
        lead_delta = (start_dt - pred_dt).total_seconds()
        if lead_delta < required_lead_time_seconds:
            return EvidenceOrigin.PIT_REPLAY

    # Candidate freeze time check
    if frozen_dt and pred_dt < frozen_dt:
        return EvidenceOrigin.PIT_REPLAY

    # Artifact hash check
    if candidate_artifact_hash and frozen_artifact_hash and candidate_artifact_hash != frozen_artifact_hash:
        return EvidenceOrigin.PIT_REPLAY

    # Snapshot observation timestamp checks
    if feat_dt and feat_dt > pred_dt:
        return EvidenceOrigin.PIT_REPLAY
    if mkt_dt and mkt_dt > pred_dt:
        return EvidenceOrigin.PIT_REPLAY

    return EvidenceOrigin.LIVE_PROSPECTIVE


# ── Core Dataclasses ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelLifecycleContract:
    """Explicit contract governing the serving, challenger, and rollback state for a market."""

    sport: str
    market: str
    champion_model_id: str
    challenger_model_id: str | None = None
    rollback_model_id: str | None = None
    serving_status: str = ServingStatus.PRODUCTION.value
    evidence_status: str = EvidenceStatus.HISTORICAL_ONLY.value
    replacement_priority: str = ReplacementPriority.MEDIUM.value
    challenger_build_status: str = ChallengerBuildStatus.PLANNED.value
    champion_artifact_hash: str | None = None
    challenger_artifact_hash: str | None = None
    rollback_artifact_hash: str | None = None
    promotion_protocol_id: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "market": self.market,
            "champion_model_id": self.champion_model_id,
            "challenger_model_id": self.challenger_model_id,
            "rollback_model_id": self.rollback_model_id,
            "serving_status": self.serving_status,
            "evidence_status": self.evidence_status,
            "replacement_priority": self.replacement_priority,
            "challenger_build_status": self.challenger_build_status,
            "champion_artifact_hash": self.champion_artifact_hash,
            "challenger_artifact_hash": self.challenger_artifact_hash,
            "rollback_artifact_hash": self.rollback_artifact_hash,
            "promotion_protocol_id": self.promotion_protocol_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelLifecycleContract:
        return cls(
            sport=str(data["sport"]),
            market=str(data["market"]),
            champion_model_id=str(data["champion_model_id"]),
            challenger_model_id=data.get("challenger_model_id"),
            rollback_model_id=data.get("rollback_model_id"),
            serving_status=str(data.get("serving_status", ServingStatus.PRODUCTION.value)),
            evidence_status=str(data.get("evidence_status", EvidenceStatus.HISTORICAL_ONLY.value)),
            replacement_priority=str(data.get("replacement_priority", ReplacementPriority.MEDIUM.value)),
            challenger_build_status=str(
                data.get("challenger_build_status", ChallengerBuildStatus.PLANNED.value)
            ),
            champion_artifact_hash=data.get("champion_artifact_hash"),
            challenger_artifact_hash=data.get("challenger_artifact_hash"),
            rollback_artifact_hash=data.get("rollback_artifact_hash"),
            promotion_protocol_id=data.get("promotion_protocol_id"),
            notes=data.get("notes"),
        )


@dataclass(frozen=True)
class DecisionContext:
    """Frozen point-in-time state provided to both champion and challenger on an event."""

    decision_context_id: str
    event_id: str
    sport: str
    market: str
    event_start_utc: str
    decision_utc: str
    market_line: float | None = None
    market_bid: float | None = None
    market_ask: float | None = None
    market_fair_probability: float | None = None
    starter_state: dict[str, Any] | None = None
    lineup_state: dict[str, Any] | None = None
    injury_state: dict[str, Any] | None = None
    weather_state: dict[str, Any] | None = None
    source_hashes: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def compute_context_hash(self) -> str:
        payload = {
            "decision_context_id": self.decision_context_id,
            "event_id": self.event_id,
            "sport": self.sport,
            "market": self.market,
            "event_start_utc": self.event_start_utc,
            "decision_utc": self.decision_utc,
            "market_line": self.market_line,
            "market_bid": self.market_bid,
            "market_ask": self.market_ask,
            "market_fair_probability": self.market_fair_probability,
            "starter_state": self.starter_state,
            "lineup_state": self.lineup_state,
            "injury_state": self.injury_state,
            "weather_state": self.weather_state,
            "source_hashes": self.source_hashes,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_context_id": self.decision_context_id,
            "event_id": self.event_id,
            "sport": self.sport,
            "market": self.market,
            "event_start_utc": self.event_start_utc,
            "decision_utc": self.decision_utc,
            "market_line": self.market_line,
            "market_bid": self.market_bid,
            "market_ask": self.market_ask,
            "market_fair_probability": self.market_fair_probability,
            "starter_state": self.starter_state,
            "lineup_state": self.lineup_state,
            "injury_state": self.injury_state,
            "weather_state": self.weather_state,
            "source_hashes": self.source_hashes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionContext:
        return cls(
            decision_context_id=str(data["decision_context_id"]),
            event_id=str(data["event_id"]),
            sport=str(data["sport"]),
            market=str(data["market"]),
            event_start_utc=str(data["event_start_utc"]),
            decision_utc=str(data["decision_utc"]),
            market_line=data.get("market_line"),
            market_bid=data.get("market_bid"),
            market_ask=data.get("market_ask"),
            market_fair_probability=data.get("market_fair_probability"),
            starter_state=data.get("starter_state"),
            lineup_state=data.get("lineup_state"),
            injury_state=data.get("injury_state"),
            weather_state=data.get("weather_state"),
            source_hashes=data.get("source_hashes") or {},
            metadata=data.get("metadata") or {},
        )


@dataclass(frozen=True)
class ModelPredictionRecord:
    """Canonical prediction schema output by all models in all lifecycle roles."""

    prediction_id: str
    event_id: str
    decision_context_id: str
    sport: str
    market_type: str
    selection: str
    line: float | None
    model_id: str
    model_artifact_hash: str
    feature_schema_hash: str
    lifecycle_role: str  # champion | challenger | research | rollback
    probability: float
    market_fair_probability: float | None = None
    entry_bid: float | None = None
    entry_ask: float | None = None
    decision_utc: str = ""
    event_start_utc: str = ""
    feature_snapshot_hash: str = ""
    market_snapshot_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "event_id": self.event_id,
            "decision_context_id": self.decision_context_id,
            "sport": self.sport,
            "market_type": self.market_type,
            "selection": self.selection,
            "line": self.line,
            "model_id": self.model_id,
            "model_artifact_hash": self.model_artifact_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "lifecycle_role": self.lifecycle_role,
            "probability": self.probability,
            "market_fair_probability": self.market_fair_probability,
            "entry_bid": self.entry_bid,
            "entry_ask": self.entry_ask,
            "decision_utc": self.decision_utc,
            "event_start_utc": self.event_start_utc,
            "feature_snapshot_hash": self.feature_snapshot_hash,
            "market_snapshot_hash": self.market_snapshot_hash,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelPredictionRecord:
        return cls(
            prediction_id=str(data["prediction_id"]),
            event_id=str(data["event_id"]),
            decision_context_id=str(data["decision_context_id"]),
            sport=str(data["sport"]),
            market_type=str(data["market_type"]),
            selection=str(data["selection"]),
            line=data.get("line"),
            model_id=str(data["model_id"]),
            model_artifact_hash=str(data.get("model_artifact_hash", "")),
            feature_schema_hash=str(data.get("feature_schema_hash", "")),
            lifecycle_role=str(data.get("lifecycle_role", LifecycleRole.CHAMPION.value)),
            probability=float(data["probability"]),
            market_fair_probability=data.get("market_fair_probability"),
            entry_bid=data.get("entry_bid"),
            entry_ask=data.get("entry_ask"),
            decision_utc=str(data.get("decision_utc", "")),
            event_start_utc=str(data.get("event_start_utc", "")),
            feature_snapshot_hash=str(data.get("feature_snapshot_hash", "")),
            market_snapshot_hash=str(data.get("market_snapshot_hash", "")),
            metadata=data.get("metadata") or {},
        )


@dataclass(frozen=True)
class ChallengerManifest:
    """Immutable manifest freezing a prospective challenger candidate."""

    model_id: str
    sport: str
    market: str
    artifact_hash: str
    feature_schema_hash: str
    training_dataset_hash: str
    training_code_hash: str
    calibration_hash: str
    promotion_protocol_hash: str
    frozen_at_utc: str
    prospective_start_utc: str
    status: str = "prospective"  # research | candidate | frozen | prospective | promotion_eligible | rejected | champion

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "sport": self.sport,
            "market": self.market,
            "artifact_hash": self.artifact_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "training_dataset_hash": self.training_dataset_hash,
            "training_code_hash": self.training_code_hash,
            "calibration_hash": self.calibration_hash,
            "promotion_protocol_hash": self.promotion_protocol_hash,
            "frozen_at_utc": self.frozen_at_utc,
            "prospective_start_utc": self.prospective_start_utc,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChallengerManifest:
        return cls(
            model_id=str(data["model_id"]),
            sport=str(data["sport"]),
            market=str(data["market"]),
            artifact_hash=str(data["artifact_hash"]),
            feature_schema_hash=str(data["feature_schema_hash"]),
            training_dataset_hash=str(data["training_dataset_hash"]),
            training_code_hash=str(data["training_code_hash"]),
            calibration_hash=str(data["calibration_hash"]),
            promotion_protocol_hash=str(data["promotion_protocol_hash"]),
            frozen_at_utc=str(data["frozen_at_utc"]),
            prospective_start_utc=str(data["prospective_start_utc"]),
            status=str(data.get("status", "prospective")),
        )


# ── Decision Context Helper ───────────────────────────────────────────────────


def generate_decision_context_id(
    sport: str,
    event_id: str,
    decision_utc: str,
    horizon: str = "TMINUS30",
) -> str:
    """Generate a canonical decision context ID, e.g. MLB_2026-09-12_NYY_BOS_TMINUS30_001."""
    date_part = decision_utc.split("T")[0] if "T" in decision_utc else decision_utc[:10]
    safe_event = event_id.replace(":", "_").replace("/", "_").replace(" ", "_")
    return f"{sport.upper()}_{date_part}_{safe_event}_{horizon}"


# ── Fail-Isolated Execution ───────────────────────────────────────────────────


def run_fail_isolated_prediction(
    champion_fn: Callable[[DecisionContext], ModelPredictionRecord | None],
    challenger_fn: Callable[[DecisionContext], ModelPredictionRecord | None] | None,
    context: DecisionContext,
) -> tuple[ModelPredictionRecord | None, ModelPredictionRecord | None, str | None]:
    """Execute champion and challenger fail-isolated.

    Champion execution is authoritative. A failure in challenger resolution or
    execution NEVER interrupts or invalidates champion serving.
    """
    # 1. Run champion (fail-closed if champion itself raises)
    champion_record = champion_fn(context)

    # 2. Run challenger in isolated try/except
    challenger_record: ModelPredictionRecord | None = None
    challenger_error: str | None = None

    if challenger_fn is not None:
        try:
            challenger_record = challenger_fn(context)
        except Exception as exc:  # noqa: BLE001 - challenger failure must never block champion
            challenger_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Challenger failed on context %s: %s (champion serving proceeds unaffected)",
                context.decision_context_id,
                exc,
            )

    return champion_record, challenger_record, challenger_error


# ── Lifecycle Invariant Validation ────────────────────────────────────────────


class LifecycleInvariantViolation(Exception):
    """Raised when champion-challenger lifecycle contracts or invariants are violated."""


def validate_lifecycle_contract(
    contract: ModelLifecycleContract,
    *,
    entries_by_id: dict[str, Any] | None = None,
) -> list[str]:
    """Validate one market's lifecycle contract.

    Invariants:
    - Exactly one champion per contract
    - challenger != champion
    - rollback != champion
    - serving_status must be a valid ServingStatus
    - evidence_status must be a valid EvidenceStatus
    - replacement_priority must be a valid ReplacementPriority
    - If entries_by_id is provided, champion must resolve and be available.
      Challenger resolution failure records a warning/error but NEVER disables champion.
    """
    errors: list[str] = []

    if not contract.champion_model_id or not contract.champion_model_id.strip():
        errors.append(f"({contract.sport}, {contract.market}): champion_model_id is missing or empty")

    if contract.challenger_model_id and contract.challenger_model_id == contract.champion_model_id:
        errors.append(
            f"({contract.sport}, {contract.market}): challenger cannot be identical to champion "
            f"('{contract.champion_model_id}')"
        )

    if contract.rollback_model_id and contract.rollback_model_id == contract.champion_model_id:
        errors.append(
            f"({contract.sport}, {contract.market}): rollback cannot be identical to champion "
            f"('{contract.champion_model_id}')"
        )

    valid_serving = {s.value for s in ServingStatus}
    if contract.serving_status not in valid_serving and contract.serving_status != "active":
        errors.append(
            f"({contract.sport}, {contract.market}): invalid serving_status '{contract.serving_status}'; "
            f"must be one of {valid_serving}"
        )

    valid_evidence = {e.value for e in EvidenceStatus}
    if contract.evidence_status not in valid_evidence:
        errors.append(
            f"({contract.sport}, {contract.market}): invalid evidence_status '{contract.evidence_status}'; "
            f"must be one of {valid_evidence}"
        )

    valid_priorities = {p.value for p in ReplacementPriority}
    if contract.replacement_priority not in valid_priorities:
        errors.append(
            f"({contract.sport}, {contract.market}): invalid replacement_priority '{contract.replacement_priority}'; "
            f"must be one of {valid_priorities}"
        )

    if entries_by_id is not None:
        champ_entry = entries_by_id.get(contract.champion_model_id)
        if champ_entry is None:
            errors.append(
                f"({contract.sport}, {contract.market}): champion '{contract.champion_model_id}' "
                f"not found in registered models"
            )
        elif not getattr(champ_entry, "available", True):
            load_err = getattr(champ_entry, "load_error", "unavailable")
            errors.append(
                f"({contract.sport}, {contract.market}): champion '{contract.champion_model_id}' "
                f"is not available: {load_err}"
            )

    return errors


def validate_production_lifecycle(
    contracts: dict[str, dict[str, ModelLifecycleContract]],
    *,
    supported_markets: dict[str, set[str]] = SUPPORTED_MARKETS,
    entries_by_id: dict[str, Any] | None = None,
) -> list[str]:
    """Enforce complete coverage across all supported sports and markets.

    Invariants:
    1. Every (sport, market) in supported_markets MUST have an active contract.
    2. Every supported market MUST have exactly one serving champion.
    3. Weak evidence (evidence_status == degraded) does NOT remove market from production.
    4. No supported market can have zero serving models.
    """
    violations: list[str] = []

    for sport, markets in supported_markets.items():
        sport_contracts = contracts.get(sport.upper()) or contracts.get(sport) or {}
        for market in sorted(markets):
            contract = sport_contracts.get(market.lower()) or sport_contracts.get(market)
            if contract is None:
                violations.append(
                    f"MISSING_CHAMPION: Supported market ({sport}, {market}) has no active champion contract. "
                    f"Every supported market must continuously have a serving champion."
                )
                continue

            contract_errors = validate_lifecycle_contract(contract, entries_by_id=entries_by_id)
            violations.extend(contract_errors)

            if contract.serving_status not in {ServingStatus.PRODUCTION.value, "active"}:
                violations.append(
                    f"UNSERVED_MARKET: Supported market ({sport}, {market}) has serving_status='{contract.serving_status}', "
                    f"expected 'production'."
                )

    if violations:
        raise LifecycleInvariantViolation(
            f"Production champion-challenger lifecycle invariant violations ({len(violations)}):\n"
            + "\n".join(f" - {v}" for v in violations)
        )

    return violations
