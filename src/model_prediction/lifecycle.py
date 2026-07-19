from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .domain import ModelOrigin, ModelState


QUALIFICATION_MINIMUM_HIT_RATE = 0.60
QUALIFICATION_MINIMUM_CALLS = 50


@dataclass(frozen=True)
class QualificationDecision:
    """Accuracy-first model qualification on one untouched holdout.

    Brier/calibration and any price-based economics are reported alongside the
    decision, but they cannot promote or reject a model version.
    """

    qualified: bool
    locked_holdout: bool
    calls: int
    hits: int
    hit_rate: float | None
    minimum_calls: int
    minimum_hit_rate: float
    total_predictions: int
    selectivity: float | None
    failures: tuple[str, ...]
    brier_score: float | None
    calibration: dict[str, Any] | None
    secondary_reporting_complete: bool
    roi: float | None
    price_diagnostics: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_locked_holdout(
    *,
    calls: int,
    hits: int,
    total_predictions: int,
    locked_holdout: bool,
    brier_score: float | None = None,
    calibration: dict[str, Any] | None = None,
    roi: float | None = None,
    price_diagnostics: dict[str, Any] | None = None,
    minimum_calls: int = QUALIFICATION_MINIMUM_CALLS,
    minimum_hit_rate: float = QUALIFICATION_MINIMUM_HIT_RATE,
) -> QualificationDecision:
    """Apply the base model-qualification gates: locked, 50+ calls, 60%+ hit.

    ROI and price diagnostics are deliberately accepted only for reporting.
    They have no role in the qualification decision.
    """
    if min(calls, hits, total_predictions) < 0:
        raise ValueError("holdout counts cannot be negative")
    if hits > calls:
        raise ValueError("holdout hits cannot exceed calls")
    if calls > total_predictions:
        raise ValueError("holdout calls cannot exceed total predictions")
    if minimum_calls < 1:
        raise ValueError("minimum_calls must be positive")
    if not 0.5 < minimum_hit_rate <= 1:
        raise ValueError("minimum_hit_rate must be in (0.5, 1]")

    hit_rate = hits / calls if calls else None
    selectivity = calls / total_predictions if total_predictions else None
    failures: list[str] = []
    if not locked_holdout:
        failures.append("evaluation is not a locked holdout")
    if calls < minimum_calls:
        failures.append(f"selective calls {calls} below required {minimum_calls}")
    if hit_rate is None or hit_rate < minimum_hit_rate - 1e-12:
        rendered = "unavailable" if hit_rate is None else f"{hit_rate:.2%}"
        failures.append(f"selective hit rate {rendered} below required {minimum_hit_rate:.2%}")

    return QualificationDecision(
        qualified=not failures,
        locked_holdout=locked_holdout,
        calls=calls,
        hits=hits,
        hit_rate=round(hit_rate, 6) if hit_rate is not None else None,
        minimum_calls=minimum_calls,
        minimum_hit_rate=minimum_hit_rate,
        total_predictions=total_predictions,
        selectivity=round(selectivity, 6) if selectivity is not None else None,
        failures=tuple(failures),
        brier_score=brier_score,
        calibration=calibration,
        secondary_reporting_complete=brier_score is not None and calibration is not None,
        roi=roi,
        price_diagnostics=price_diagnostics,
    )


ALLOWED_TRANSITIONS: dict[ModelState, set[ModelState]] = {
    ModelState.RESEARCH: {ModelState.SHADOW_CANDIDATE, ModelState.RETIRED},
    ModelState.SHADOW_CANDIDATE: {ModelState.SHADOW_QUALIFIED, ModelState.RESEARCH, ModelState.RETIRED},
    ModelState.SHADOW_QUALIFIED: {ModelState.DEGRADED, ModelState.RETIRED},
    ModelState.DEGRADED: {ModelState.SHADOW_QUALIFIED, ModelState.SUSPENDED, ModelState.RETIRED},
    ModelState.SUSPENDED: {ModelState.RESEARCH, ModelState.RETIRED},
    ModelState.RETIRED: set(),
}


def validate_transition(current: ModelState, target: ModelState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid model-state transition: {current.value} -> {target.value}")


def can_create_qualified_call(state: ModelState, origin: ModelOrigin) -> bool:
    return state is ModelState.SHADOW_QUALIFIED and origin is ModelOrigin.STATISTICAL_MODEL


def can_create_observation(state: ModelState) -> bool:
    return state is not ModelState.RETIRED
