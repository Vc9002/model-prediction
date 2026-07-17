"""Confidence-based call/no-call gate — the PRIMARY pick filter.

Every model prediction passes through this gate before becoming a pick.
The model only bets when confident. "No call" is the default.

Thresholds must come from a hash-verified learned artifact or explicit config.
There are no sport defaults: silently falling back to a convenient constant
would invalidate the locked-holdout contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.learned_market import learn_confidence_threshold


@dataclass(frozen=True)
class ConfidenceGate:
    """Call/no-call decision for a single prediction."""

    call: bool
    confidence: float
    threshold: float
    sport: str
    reason: str

    @property
    def is_call(self) -> bool:
        return self.call


def evaluate(
    confidence: float,
    sport: str,
    threshold: float | None = None,
    override_thresholds: dict[str, float] | None = None,
) -> ConfidenceGate:
    """Evaluate whether a model prediction meets the confidence threshold.

    Args:
        confidence: Model's confidence score (max predicted probability, 0-1).
        sport: Sport key (mlb, nba, wnba, nfl, soccer, tennis).
        threshold: Override sport-specific threshold. If None, uses default.
        override_thresholds: Override the entire threshold map.

    Returns:
        ConfidenceGate with call/no-call decision and reasoning.
    """
    sport_key = sport.lower()
    configured = (override_thresholds or {}).get(sport_key)
    threshold_value = threshold if threshold is not None else configured
    if threshold_value is None:
        raise ValueError(f"no learned confidence threshold configured for {sport_key}")
    if not 0.5 <= threshold_value < 1:
        raise ValueError("confidence threshold must be between 0.5 and 1")

    if confidence >= threshold_value:
        return ConfidenceGate(
            call=True,
            confidence=round(confidence, 4),
            threshold=threshold_value,
            sport=sport_key,
            reason=f"confidence_{confidence:.3f}_above_threshold_{threshold_value}",
        )
    return ConfidenceGate(
        call=False,
        confidence=round(confidence, 4),
        threshold=threshold_value,
        sport=sport_key,
        reason=f"confidence_{confidence:.3f}_below_threshold_{threshold_value}",
    )


def learn_threshold(
    predictions: list[dict[str, Any]],
    target_hit_rate: float = 0.60,
    min_calls: int = 50,
) -> tuple[float, dict[str, Any]]:
    """Learn the optimal confidence threshold from historical predictions.

    Sorts predictions by confidence descending. Finds the lowest threshold
    where called-pick hit rate ≥ target_hit_rate and ≥ min_calls are made.

    Args:
        predictions: List of dicts with 'confidence' (float) and 'correct' (bool).
        target_hit_rate: Minimum acceptable called-pick hit rate (default 0.60).
        min_calls: Minimum number of calls required (default 20).

    Returns:
        (optimal_threshold, stats_dict) with hit_rate, calls, units, selectivity.
    """
    probabilities = [float(item["confidence"]) for item in predictions]
    outcomes = [int(bool(item["correct"])) for item in predictions]
    threshold, stats = learn_confidence_threshold(
        probabilities,
        outcomes,
        target_hit_rate=target_hit_rate,
        minimum_calls=min_calls,
    )
    called = [item for item in predictions if float(item["confidence"]) >= threshold]
    hits = sum(bool(item["correct"]) for item in called)
    stats.update(
        {
            "hit_rate": round(hits / len(called), 6),
            "calls": len(called),
            "units_at_minus_110": round(hits * (10 / 11) - (len(called) - hits), 6),
        }
    )
    return threshold, stats
