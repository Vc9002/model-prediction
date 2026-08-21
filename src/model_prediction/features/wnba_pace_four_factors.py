"""WNBA Pace and Four Factors feature engineering.

Dean Oliver Four Factors adapted for WNBA 40-minute regulation games:
1. eFG% (Effective Field Goal Percentage): (FGM + 0.5 * 3PM) / FGA
2. TOV% (Turnover Percentage): TOV / (FGA + 0.44 * FTA + TOV)
3. OREB% (Offensive Rebound Percentage): OREB / (OREB + OppDREB)
4. FTR (Free Throw Rate): FTA / FGA
5. Pace: Possessions per 40 minutes with Bayesian shrinkage to league mean (~78-80 possessions/40m).

Point-in-time calculation from historical boxscores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# WNBA League Priors (empirical baselines for 40-minute regulation games)
LEAGUE_PACE_40M = 79.5
LEAGUE_EFG = 0.490
LEAGUE_TOV_RATE = 0.165
LEAGUE_OREB_RATE = 0.260
LEAGUE_FTR = 0.220
LEAGUE_OFF_RATING = 100.0  # Points per 100 possessions (~79.5 pace -> ~79.5 pts per game)

SHRINKAGE_GAMES_PRIOR = 8.0  # Empirical Bayes shrinkage constant


@dataclass(frozen=True)
class TeamFourFactors:
    team: str
    games_played: int
    pace_40m: float
    efg_pct: float
    tov_pct: float
    oreb_pct: float
    ft_rate: float
    off_rating: float  # pts per 100 poss
    def_rating: float  # pts allowed per 100 poss
    projected_points_per_game: float


def compute_team_four_factors(
    team: str,
    recent_game_logs: list[dict[str, Any]],
    *,
    lookback_games: int = 15,
) -> TeamFourFactors:
    """Compute point-in-time credibility-shrunk Four Factors for a WNBA team."""
    logs = recent_game_logs[-lookback_games:]
    n = len(logs)
    if n == 0:
        return TeamFourFactors(
            team=team,
            games_played=0,
            pace_40m=LEAGUE_PACE_40M,
            efg_pct=LEAGUE_EFG,
            tov_pct=LEAGUE_TOV_RATE,
            oreb_pct=LEAGUE_OREB_RATE,
            ft_rate=LEAGUE_FTR,
            off_rating=LEAGUE_OFF_RATING,
            def_rating=LEAGUE_OFF_RATING,
            projected_points_per_game=round(LEAGUE_PACE_40M * (LEAGUE_OFF_RATING / 100.0), 1),
        )

    # Accumulate raw statistics
    total_poss = 0.0
    total_pts = 0.0
    total_opp_pts = 0.0
    total_fgm = 0.0
    total_fga = 0.0
    total_3pm = 0.0
    total_fta = 0.0
    total_tov = 0.0
    total_oreb = 0.0
    total_opp_dreb = 0.0

    for g in logs:
        pts = float(g.get("points") or 0.0)
        opp_pts = float(g.get("opp_points") or 0.0)
        fgm = float(g.get("fgm") or 0.0)
        fga = float(g.get("fga") or 0.0)
        tpm = float(g.get("fg3m") or g.get("3pm") or 0.0)
        fta = float(g.get("fta") or 0.0)
        tov = float(g.get("turnovers") or g.get("tov") or 0.0)
        oreb = float(g.get("oreb") or 0.0)
        opp_dreb = float(g.get("opp_dreb") or 25.0)

        # Estimate possessions
        poss = fga + 0.44 * fta - oreb + tov
        total_poss += max(50.0, poss)
        total_pts += pts
        total_opp_pts += opp_pts
        total_fgm += fgm
        total_fga += fga
        total_3pm += tpm
        total_fta += fta
        total_tov += tov
        total_oreb += oreb
        total_opp_dreb += opp_dreb

    raw_pace = total_poss / n
    raw_efg = (total_fgm + 0.5 * total_3pm) / max(1.0, total_fga) if total_fga > 0 else LEAGUE_EFG
    raw_tov = total_tov / max(1.0, total_fga + 0.44 * total_fta + total_tov)
    raw_oreb = total_oreb / max(1.0, total_oreb + total_opp_dreb)
    raw_ftr = total_fta / max(1.0, total_fga) if total_fga > 0 else LEAGUE_FTR
    raw_off_rtg = (total_pts / max(1.0, total_poss)) * 100.0
    raw_def_rtg = (total_opp_pts / max(1.0, total_poss)) * 100.0

    # Empirical Bayes Credibility Weight
    c = n / (n + SHRINKAGE_GAMES_PRIOR)

    pace = c * raw_pace + (1.0 - c) * LEAGUE_PACE_40M
    efg = c * raw_efg + (1.0 - c) * LEAGUE_EFG
    tov_pct = c * raw_tov + (1.0 - c) * LEAGUE_TOV_RATE
    oreb_pct = c * raw_oreb + (1.0 - c) * LEAGUE_OREB_RATE
    ftr = c * raw_ftr + (1.0 - c) * LEAGUE_FTR
    off_rtg = c * raw_off_rtg + (1.0 - c) * LEAGUE_OFF_RATING
    def_rtg = c * raw_def_rtg + (1.0 - c) * LEAGUE_OFF_RATING

    proj_ppg = pace * (off_rtg / 100.0)

    return TeamFourFactors(
        team=team,
        games_played=n,
        pace_40m=round(pace, 2),
        efg_pct=round(efg, 4),
        tov_pct=round(tov_pct, 4),
        oreb_pct=round(oreb_pct, 4),
        ft_rate=round(ftr, 4),
        off_rating=round(off_rtg, 2),
        def_rating=round(def_rtg, 2),
        projected_points_per_game=round(proj_ppg, 2),
    )


def project_wnba_game_total(
    home_factors: TeamFourFactors,
    away_factors: TeamFourFactors,
) -> dict[str, float]:
    """Project game pace, home/away points, and combined total from Four Factors."""
    # Projected game pace is harmonic/geometric blend of both teams pace vs league
    game_pace = (home_factors.pace_40m + away_factors.pace_40m) / 2.0

    # Home points: Home Offense vs Away Defense + Home court advantage (~1.5 pts)
    home_efficiency = (home_factors.off_rating + away_factors.def_rating) / 2.0
    home_pts = game_pace * (home_efficiency / 100.0) + 1.5

    # Away points: Away Offense vs Home Defense
    away_efficiency = (away_factors.off_rating + home_factors.def_rating) / 2.0
    away_pts = game_pace * (away_efficiency / 100.0) - 1.5

    projected_total = home_pts + away_pts
    projected_margin = home_pts - away_pts

    return {
        "game_pace": round(game_pace, 2),
        "home_points": round(home_pts, 2),
        "away_points": round(away_pts, 2),
        "projected_total": round(projected_total, 2),
        "projected_margin": round(projected_margin, 2),
    }
