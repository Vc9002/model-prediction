"""NFL Quarterback State Vector & Offensive Line Feature Engine (Roadmap Step 26).

Implements point-in-time feature extraction for NFL:
  1. Starting Quarterback EPA/play, CPOE, P2S%, and Backup replacement penalties
  2. Offensive Line health index and adjusted pass protection composite
  3. Early-down success rates and explosive drive potentials
  4. Composite spread margin and total points projection
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NFLQuarterbackProfile:
    """Point-in-time QB state vector."""

    qb_name: str
    team: str
    is_starter: bool
    epa_per_dropback: float  # EPA per pass play (-0.25 to +0.35)
    cpoe: float  # Completion % Over Expected (-6.0% to +8.0%)
    pressure_to_sack_pct: float  # P2S% (10% elite to 28% vulnerable)
    turnover_worthy_play_pct: float  # TWP% (1.5% ball secure to 5.0% reckless)
    spread_value_pts: float  # Value over replacement backup (0.0 to +6.5 pts)
    sample_dropbacks: int


@dataclass(frozen=True, slots=True)
class NFLOffensiveLineProfile:
    """Offensive line protection and health index."""

    team: str
    pass_block_rating: float  # 0-100 scale (50 = average)
    run_block_rating: float  # 0-100 scale (50 = average)
    missing_starters: int  # 0 to 5
    adjusted_sack_rate: float  # 3.0% (elite) to 11.0% (turnstile)
    oline_penalty_pts: float  # Point adjustment (-0.5 pts per missing starter)


@dataclass(frozen=True, slots=True)
class NFLMatchupFeatures:
    """Unified 12-dimensional NFL feature vector for spreads and totals."""

    home_qb_spread_value: float
    away_qb_spread_value: float
    qb_value_gap: float  # home - away
    home_oline_rating: float
    away_oline_rating: float
    oline_protection_gap: float  # home - away
    home_early_down_success_rate: float
    away_early_down_success_rate: float
    early_down_success_gap: float
    hfa_spread_points: float  # Home field advantage (+1.8 pts modern NFL)
    projected_spread_margin: float  # Projected home margin (e.g. +4.5 = home by 4.5)
    projected_total_points: float  # Projected game total (e.g. 44.5)


LEAGUE_AVG_EPA_DROPBACK = 0.04
LEAGUE_AVG_CPOE = 0.0
LEAGUE_AVG_P2S = 0.19
LEAGUE_AVG_TOTAL = 44.0
MODERN_NFL_HFA = 1.8


def evaluate_qb_profile(
    qb_name: str,
    team: str,
    epa_per_dropback: float,
    cpoe: float,
    pressure_to_sack_pct: float,
    turnover_worthy_play_pct: float,
    sample_dropbacks: int,
    is_starter: bool = True,
) -> NFLQuarterbackProfile:
    """Compute point-in-time QB profile with Bayesian shrinkage."""
    if sample_dropbacks <= 0 or not is_starter:
        return NFLQuarterbackProfile(
            qb_name=qb_name,
            team=team,
            is_starter=False,
            epa_per_dropback=-0.12,
            cpoe=-3.5,
            pressure_to_sack_pct=0.24,
            turnover_worthy_play_pct=0.042,
            spread_value_pts=0.0,  # Baseline replacement level
            sample_dropbacks=sample_dropbacks,
        )

    # Shrink toward league average with 150 dropbacks pseudo-prior
    PRIOR_DROPBACKS = 150.0
    shrunk_epa = (PRIOR_DROPBACKS * LEAGUE_AVG_EPA_DROPBACK + sample_dropbacks * epa_per_dropback) / (
        PRIOR_DROPBACKS + sample_dropbacks
    )
    shrunk_cpoe = (PRIOR_DROPBACKS * LEAGUE_AVG_CPOE + sample_dropbacks * cpoe) / (
        PRIOR_DROPBACKS + sample_dropbacks
    )

    # Map EPA + CPOE to point spread value over replacement (0.0 to 6.5 pts)
    spread_val = max(0.5, min(6.5, 3.0 + (shrunk_epa - LEAGUE_AVG_EPA_DROPBACK) * 15.0 + (shrunk_cpoe / 3.0)))

    return NFLQuarterbackProfile(
        qb_name=qb_name,
        team=team,
        is_starter=True,
        epa_per_dropback=round(shrunk_epa, 3),
        cpoe=round(shrunk_cpoe, 2),
        pressure_to_sack_pct=round(pressure_to_sack_pct, 3),
        turnover_worthy_play_pct=round(turnover_worthy_play_pct, 3),
        spread_value_pts=round(spread_val, 2),
        sample_dropbacks=sample_dropbacks,
    )


def extract_nfl_matchup_features(
    home_qb: NFLQuarterbackProfile,
    away_qb: NFLQuarterbackProfile,
    home_oline: NFLOffensiveLineProfile,
    away_oline: NFLOffensiveLineProfile,
    home_base_success_rate: float = 0.48,
    away_base_success_rate: float = 0.48,
) -> NFLMatchupFeatures:
    """Extract point-in-time 12-D feature vector and project spread margin and total."""
    qb_gap = round(home_qb.spread_value_pts - away_qb.spread_value_pts, 2)

    # O-line pass protection adjusted for missing starters (-0.5 pts each)
    home_oline_adj = home_oline.pass_block_rating - (home_oline.missing_starters * 8.0)
    away_oline_adj = away_oline.pass_block_rating - (away_oline.missing_starters * 8.0)
    oline_gap = round(home_oline_adj - away_oline_adj, 2)
    oline_spread_impact = round((oline_gap / 20.0), 2)  # ~1.0 pt per 20 grade points

    # Early down success rate gap
    success_gap = round(home_base_success_rate - away_base_success_rate, 3)

    # Projected spread margin (positive = home favored)
    projected_margin = round(MODERN_NFL_HFA + qb_gap + oline_spread_impact + (success_gap * 10.0), 1)

    # Projected total points
    qb_scoring_factor = (home_qb.epa_per_dropback + away_qb.epa_per_dropback) * 12.0
    oline_sack_suppression = -((home_oline.adjusted_sack_rate + away_oline.adjusted_sack_rate - 0.13) * 25.0)
    projected_total = round(LEAGUE_AVG_TOTAL + qb_scoring_factor + oline_sack_suppression, 1)

    return NFLMatchupFeatures(
        home_qb_spread_value=home_qb.spread_value_pts,
        away_qb_spread_value=away_qb.spread_value_pts,
        qb_value_gap=qb_gap,
        home_oline_rating=round(home_oline_adj, 1),
        away_oline_rating=round(away_oline_adj, 1),
        oline_protection_gap=oline_gap,
        home_early_down_success_rate=home_base_success_rate,
        away_early_down_success_rate=away_base_success_rate,
        early_down_success_gap=success_gap,
        hfa_spread_points=MODERN_NFL_HFA,
        projected_spread_margin=projected_margin,
        projected_total_points=projected_total,
    )
