"""Dota 2 Objective Priority & Map Geography Model for Polymarket US.

Quantitative Architecture:
1. Radiant vs Dire Map Geography:
   Tracks inherent Radiant map advantages (easier Roshan pathing / jungle camps ~52.5% baseline win rate).
2. Objective Dynamics:
   - Expected Net Worth Differential at 15 min (NW@15)
   - First Roshan conversion rate (~78% series win correlation)
   - Tormentor & First Barracks conversion
3. Series Formats & Polymarket Settlement:
   - Bo1 (Tiebreakers)
   - Bo2 (Group Stages: 2-0 Win, 1-1 Tie, 0-2 Loss; in Polymarket moneyline, ties settle to 0.50)
   - Bo3 (Main Stage)
   - Bo5 (Grand Finals)
4. Polymarket US Binary Share Output:
   Directly outputs contract probabilities for Match Winner, Game 1, and First Roshan.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RADIANT_SIDE_ADVANTAGE = 0.025  # +2.5% win rate boost for Radiant


@dataclass(slots=True)
class Dota2TeamProfile:
    """Point-in-time Dota 2 team profile."""

    team_id: str
    team_name: str
    overall_rating: float = 1500.0
    avg_nw15_diff: float = 0.0  # Average Net Worth differential at 15 min
    first_roshan_rate: float = 0.50
    tier: str = "Tier1"  # "Tier1", "Tier2", "Tier3"


@dataclass(slots=True)
class Dota2MatchForecast:
    """Full Dota 2 match forecast for Polymarket US binary contracts."""

    team_radiant: str
    team_dire: str
    format: str  # "Bo1", "Bo2", "Bo3", or "Bo5"
    p_game_1_radiant: float
    p_series_radiant: float
    p_series_dire: float
    p_tie_bo2: float  # Only for Bo2 group stages
    expected_payout_radiant: float  # For Polymarket 0.50 tie rule
    expected_payout_dire: float
    p_first_roshan_radiant: float


class Dota2Engine:
    """Evaluates objective-driven win probabilities for competitive Dota 2."""

    def __init__(self, radiant_boost: float = DEFAULT_RADIANT_SIDE_ADVANTAGE) -> None:
        self.radiant_boost = radiant_boost

    def evaluate_game_probability(
        self,
        team_radiant: Dota2TeamProfile,
        team_dire: Dota2TeamProfile,
    ) -> float:
        """Evaluate single game win probability for Radiant team."""
        r_rad = team_radiant.overall_rating
        r_dire = team_dire.overall_rating

        # Baseline Elo logistic probability
        p_base = 1.0 / (1.0 + 10.0 ** ((r_dire - r_rad) / 400.0))

        # Radiant map boost
        p_with_side = p_base + self.radiant_boost

        # NW@15 adjustment (+1500g diff ~ +4.5% win probability)
        nw15_diff = team_radiant.avg_nw15_diff - team_dire.avg_nw15_diff
        p_with_nw = p_with_side + (nw15_diff / 1500.0) * 0.045

        return max(0.05, min(0.95, float(p_with_nw)))

    def forecast_series(
        self,
        team_radiant: Dota2TeamProfile,
        team_dire: Dota2TeamProfile,
        match_format: str = "Bo3",
    ) -> Dota2MatchForecast:
        """Forecast complete series outcomes for Polymarket US binary contracts."""
        p_g1_rad = self.evaluate_game_probability(team_radiant, team_dire)

        # In alternating side series:
        p_g_alt = self.evaluate_game_probability(team_dire, team_radiant)
        p_g2_rad = 1.0 - p_g_alt  # Team Radiant on Dire in Game 2

        p_avg = 0.5 * (p_g1_rad + p_g2_rad)
        q_avg = 1.0 - p_avg

        fmt = match_format.upper()
        p_tie_bo2 = 0.0

        if fmt == "BO1":
            p_series_rad = p_g1_rad
            p_series_dire = 1.0 - p_series_rad
        elif fmt == "BO2":
            # Group stage 2-game series:
            # 2-0 Radiant: p1 * p2
            # 1-1 Tie: p1 * (1-p2) + (1-p1) * p2
            # 0-2 Dire: (1-p1) * (1-p2)
            p_2_0_rad = p_g1_rad * p_g2_rad
            p_tie_bo2 = p_g1_rad * (1.0 - p_g2_rad) + (1.0 - p_g1_rad) * p_g2_rad
            p_2_0_dire = (1.0 - p_g1_rad) * (1.0 - p_g2_rad)
            p_series_rad = p_2_0_rad
            p_series_dire = p_2_0_dire
        elif fmt == "BO5":
            p_series_rad = p_avg**3 * (1.0 + 3.0 * q_avg + 6.0 * q_avg * q_avg)
            p_series_dire = 1.0 - p_series_rad
        else:
            # Standard Bo3
            p_series_rad = p_avg**2 * (3.0 - 2.0 * p_avg)
            p_series_dire = 1.0 - p_series_rad

        # Polymarket Expected Payout:
        # In Bo2, Polymarket settles ties to 0.50
        ev_rad = p_series_rad + 0.5 * p_tie_bo2
        ev_dire = p_series_dire + 0.5 * p_tie_bo2

        # First Roshan probability
        frosh_rad = 0.50 + 0.5 * (team_radiant.first_roshan_rate - team_dire.first_roshan_rate) + 0.03

        return Dota2MatchForecast(
            team_radiant=team_radiant.team_name,
            team_dire=team_dire.team_name,
            format=match_format,
            p_game_1_radiant=round(p_g1_rad, 4),
            p_series_radiant=round(p_series_rad, 4),
            p_series_dire=round(p_series_dire, 4),
            p_tie_bo2=round(p_tie_bo2, 4),
            expected_payout_radiant=round(ev_rad, 4),
            expected_payout_dire=round(ev_dire, 4),
            p_first_roshan_radiant=round(max(0.10, min(0.90, frosh_rad)), 4),
        )
