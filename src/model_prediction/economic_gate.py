"""Predictive + economic promotion gates (model_improvements.md section 2).

A feature or model variant may only be promoted from research to real units
if it clears BOTH gates:

- **Predictive gate**: proper-scoring-rule quality (Brier score, log loss,
  calibration) -- see ``calibration.calibration_metrics()``.
- **Economic gate**: profitability at executable prices after costs, with
  uncertainty quantified (CLV, ROI, max drawdown, bootstrap CI) -- this
  module.

Neither gate alone is sufficient: a model can be well-calibrated and still
lose money after costs, or show a P&L number without honest probabilities
behind it. This module does not compute ROI/CLV itself -- the ledger and
validation pipelines already do that from timestamp-valid executable prices
-- it only judges whether the already-measured economics clear the bar.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DrawdownResult:
    max_drawdown_units: float
    peak_units: float
    trough_units: float
    peak_index: int
    trough_index: int


def max_drawdown(pnl_sequence: Sequence[float]) -> DrawdownResult:
    """Largest peak-to-trough decline in cumulative P&L.

    ``pnl_sequence`` must already be in chronological (settlement/event date)
    order -- drawdown computed on a shuffled sequence is meaningless.
    """
    if not pnl_sequence:
        return DrawdownResult(0.0, 0.0, 0.0, -1, -1)
    cumulative = 0.0
    peak = 0.0
    peak_index = 0
    worst = 0.0
    worst_peak_index = 0
    worst_trough_index = 0
    for index, pnl in enumerate(pnl_sequence):
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
            peak_index = index
        decline = peak - cumulative
        if decline > worst:
            worst = decline
            worst_peak_index = peak_index
            worst_trough_index = index
    return DrawdownResult(
        max_drawdown_units=round(worst, 6),
        peak_units=round(peak, 6),
        trough_units=round(peak - worst, 6),
        peak_index=worst_peak_index,
        trough_index=worst_trough_index,
    )


def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = lambda xs: sum(xs) / len(xs),
    *,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int | None = 0,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for a statistic over ``values``.

    Each resample draws ``len(values)`` items with replacement from
    ``values``. Per model_improvements.md section 11 ("bootstrap by event
    date or week, not by treating correlated games/features as fully
    independent"): callers should pass one value PER DATE/WEEK (e.g.
    already-aggregated daily P&L), not one value per individual game, when
    games on the same date are correlated.
    """
    if not values:
        raise ValueError("cannot bootstrap an empty sample")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    rng = random.Random(seed)
    n = len(values)
    samples = []
    for _ in range(n_resamples):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        samples.append(statistic(resample))
    samples.sort()
    tail = (1 - confidence) / 2
    lower = samples[int(tail * n_resamples)]
    upper = samples[min(n_resamples - 1, int((1 - tail) * n_resamples))]
    return round(lower, 6), round(upper, 6)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EconomicGateThresholds:
    """Config-sourceable defaults for economic_gate/promotion_gate.

    Neither gate function is wired into a live promotion decision yet (see
    module docstring) -- this dataclass exists so that when one is, the
    thresholds come from config/model.yaml's ``economic_gate`` section
    (config.economic_gate_thresholds) instead of being hand-edited literals
    at whatever call site eventually invokes them.
    """

    minimum_calls: int = 50
    minimum_roi: float = 0.0
    require_positive_clv: bool = True
    minimum_predictive_sample: int = 30


def economic_gate(
    *,
    calls: int,
    roi: float | None,
    mean_clv: float | None,
    pnl_sequence: Sequence[float] = (),
    minimum_calls: int = 50,
    minimum_roi: float = 0.0,
    maximum_drawdown_units: float | None = None,
    require_positive_clv: bool = True,
    daily_pnl_for_bootstrap: Sequence[float] | None = None,
) -> GateResult:
    """Section 2's economic gate.

    Callers are responsible for having computed ``roi``/``mean_clv``/
    ``pnl_sequence`` from timestamp-valid executable asks -- this function
    only judges whether the already-measured economics clear the bar, it
    does not validate price provenance itself.
    """
    reasons: list[str] = []
    if calls < minimum_calls:
        reasons.append(f"only {calls} calls, below the {minimum_calls}-call minimum")
    if roi is None:
        reasons.append("no ROI computed (no settled qualified calls)")
    elif roi < minimum_roi:
        reasons.append(f"ROI {roi:.4f} is below the {minimum_roi:.4f} minimum")
    if require_positive_clv:
        if mean_clv is None:
            reasons.append("no CLV computed")
        elif mean_clv <= 0:
            reasons.append(f"mean CLV {mean_clv:.4f} is not positive")
    drawdown = max_drawdown(pnl_sequence) if pnl_sequence else None
    if (
        maximum_drawdown_units is not None
        and drawdown is not None
        and drawdown.max_drawdown_units > maximum_drawdown_units
    ):
            reasons.append(
                f"max drawdown {drawdown.max_drawdown_units:.2f}U exceeds the "
                f"{maximum_drawdown_units:.2f}U limit"
            )
    roi_ci = None
    if daily_pnl_for_bootstrap:
        roi_ci = bootstrap_ci(list(daily_pnl_for_bootstrap))
        if roi_ci[1] < 0:
            reasons.append(f"bootstrap 95% CI for daily P&L {roi_ci} does not exclude a loss")
    metrics: dict[str, object] = {
        "calls": calls,
        "roi": roi,
        "mean_clv": mean_clv,
        "max_drawdown_units": drawdown.max_drawdown_units if drawdown else None,
        "drawdown_detail": vars(drawdown) if drawdown else None,
        "bootstrap_daily_pnl_ci": roi_ci,
    }
    return GateResult(passed=not reasons, reasons=reasons, metrics=metrics)


def promotion_gate(
    *,
    predictive_metrics: dict[str, object],
    economic: GateResult,
    minimum_predictive_sample: int = 30,
) -> GateResult:
    """Section 2's promotion rule: promote only if BOTH gates pass.

    ``predictive_metrics`` is the output of
    ``calibration.calibration_metrics()``.
    """
    reasons = list(economic.reasons)
    if predictive_metrics.get("status") != "ok":
        reasons.append(f"predictive gate not evaluable: {predictive_metrics.get('status', 'unknown')}")
    else:
        raw_sample_size = predictive_metrics.get("sample_size", 0)
        sample_size = raw_sample_size if isinstance(raw_sample_size, int) else 0
        if sample_size < minimum_predictive_sample:
            reasons.append(
                f"predictive sample size {sample_size} below the {minimum_predictive_sample} minimum"
            )
    return GateResult(
        passed=not reasons,
        reasons=reasons,
        metrics={"predictive": predictive_metrics, **economic.metrics},
    )
