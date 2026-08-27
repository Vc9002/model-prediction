"""MLB v9 Dual-Horizon Architecture (Roadmap Phase 16).

Implements side-by-side early vs late decision horizons:
  1. Early Horizon (T-6h to T-3h): Projected offense priors + PIT starter + bullpen fatigue
  2. Late Horizon (T-45m): Confirmed lineup + confirmed starter + live weather
  3. Value of Lineup Confirmation: Score(Early) - Score(Late)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MLBHorizonForecast:
    """Probabilistic forecast generated for a specific decision horizon."""

    horizon: str  # 'early_projected' or 'late_confirmed'
    event_id: str
    home_team: str
    away_team: str
    home_win_probability: float
    projected_total_runs: float
    lineup_source: str  # 'projected_priors' or 'confirmed_pregame'
    feature_count: int
    observed_at_utc: str


@dataclass(frozen=True, slots=True)
class HorizonComparison:
    """Side-by-side comparison between early projected and late confirmed calls."""

    event_id: str
    early_prob: float
    late_prob: float
    prob_shift_pp: float  # late - early in percentage points
    early_total: float
    late_total: float
    total_shift_runs: float  # late - early runs
    lineup_shift_significance: str  # 'negligible', 'moderate', 'major'


def compare_horizons(early: MLBHorizonForecast, late: MLBHorizonForecast) -> HorizonComparison:
    """Compute delta metrics between early projected and late confirmed forecasts."""
    prob_shift = round((late.home_win_probability - early.home_win_probability) * 100.0, 2)
    total_shift = round(late.projected_total_runs - early.projected_total_runs, 2)

    abs_prob = abs(prob_shift)
    if abs_prob >= 5.0:
        sig = "major"
    elif abs_prob >= 2.0:
        sig = "moderate"
    else:
        sig = "negligible"

    return HorizonComparison(
        event_id=early.event_id,
        early_prob=early.home_win_probability,
        late_prob=late.home_win_probability,
        prob_shift_pp=prob_shift,
        early_total=early.projected_total_runs,
        late_total=late.projected_total_runs,
        total_shift_runs=total_shift,
        lineup_shift_significance=sig,
    )
