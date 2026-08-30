"""Sequential Probability Ratio Test (SPRT) for model evaluation and promotion.

Wald's SPRT allows early stopping when testing challenger models against incumbents
or evaluating out-of-sample edge against fixed benchmarks.

Stopping Boundaries:
    Upper bound A = ln((1 - beta) / alpha) -> ACCEPT H1 (Promotion Candidate)
    Lower bound B = ln(beta / (1 - alpha)) -> REJECT H1 (Reject Candidate)
    Between B and A                        -> CONTINUE TESTING (Need more samples)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class SPRTDecision:
    verdict: Literal["ACCEPT_H1", "REJECT_H1", "CONTINUE_TESTING"]
    log_likelihood_ratio: float
    upper_bound: float
    lower_bound: float
    n_samples: int
    alpha: float
    beta: float
    test_type: str
    details: dict[str, Any]


class BernoulliSPRT:
    """SPRT for binary outcomes (e.g. bet wins vs losses, directional hits).

    H0: p <= p0 (baseline / breakeven hit rate)
    H1: p >= p1 (target edge hit rate)
    """

    def __init__(
        self,
        p0: float = 0.50,
        p1: float = 0.55,
        alpha: float = 0.05,
        beta: float = 0.10,
    ) -> None:
        if not (0.0 < p0 < 1.0 and 0.0 < p1 < 1.0):
            raise ValueError(f"p0 and p1 must be in (0, 1), got p0={p0}, p1={p1}")
        if p0 >= p1:
            raise ValueError(f"p1 must be greater than p0, got p0={p0}, p1={p1}")
        if not (0.0 < alpha < 0.5 and 0.0 < beta < 0.5):
            raise ValueError(f"alpha and beta must be in (0, 0.5), got alpha={alpha}, beta={beta}")

        self.p0 = p0
        self.p1 = p1
        self.alpha = alpha
        self.beta = beta

        # Wald decision boundaries
        self.upper_bound = math.log((1.0 - beta) / alpha)
        self.lower_bound = math.log(beta / (1.0 - alpha))

        # Log ratios per outcome
        self.llr_win = math.log(p1 / p0)
        self.llr_loss = math.log((1.0 - p1) / (1.0 - p0))

    def evaluate(self, outcomes: list[int] | list[bool]) -> SPRTDecision:
        """Evaluate a sequence of binary outcomes (1 for win/hit, 0 for loss/miss)."""
        if not outcomes:
            return SPRTDecision(
                verdict="CONTINUE_TESTING",
                log_likelihood_ratio=0.0,
                upper_bound=round(self.upper_bound, 4),
                lower_bound=round(self.lower_bound, 4),
                n_samples=0,
                alpha=self.alpha,
                beta=self.beta,
                test_type="bernoulli",
                details={"wins": 0, "losses": 0, "win_rate": 0.0},
            )

        wins = sum(1 for x in outcomes if x)
        losses = len(outcomes) - wins
        llr = wins * self.llr_win + losses * self.llr_loss

        verdict: Literal["ACCEPT_H1", "REJECT_H1", "CONTINUE_TESTING"]
        if llr >= self.upper_bound:
            verdict = "ACCEPT_H1"
        elif llr <= self.lower_bound:
            verdict = "REJECT_H1"
        else:
            verdict = "CONTINUE_TESTING"

        return SPRTDecision(
            verdict=verdict,
            log_likelihood_ratio=round(llr, 4),
            upper_bound=round(self.upper_bound, 4),
            lower_bound=round(self.lower_bound, 4),
            n_samples=len(outcomes),
            alpha=self.alpha,
            beta=self.beta,
            test_type="bernoulli",
            details={
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / len(outcomes), 4),
                "p0": self.p0,
                "p1": self.p1,
            },
        )


class GaussianSPRT:
    """SPRT for continuous comparative metrics (e.g. delta Brier score, delta log loss, PnL per pick).

    H0: mu <= 0 (challenger is equal or worse than incumbent)
    H1: mu >= delta (challenger has true positive improvement >= delta)
    """

    def __init__(
        self,
        target_delta: float = 0.005,
        estimated_sigma: float = 0.05,
        alpha: float = 0.05,
        beta: float = 0.10,
    ) -> None:
        if target_delta <= 0:
            raise ValueError(f"target_delta must be positive, got {target_delta}")
        if estimated_sigma <= 0:
            raise ValueError(f"estimated_sigma must be positive, got {estimated_sigma}")
        if not (0.0 < alpha < 0.5 and 0.0 < beta < 0.5):
            raise ValueError(f"alpha and beta must be in (0, 0.5), got alpha={alpha}, beta={beta}")

        self.target_delta = target_delta
        self.sigma = estimated_sigma
        self.alpha = alpha
        self.beta = beta

        self.upper_bound = math.log((1.0 - beta) / alpha)
        self.lower_bound = math.log(beta / (1.0 - alpha))

    def evaluate(self, deltas: list[float]) -> SPRTDecision:
        """Evaluate a sequence of comparative improvements (positive = challenger better)."""
        if not deltas:
            return SPRTDecision(
                verdict="CONTINUE_TESTING",
                log_likelihood_ratio=0.0,
                upper_bound=round(self.upper_bound, 4),
                lower_bound=round(self.lower_bound, 4),
                n_samples=0,
                alpha=self.alpha,
                beta=self.beta,
                test_type="gaussian",
                details={"mean_delta": 0.0, "sample_std": 0.0},
            )

        n = len(deltas)
        sum_deltas = sum(deltas)
        variance = self.sigma**2

        # Log likelihood ratio under normal distribution
        llr = (self.target_delta / variance) * (sum_deltas - (n * self.target_delta / 2.0))

        verdict: Literal["ACCEPT_H1", "REJECT_H1", "CONTINUE_TESTING"]
        if llr >= self.upper_bound:
            verdict = "ACCEPT_H1"
        elif llr <= self.lower_bound:
            verdict = "REJECT_H1"
        else:
            verdict = "CONTINUE_TESTING"

        mean_val = sum_deltas / n
        std_val = math.sqrt(sum((x - mean_val) ** 2 for x in deltas) / max(1, n - 1)) if n > 1 else 0.0

        return SPRTDecision(
            verdict=verdict,
            log_likelihood_ratio=round(llr, 4),
            upper_bound=round(self.upper_bound, 4),
            lower_bound=round(self.lower_bound, 4),
            n_samples=n,
            alpha=self.alpha,
            beta=self.beta,
            test_type="gaussian",
            details={
                "mean_delta": round(mean_val, 6),
                "sample_std": round(std_val, 6),
                "target_delta": self.target_delta,
                "sigma_assumed": self.sigma,
            },
        )
