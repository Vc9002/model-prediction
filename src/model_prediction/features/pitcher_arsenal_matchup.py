"""Pitcher Arsenal Repertoire × Hitter Vulnerability Matchup Engine (Roadmap Phase 14).

Implements point-in-time pitch-arsenal matchup scoring:
  1. Pitcher pitch-type repertoire usage distribution (FF, SL, CH, CU, FC, SI, ST)
  2. Hitter / Lineup pitch-type run values (wOBA / runs per 100 pitches) with Empirical-Bayes shrinkage
  3. Aggregate lineup matchup run expectancy vs opposing starting pitcher
  4. Sample size reliability and variance weighting
"""

from __future__ import annotations

from dataclasses import dataclass

# Standard 7 pitch classification buckets
PITCH_TYPES = ("four_seam", "slider", "changeup", "curveball", "cutter", "sinker", "sweeper")

# Default league pitch-type distribution prior
LEAGUE_USAGE_PRIORS: dict[str, float] = {
    "four_seam": 0.34,
    "slider": 0.22,
    "changeup": 0.12,
    "curveball": 0.10,
    "cutter": 0.08,
    "sinker": 0.10,
    "sweeper": 0.04,
}


@dataclass(frozen=True, slots=True)
class PitcherArsenalProfile:
    """Point-in-time pitcher repertoire distribution and pitch effectiveness."""

    pitcher_name: str
    pitch_usage: dict[str, float]  # Pitch type -> fraction of total pitches (sum = 1.0)
    pitch_whiff_rate: dict[str, float]  # Whiff% per pitch type
    total_pitches_sample: int


@dataclass(frozen=True, slots=True)
class LineupPitchMatchupProfile:
    """Opposing lineup performance vs each pitch type."""

    team: str
    run_values_per_100: dict[
        str, float
    ]  # Shrunk run values per 100 pitches (+1.5 = crusher, -1.5 = vulnerable)
    sample_pitches_seen: int


@dataclass(frozen=True, slots=True)
class ArsenalMatchupScore:
    """Composite matchup score for a starting pitcher vs opposing lineup."""

    pitcher_name: str
    opposing_team: str
    expected_run_value_per_100: (
        float  # Weighted run value (positive = hitters favored, negative = pitcher favored)
    )
    matchup_sample_strength: float  # 0.0 to 1.0 reliability factor
    primary_pitch_vulnerability: str  # The pitch type with greatest edge


def compute_arsenal_matchup(
    pitcher: PitcherArsenalProfile,
    lineup: LineupPitchMatchupProfile,
    prior_pitches_weight: float = 300.0,
) -> ArsenalMatchupScore:
    """Calculate point-in-time pitch repertoire vs lineup vulnerability matchup score."""
    # Normalize pitcher usage distribution against prior if sample is small
    p_sample = pitcher.total_pitches_sample
    usage: dict[str, float] = {}
    for pt in PITCH_TYPES:
        obs = pitcher.pitch_usage.get(pt, 0.0)
        prior = LEAGUE_USAGE_PRIORS.get(pt, 0.0)
        usage[pt] = (prior_pitches_weight * prior + p_sample * obs) / (prior_pitches_weight + p_sample)

    # Re-normalize to sum to 1.0
    u_sum = sum(usage.values()) or 1.0
    normalized_usage = {pt: u / u_sum for pt, u in usage.items()}

    # Compute expected run value per 100 pitches
    weighted_run_val = 0.0
    vulnerabilities: list[tuple[float, str]] = []
    for pt, u in normalized_usage.items():
        rv = lineup.run_values_per_100.get(pt, 0.0)
        weighted_run_val += u * rv
        vulnerabilities.append((abs(rv), pt))

    # Reliability factor
    reliability = min(1.0, (p_sample + lineup.sample_pitches_seen) / 1000.0)
    top_vuln = max(vulnerabilities, key=lambda x: x[0])[1] if vulnerabilities else "four_seam"

    return ArsenalMatchupScore(
        pitcher_name=pitcher.pitcher_name,
        opposing_team=lineup.team,
        expected_run_value_per_100=round(weighted_run_val, 3),
        matchup_sample_strength=round(reliability, 3),
        primary_pitch_vulnerability=top_vuln,
    )
