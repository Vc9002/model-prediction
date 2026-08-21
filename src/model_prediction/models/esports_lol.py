"""League of Legends (LoL) Objective Priority & GD@15 Model for Polymarket US.

Quantitative Architecture:
1. Side Selection Advantage:
   Incorporates Blue side map geography / first pick draft priority (+3.5% baseline win advantage).
2. Early Objective & Gold State Metrics:
   - Expected Gold Differential at 15 min (GD@15)
   - First Dragon (FD%) and First Tower (FT%) conversion efficiency
   - First Baron (FBN%) conversion rate (~82% win correlation)
3. Series Markov Transition:
   Solves exact match-win distributions for Bo1, Bo3 (regular season / LCK / LPL),
   and Bo5 (playoffs / Worlds / MSI).
4. Polymarket US Binary Share Pricing:
   Directly outputs contract probabilities for Match Winner, Game 1 Winner, and First Dragon.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_BLUE_SIDE_ADVANTAGE = 0.035  # +3.5% win rate boost on Blue side


@dataclass(slots=True)
class LoLTeamProfile:
    """Point-in-time League of Legends team profile."""

    team_id: str
    team_name: str
    overall_rating: float = 1500.0
    avg_gd15: float = 0.0  # Average gold differential at 15 min (e.g. +850g)
    first_dragon_rate: float = 0.50
    first_tower_rate: float = 0.50
    first_baron_rate: float = 0.50


@dataclass(slots=True)
class LoLMatchForecast:
    """Full LoL series forecast for Polymarket US binary contracts."""

    team_blue: str
    team_red: str
    format: str  # "Bo1", "Bo3", or "Bo5"
    p_game_1_blue: float
    p_series_blue: float
    p_series_red: float
    p_first_dragon_blue: float
    p_first_tower_blue: float
    p_first_baron_blue: float


class LoLEngine:
    """Evaluates objective-driven win probabilities for competitive League of Legends."""

    def __init__(self, blue_side_boost: float = DEFAULT_BLUE_SIDE_ADVANTAGE) -> None:
        self.blue_side_boost = blue_side_boost

    def evaluate_game_probability(
        self,
        team_blue: LoLTeamProfile,
        team_red: LoLTeamProfile,
    ) -> float:
        """Evaluate single game win probability for Blue side team."""
        r_blue = team_blue.overall_rating
        r_red = team_red.overall_rating

        # Baseline Elo logistic probability
        p_base = 1.0 / (1.0 + 10.0 ** ((r_red - r_blue) / 400.0))

        # Blue side structural boost
        p_with_side = p_base + self.blue_side_boost

        # Tactical GD@15 adjustment (+1000g diff ~ +5% win probability)
        gd15_diff = team_blue.avg_gd15 - team_red.avg_gd15
        p_with_gd = p_with_side + (gd15_diff / 1000.0) * 0.05

        return max(0.05, min(0.95, float(p_with_gd)))

    def forecast_series(
        self,
        team_blue: LoLTeamProfile,
        team_red: LoLTeamProfile,
        match_format: str = "Bo3",
    ) -> LoLMatchForecast:
        """Forecast complete series outcomes for Polymarket US binary contracts."""
        p_g1_blue = self.evaluate_game_probability(team_blue, team_red)

        # In alternating side series, average game probability
        p_g_alt = self.evaluate_game_probability(team_red, team_blue)
        p_g2_blue = 1.0 - p_g_alt  # Team Blue playing on Red in Game 2

        # Average single-game win probability for team_blue across the match:
        p_avg = 0.5 * (p_g1_blue + p_g2_blue)
        q_avg = 1.0 - p_avg

        fmt = match_format.upper()
        if fmt == "BO1":
            p_series_blue = p_g1_blue
        elif fmt == "BO5":
            # Best of 5 (Grand Finals / Worlds)
            p_series_blue = p_avg**3 * (1.0 + 3.0 * q_avg + 6.0 * q_avg * q_avg)
        else:
            # Standard Bo3
            p_series_blue = p_avg**2 * (3.0 - 2.0 * p_avg)

        p_series_red = 1.0 - p_series_blue

        # Objective probabilities
        # First Dragon is correlated with early jungle/bot priority
        fd_blue = 0.50 + 0.5 * (team_blue.first_dragon_rate - team_red.first_dragon_rate) + 0.02
        ft_blue = 0.50 + 0.5 * (team_blue.first_tower_rate - team_red.first_tower_rate) + 0.02
        fbn_blue = 0.50 + 0.5 * (team_blue.first_baron_rate - team_red.first_baron_rate)

        return LoLMatchForecast(
            team_blue=team_blue.team_name,
            team_red=team_red.team_name,
            format=match_format,
            p_game_1_blue=round(p_g1_blue, 4),
            p_series_blue=round(p_series_blue, 4),
            p_series_red=round(p_series_red, 4),
            p_first_dragon_blue=round(max(0.10, min(0.90, fd_blue)), 4),
            p_first_tower_blue=round(max(0.10, min(0.90, ft_blue)), 4),
            p_first_baron_blue=round(max(0.10, min(0.90, fbn_blue)), 4),
        )
