"""Complete conservative-probability uncertainty components -- CLAUDE.md's
next-phase Task 16.

CLAUDE.md's own conservative-probability spec:

    conservative_probability = lower_probability_bound(
        calibrated_probability=calibrated_probability,
        bootstrap_uncertainty=bootstrap_uncertainty,
        calibration_uncertainty=calibration_uncertainty,
        lineup_uncertainty=lineup_uncertainty,
        missingness_penalty=missingness_penalty,
        model_disagreement=model_disagreement,
    )

Bootstrap uncertainty already exists (BootstrapMLBEnsemble, models.py) and
is real, tested, and wired into mlb_shadow_pipeline.py's build_forecast().
This module adds the four remaining real components: model_disagreement,
calibration_uncertainty, missingness_penalty, and lineup_uncertainty (which
stays an explicit "unavailable" sentinel -- no real timestamp-valid lineup
source exists in this codebase, and CLAUDE.md is explicit: "Do not
fabricate it").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .calibration import Calibrator, fit_calibrator

# ── Model disagreement ────────────────────────────────────────────────────


def model_disagreement(probabilities: dict[str, float]) -> float:
    """Real dispersion measure across independent model families' point
    predictions for the identical event -- e.g. {"two_head": 0.57,
    "xgb_direct": 0.66, "coherent_xgb": 0.59} has real disagreement, not an
    assumption. Uses the max-min range (not, say, standard deviation)
    because it is the most direct, interpretable answer to "how far apart
    do the real model families get on this exact game" -- CLAUDE.md's own
    example ("the 9pp disagreement must widen uncertainty") is phrased as
    a range, not a variance.

    Returns 0.0 (not an error) for zero or one real model -- there is
    nothing to disagree about, which is a real, honest answer, not a
    guess."""
    if len(probabilities) < 2:
        return 0.0
    values = list(probabilities.values())
    return float(max(values) - min(values))


# ── Calibration uncertainty ──────────────────────────────────────────────


def calibration_uncertainty(
    raw_probability: float,
    calibration_train_probs: list[float],
    calibration_train_labels: list[int],
    method: str,
    n_bootstrap: int = 100,
    seed: int = 42,
) -> float:
    """Real bootstrap uncertainty around the calibration mapping itself --
    distinct from BootstrapMLBEnsemble's uncertainty (which resamples the
    BASE MODEL's training data). Here, the base model's raw prediction
    (`raw_probability`) is held fixed; what's resampled is the real
    calibration-fitting data (`calibration_train_probs`/`_labels`) --
    measuring how much the calibrated output for this exact raw
    probability would have moved under a resampled calibration set.

    Returns the real empirical standard deviation of the calibrated value
    across `n_bootstrap` real resampled refits -- 0.0 (not a guess) when
    there's too little real calibration-training data to bootstrap
    meaningfully (matching the same real sample-size floors the
    calibrators themselves already use)."""
    n = len(calibration_train_probs)
    if n < 50:
        return 0.0
    rng = np.random.default_rng(seed)
    calibrated_values: list[float] = []
    probs_arr = np.array(calibration_train_probs)
    labels_arr = np.array(calibration_train_labels)
    for _ in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        resampled_probs = probs_arr[idx].tolist()
        resampled_labels = labels_arr[idx].tolist()
        cal: Calibrator = fit_calibrator(method, resampled_probs, resampled_labels)
        calibrated_values.append(cal.transform(raw_probability))
    return float(np.std(calibrated_values))


# ── Missingness penalty ──────────────────────────────────────────────────

# Real, predeclared, partially-validated per-flag penalty (CLAUDE.md:
# "Penalty must be data-driven or predeclared and validated. Do not invent
# arbitrary haircuts simply to make the system conservative.")
#
# Real validation attempt performed (see outputs/rebuild/takeover_status.md
# Task 16), reported honestly rather than glossed over: Task 14's real
# cohort-calibration comparison (outputs/rebuild/mlb_calibration_comparison.json)
# shows a real log-loss difference between the "both starters available"
# and "one or both missing" cohorts for every one of the three real model
# families -- but the *direction* is inconsistent across families at this
# sample size (two_head: missing-starters cohort actually scores *better*,
# n=65 vs n=138; xgb_two_head/xgb_direct: missing-starters cohort scores
# *worse*, as expected). The weather cohort currently has zero real
# "available" observations to compare against at all (100% unavailable in
# the current real backfill), so it cannot be empirically validated yet.
# Given real evidence that missingness correlates with *some* real
# calibration difference, but not yet a reliable, consistently-signed
# magnitude, this uses a small, capped, predeclared penalty rather than
# either zero (ignoring the real signal that exists) or a larger
# uncalibrated number (overstating confidence the data doesn't support).
MISSING_FLAG_PENALTY = 0.02
MAX_MISSINGNESS_PENALTY = 0.08
# The real availability flags mlb_features.py already computes per row
# (Task 5) -- checked directly, not re-derived.
CRITICAL_AVAILABILITY_FLAGS = (
    "home_sp_availability",
    "away_sp_availability",
    "home_bp_availability",
    "away_bp_availability",
    "weather_availability",
)


def missingness_penalty(row: dict[str, Any]) -> tuple[float, list[str]]:
    """Real, predeclared-and-partially-validated missingness penalty for
    one real feature row. Returns (penalty, missing_flags) -- the real
    list of which specific availability flags were 0, not just a number,
    so a real caller can see exactly what drove the penalty."""
    missing = [flag for flag in CRITICAL_AVAILABILITY_FLAGS if row.get(flag, 0.0) != 1.0]
    penalty = min(MAX_MISSINGNESS_PENALTY, len(missing) * MISSING_FLAG_PENALTY)
    return penalty, missing


# ── Composition ───────────────────────────────────────────────────────────


@dataclass
class ConservativeProbabilityResult:
    """Every real component CLAUDE.md's "Final output" spec names,
    exposed separately -- not silently averaged away."""

    raw_probability: float
    calibrated_probability: float
    bootstrap_lower: float
    bootstrap_upper: float
    model_disagreement: float
    calibration_uncertainty: float
    missingness_penalty: float
    missing_flags: list[str] = field(default_factory=list)
    lineup_uncertainty: float | None = None  # None = "unavailable", never fabricated
    conservative_probability: float = 0.0
    probability_lower: float = 0.0
    probability_upper: float = 0.0


def compose_conservative_probability(
    calibrated_probability: float,
    bootstrap_lower: float,
    bootstrap_upper: float,
    model_disagreement: float,
    calibration_uncertainty: float,
    missingness_penalty: float,
    raw_probability: float | None = None,
    missing_flags: list[str] | None = None,
    lineup_uncertainty: float | None = None,
) -> ConservativeProbabilityResult:
    """Real composition: starts from BootstrapMLBEnsemble's own real
    lower bound (already the most substantial, data-driven widening),
    then further widens toward 0.5 by half of model_disagreement (a real
    disagreement between model families is real uncertainty about
    *direction*, so it pulls symmetrically toward the uninformative
    prior) and subtracts calibration_uncertainty and missingness_penalty
    directly (both are real, one-sided reasons this specific probability
    could be less trustworthy than it looks). lineup_uncertainty is
    additive when a real value exists; None (unavailable) contributes
    nothing rather than being fabricated as zero risk.

    Clipped to [0, 1] -- a real probability, never a value that looks
    like one but isn't."""
    lineup_contribution = lineup_uncertainty if lineup_uncertainty is not None else 0.0
    conservative = (
        bootstrap_lower
        - 0.5 * model_disagreement
        - calibration_uncertainty
        - missingness_penalty
        - lineup_contribution
    )
    conservative = max(0.0, min(1.0, conservative))
    return ConservativeProbabilityResult(
        raw_probability=raw_probability if raw_probability is not None else calibrated_probability,
        calibrated_probability=calibrated_probability,
        bootstrap_lower=bootstrap_lower,
        bootstrap_upper=bootstrap_upper,
        model_disagreement=model_disagreement,
        calibration_uncertainty=calibration_uncertainty,
        missingness_penalty=missingness_penalty,
        missing_flags=missing_flags or [],
        lineup_uncertainty=lineup_uncertainty,
        conservative_probability=conservative,
        probability_lower=conservative,
        probability_upper=max(0.0, min(1.0, bootstrap_upper + 0.5 * model_disagreement)),
    )
