"""Missingness handling and player-rate shrinkage.

Every feature carries: observed value, availability flag, source, observation age,
missing reason, conflict count, sample size, uncertainty.

Player-rate shrinkage: beta-binomial for binary stats (clean rates), empirical Bayes
for continuous metrics. Never silently fill a required value with zero or league average.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ── Missingness ──────────────────────────────────────────────────────────────


@dataclass
class FeatureRecord:
    """One feature value with full missingness metadata."""
    name: str
    observed_value: float | None
    available: bool = True
    source: str = ""
    observed_at_utc: str = ""
    observation_age_hours: float = 0.0
    missing_reason: str = ""
    conflict_count: int = 0
    sample_size: int = 1
    uncertainty: float = 0.0
    prior_mean: float = 0.0
    posterior_mean: float | None = None
    posterior_variance: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "value": self.observed_value,
            "available": self.available, "source": self.source,
            "observation_age_hours": self.observation_age_hours,
            "missing_reason": self.missing_reason,
            "sample_size": self.sample_size, "uncertainty": self.uncertainty,
        }


@dataclass
class MissingnessReport:
    """Coverage report for one feature group across a dataset."""
    feature_group: str
    total_rows: int
    available_rows: int
    missing_rows: int
    missing_reasons: dict[str, int] = field(default_factory=dict)
    mean_age_hours: float = 0.0
    mean_uncertainty: float = 0.0
    conflict_rate: float = 0.0

    @property
    def coverage(self) -> float:
        return self.available_rows / max(1, self.total_rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_group": self.feature_group,
            "total_rows": self.total_rows,
            "available_rows": self.available_rows,
            "missing_rows": self.missing_rows,
            "coverage": self.coverage,
            "missing_reasons": self.missing_reasons,
            "mean_age_hours": self.mean_age_hours,
            "mean_uncertainty": self.mean_uncertainty,
            "conflict_rate": self.conflict_rate,
        }


def compute_missingness_report(
    feature_group: str, records: list[FeatureRecord],
) -> MissingnessReport:
    """Aggregate missingness statistics for a feature group."""
    total = len(records)
    available = [r for r in records if r.available]
    missing = [r for r in records if not r.available]
    reasons: dict[str, int] = {}
    for r in missing:
        if r.missing_reason:
            reasons[r.missing_reason] = reasons.get(r.missing_reason, 0) + 1
    avg_age = sum(r.observation_age_hours for r in available) / max(1, len(available))
    avg_unc = sum(r.uncertainty for r in available) / max(1, len(available))
    conflicts = sum(1 for r in records if r.conflict_count > 0) / max(1, total)
    return MissingnessReport(
        feature_group=feature_group, total_rows=total,
        available_rows=len(available), missing_rows=len(missing),
        missing_reasons=reasons, mean_age_hours=avg_age,
        mean_uncertainty=avg_unc, conflict_rate=conflicts,
    )


# ── Player-rate shrinkage ────────────────────────────────────────────────────


@dataclass
class ShrunkRate:
    """Beta-binomial posterior for a binary rate statistic."""
    player_id: str
    stat_name: str
    numerator: float      # successes
    denominator: float    # opportunities
    raw_rate: float        # numerator / denominator
    prior_alpha: float
    prior_beta: float
    posterior_mean: float  # (α + successes) / (α + β + opportunities)
    posterior_variance: float
    effective_sample: float  # α + β

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id, "stat_name": self.stat_name,
            "numerator": self.numerator, "denominator": self.denominator,
            "raw_rate": self.raw_rate, "posterior_mean": self.posterior_mean,
            "posterior_variance": self.posterior_variance,
            "effective_sample": self.effective_sample,
        }


def beta_binomial_shrink(
    player_id: str,
    stat_name: str,
    successes: float,
    opportunities: float,
    prior_alpha: float = 2.0,
    prior_beta: float = 2.0,
) -> ShrunkRate:
    """Shrink a binary rate toward a Beta(α, β) prior.

    posterior_mean = (α + successes) / (α + β + opportunities)
    posterior_variance = (α+s)(β+opp-s) / ((α+β+opp)² * (α+β+opp+1))

    Used for: pitcher clean rates, first-inning scoreless rates, etc.
    """
    if opportunities <= 0:
        return ShrunkRate(player_id, stat_name, successes, opportunities, 0.0,
                          prior_alpha, prior_beta, prior_alpha / (prior_alpha + prior_beta),
                          0.0, prior_alpha + prior_beta)
    alpha_post = prior_alpha + successes
    beta_post = prior_beta + opportunities - successes
    total = alpha_post + beta_post
    mean = alpha_post / total
    var = (alpha_post * beta_post) / (total ** 2 * (total + 1))
    return ShrunkRate(player_id, stat_name, successes, opportunities,
                      successes / opportunities, prior_alpha, prior_beta,
                      float(mean), float(var), prior_alpha + prior_beta)


# MLB-specific clean rates
PITCHER_CLEAN_RATES = [
    "first_inning_clean",
    "scoreless_inning",
    "clean_appearance",
    "rolling_10_clean",
    "rolling_20_clean",
]


def pitcher_clean_rate_shrink(
    player_id: str,
    rate_name: str,
    successes: float,
    opportunities: float,
    league_prior_alpha: float = 5.0,
    league_prior_beta: float = 5.0,
) -> ShrunkRate:
    """Shrink a pitcher clean rate with league-informed priors."""
    return beta_binomial_shrink(
        player_id, rate_name, successes, opportunities,
        prior_alpha=league_prior_alpha, prior_beta=league_prior_beta,
    )


def empirical_bayes_shrink(
    values: list[float],
    std_errors: list[float],
) -> tuple[float, list[float]]:
    """Simple empirical Bayes shrinkage for continuous metrics.

    Returns (prior_variance, shrunk_values) where each value is shrunk
    toward the grand mean proportional to its standard error.
    """
    arr = np.array(values)
    se = np.array(std_errors)
    grand_mean = float(np.mean(arr))
    total_var = float(np.var(arr))
    avg_se2 = float(np.mean(se ** 2))
    prior_var = max(0.0, total_var - avg_se2)
    shrunk = []
    for v, s in zip(values, std_errors, strict=True):
        weight = prior_var / (prior_var + s ** 2) if prior_var > 0 else 0.0
        # weight = trust in the observation (high weight = precise, keep value)
        # (1 - weight) = trust in the prior (grand mean)
        shrunk.append(float(weight * v + (1 - weight) * grand_mean))
    return prior_var, shrunk
