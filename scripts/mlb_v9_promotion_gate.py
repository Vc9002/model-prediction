"""Formal 4-Gate Promotion Decision Evaluator (Roadmap Phase 23-24).

Enforces the four non-negotiable gates before MLB v9 can replace v8:
  1. Predictive Gate: Proper scores improved with 80%+ bootstrap confidence
  2. Operational Gate: Clean feature serving without missingness crashes
  3. Prospective Gate: Untouched live prospective sample requirement
  4. Economic Gate: Market-executable decision performance and non-negative CLV
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    passed: bool
    details: dict[str, Any]
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PromotionEvaluation:
    model_id: str
    incumbent_id: str
    overall_verdict: str  # 'PROMOTION_CANDIDATE', 'CONTINUE_SHADOW', 'REJECT', 'INCONCLUSIVE'
    predictive_gate: GateResult
    operational_gate: GateResult
    prospective_gate: GateResult
    economic_gate: GateResult


def evaluate_promotion_gates(
    model_id: str,
    paired_shadow_metrics: dict[str, Any],
    operational_metrics: dict[str, Any],
    economic_metrics: dict[str, Any],
    min_prospective_games: int = 200,
    min_prospective_dates: int = 30,
) -> PromotionEvaluation:
    """Evaluate candidate against all four governance gates."""
    # 1. Predictive Gate
    delta_ll = paired_shadow_metrics.get("delta_log_loss", 0.0)
    delta_br = paired_shadow_metrics.get("delta_brier", 0.0)
    p_better = paired_shadow_metrics.get("p_log_loss_better", 0.5)
    pred_pass = bool(delta_ll < 0 and delta_br <= 0 and p_better >= 0.80)
    pred_gate = GateResult(
        name="predictive_gate",
        passed=pred_pass,
        details=paired_shadow_metrics,
        failure_reason=None
        if pred_pass
        else f"Insufficient predictive edge (delta_ll={delta_ll}, p_better={p_better})",
    )

    # 2. Operational Gate
    cov = operational_metrics.get("serving_coverage", 0.0)
    latency = operational_metrics.get("latency_ms", 0.0)
    op_pass = bool(cov >= 0.95 and latency < 500.0)
    op_gate = GateResult(
        name="operational_gate",
        passed=op_pass,
        details=operational_metrics,
        failure_reason=None
        if op_pass
        else f"Operational standards failed (coverage={cov}, latency={latency}ms)",
    )

    # 3. Prospective Gate
    settled_n = paired_shadow_metrics.get("settled_games", 0)
    dates_n = paired_shadow_metrics.get("unique_dates", 0)
    prosp_pass = bool(settled_n >= min_prospective_games and dates_n >= min_prospective_dates)
    prosp_gate = GateResult(
        name="prospective_gate",
        passed=prosp_pass,
        details={"settled_games": settled_n, "unique_dates": dates_n, "target_games": min_prospective_games},
        failure_reason=None
        if prosp_pass
        else f"Underpowered prospective sample ({settled_n}/{min_prospective_games} games, {dates_n}/{min_prospective_dates} dates)",
    )

    # 4. Economic Gate
    clv = economic_metrics.get("rolling_clv_pp", 0.0)
    roi = economic_metrics.get("executable_roi", 0.0)
    econ_pass = bool(clv >= 0.0 and roi >= 0.0)
    econ_gate = GateResult(
        name="economic_gate",
        passed=econ_pass,
        details=economic_metrics,
        failure_reason=None if econ_pass else f"Economic drag detected (clv={clv}pp, roi={roi})",
    )

    # Overall Verdict
    if pred_pass and op_pass and prosp_pass and econ_pass:
        verdict = "PROMOTION_CANDIDATE"
    elif not op_pass or (delta_ll > 0.005 and p_better < 0.20):
        verdict = "REJECT"
    elif not prosp_pass:
        verdict = "CONTINUE_SHADOW"
    else:
        verdict = "INCONCLUSIVE"

    return PromotionEvaluation(
        model_id=model_id,
        incumbent_id="mlb-elo-trend-lr-v8",
        overall_verdict=verdict,
        predictive_gate=pred_gate,
        operational_gate=op_gate,
        prospective_gate=prosp_gate,
        economic_gate=econ_gate,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate MLB v9 promotion gate status")
    parser.add_argument("--model-id", default="mlb-v9-lr-l2-shrinkage")
    args = parser.parse_args()

    # Evaluation against current state
    mock_shadow = {
        "settled_games": 32,
        "unique_dates": 1,
        "delta_log_loss": -0.0013,
        "p_log_loss_better": 0.842,
        "delta_brier": -0.0006,
        "p_brier_better": 0.842,
    }
    mock_op = {"serving_coverage": 0.985, "latency_ms": 85.0}
    mock_econ = {"rolling_clv_pp": 1.4, "executable_roi": 0.086}

    eval_result = evaluate_promotion_gates(args.model_id, mock_shadow, mock_op, mock_econ)
    print("=== MLB PROMOTION GATE EVALUATION ===")
    print(f"Model Candidate : {eval_result.model_id}")
    print(f"Champion Baseline: {eval_result.incumbent_id} (FROZEN)")
    print(f"Overall Verdict : {eval_result.overall_verdict}")
    print("\nGates Breakdown:")
    for g in [
        eval_result.predictive_gate,
        eval_result.operational_gate,
        eval_result.prospective_gate,
        eval_result.economic_gate,
    ]:
        status = "PASSED [OK]" if g.passed else f"PENDING/FAILED: {g.failure_reason}"
        print(f"  - {g.name:<20s}: {status}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
