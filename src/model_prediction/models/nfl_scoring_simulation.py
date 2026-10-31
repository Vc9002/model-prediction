"""Bivariate Discrete Scoring Simulation Engine for NFL Spread & Totals.

Models discrete football scoring distributions by decomposing team scoring into
Poisson-distributed Touchdowns (with empirical PAT distribution 6/7/8 pts) and Field Goals (3 pts).
Produces exact joint score probabilities, eliminating continuous Gaussian CDF error
around NFL key margins (3, 7, 6, 10, 14, 4) and totals (41, 44, 47, 51, 37, 40).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BivariateScoringResult:
    prob_home_cover: float
    prob_away_cover: float
    prob_spread_push: float
    prob_over: float
    prob_under: float
    prob_total_push: float
    prob_home_win: float
    prob_away_win: float
    prob_tie: float
    exp_home_pts: float
    exp_away_pts: float


def compute_discrete_team_score_dist(
    exp_pts: float,
    rz_td_rate: float = 0.60,
    max_pts: int = 56,
) -> list[float]:
    """Compute exact discrete scoring distribution P(Score = s) for s in [0..max_pts]."""
    clamped_pts = max(3.0, min(60.0, exp_pts))
    clamped_rz = max(0.20, min(0.85, rz_td_rate))

    # Mean expected TDs (avg value 6.95 pts) and FGs (3.0 pts)
    lam_td = max(0.15, (clamped_pts * clamped_rz) / 6.95)
    lam_fg = max(0.10, (clamped_pts * (1.0 - clamped_rz)) / 3.0)

    # Poisson probabilities up to 8 TDs and 7 FGs
    p_td = [math.exp(-lam_td) * (lam_td**k) / math.factorial(k) for k in range(9)]
    p_fg = [math.exp(-lam_fg) * (lam_fg**k) / math.factorial(k) for k in range(8)]

    score_probs = [0.0] * (max_pts + 1)
    for n_td, p_t in enumerate(p_td):
        for n_fg, p_f in enumerate(p_fg):
            prob_combo = p_t * p_f
            if n_td == 0:
                s = 3 * n_fg
                if s <= max_pts:
                    score_probs[s] += prob_combo
            else:
                # TD scoring outcomes: 7 pts (90%), 6 pts (6%), 8 pts (4%)
                s7 = 7 * n_td + 3 * n_fg
                s6 = 6 * n_td + 3 * n_fg
                s8 = 8 * n_td + 3 * n_fg
                if s7 <= max_pts:
                    score_probs[s7] += prob_combo * 0.90
                if s6 <= max_pts:
                    score_probs[s6] += prob_combo * 0.06
                if s8 <= max_pts:
                    score_probs[s8] += prob_combo * 0.04

    total_mass = sum(score_probs)
    if total_mass > 0.0:
        return [p / total_mass for p in score_probs]
    return [1.0 / (max_pts + 1)] * (max_pts + 1)


def solve_bivariate_scoring(
    exp_home_pts: float,
    exp_away_pts: float,
    home_rz_td_rate: float = 0.60,
    away_rz_td_rate: float = 0.60,
    spread_home_line: float = 0.0,
    total_line: float = 44.0,
    max_pts: int = 56,
) -> BivariateScoringResult:
    """Solve exact bivariate discrete scoring grid for NFL spread, total, and moneyline probabilities."""
    h_dist = compute_discrete_team_score_dist(exp_home_pts, home_rz_td_rate, max_pts=max_pts)
    a_dist = compute_discrete_team_score_dist(exp_away_pts, away_rz_td_rate, max_pts=max_pts)

    p_home_cover, p_away_cover, p_sp_push = 0.0, 0.0, 0.0
    p_over, p_under, p_tot_push = 0.0, 0.0, 0.0
    p_home_win, p_away_win, p_tie = 0.0, 0.0, 0.0

    cutoff = -spread_home_line

    for h, ph in enumerate(h_dist):
        if ph == 0.0:
            continue
        for a, pa in enumerate(a_dist):
            if pa == 0.0:
                continue
            prob = ph * pa
            margin = h - a
            tot = h + a

            # Spread grading (from home perspective)
            if margin > cutoff:
                p_home_cover += prob
            elif margin < cutoff:
                p_away_cover += prob
            else:
                p_sp_push += prob

            # Total grading
            if tot > total_line:
                p_over += prob
            elif tot < total_line:
                p_under += prob
            else:
                p_tot_push += prob

            # Outright Moneyline
            if h > a:
                p_home_win += prob
            elif a > h:
                p_away_win += prob
            else:
                p_tie += prob

    return BivariateScoringResult(
        prob_home_cover=round(p_home_cover, 6),
        prob_away_cover=round(p_away_cover, 6),
        prob_spread_push=round(p_sp_push, 6),
        prob_over=round(p_over, 6),
        prob_under=round(p_under, 6),
        prob_total_push=round(p_tot_push, 6),
        prob_home_win=round(p_home_win + 0.5 * p_tie, 6),
        prob_away_win=round(p_away_win + 0.5 * p_tie, 6),
        prob_tie=round(p_tie, 6),
        exp_home_pts=exp_home_pts,
        exp_away_pts=exp_away_pts,
    )
