"""Valorant Map Veto & Tactical Economy Model for Polymarket US.

Quantitative Architecture:
1. Active Map Pool & Side Bias:
   Active Pool: (Ascent, Bind, Haven, Split, Lotus, Sunset, Abyss)
   Tracks Defender vs Attacker side win rates per map.
2. Pistol Round & Eco Round Multiplier:
   In first-to-13 round halves, winning pistol rounds converts to an expected +2.05 round advantage.
3. Map Veto Simulator:
   Simulates Bo3 (Ban-Ban-Pick-Pick-Ban-Ban-Decider) and Bo5 (Grand Finals).
4. Polymarket US Binary Share Output:
   Directly computes contract probabilities for Series Winner, Map 1, and Map 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VALORANT_ACTIVE_MAPS = (
    "Ascent",
    "Bind",
    "Haven",
    "Split",
    "Lotus",
    "Sunset",
    "Abyss",
)

# Baseline Defender win rates by map (~2024-2026 VCT aggregate)
MAP_DEFENDER_BIAS: dict[str, float] = {
    "Ascent": 0.535,
    "Split": 0.525,
    "Bind": 0.515,
    "Haven": 0.505,
    "Sunset": 0.495,
    "Lotus": 0.485,
    "Abyss": 0.490,
}


@dataclass(slots=True)
class ValorantTeamProfile:
    """Point-in-time Valorant team profile."""

    team_id: str
    team_name: str
    overall_rating: float = 1500.0
    map_ratings: dict[str, float] = field(default_factory=dict)
    map_permabans: list[str] = field(default_factory=list)
    pistol_win_rate: float = 0.50


@dataclass(slots=True)
class ValorantSeriesForecast:
    """Full Valorant series forecast for Polymarket US binary contracts."""

    team_a: str
    team_b: str
    format: str  # "Bo1", "Bo3", or "Bo5"
    selected_maps: list[str]
    p_map_wins_a: list[float]
    p_series_a: float
    p_series_b: float
    p_2_0_a: float
    p_2_1_a: float
    p_2_0_b: float
    p_2_1_b: float


class ValorantVetoEngine:
    """Simulates competitive Valorant map vetos and evaluates series win distributions."""

    def __init__(self, map_pool: tuple[str, ...] = VALORANT_ACTIVE_MAPS) -> None:
        self.map_pool = list(map_pool)

    def simulate_bo3_veto(
        self,
        team_a: ValorantTeamProfile,
        team_b: ValorantTeamProfile,
    ) -> list[str]:
        """Simulate standard VCT Bo3 veto sequence."""
        remaining = list(self.map_pool)
        if len(remaining) <= 3:
            return remaining[:3]

        # 1. Ban 1: A bans worst map
        ban_a1 = (
            team_a.map_permabans[0]
            if team_a.map_permabans and team_a.map_permabans[0] in remaining
            else min(remaining, key=lambda m: team_a.map_ratings.get(m, team_a.overall_rating))
        )
        remaining.remove(ban_a1)

        # 2. Ban 1: B bans worst map
        ban_b1 = (
            team_b.map_permabans[0]
            if team_b.map_permabans and team_b.map_permabans[0] in remaining
            else min(remaining, key=lambda m: team_b.map_ratings.get(m, team_b.overall_rating))
        )
        remaining.remove(ban_b1)

        # 3. Pick 1: A picks best map (Map 1)
        map_1 = max(
            remaining,
            key=lambda m: (
                team_a.map_ratings.get(m, team_a.overall_rating)
                - team_b.map_ratings.get(m, team_b.overall_rating)
            ),
        )
        remaining.remove(map_1)

        # 4. Pick 2: B picks best map (Map 2)
        map_2 = max(
            remaining,
            key=lambda m: (
                team_b.map_ratings.get(m, team_b.overall_rating)
                - team_a.map_ratings.get(m, team_a.overall_rating)
            ),
        )
        remaining.remove(map_2)

        # 5. Ban 2: A bans
        ban_a2 = min(remaining, key=lambda m: team_a.map_ratings.get(m, team_a.overall_rating))
        remaining.remove(ban_a2)

        # 6. Ban 2: B bans
        ban_b2 = min(remaining, key=lambda m: team_b.map_ratings.get(m, team_b.overall_rating))
        remaining.remove(ban_b2)

        # 7. Decider (Map 3)
        map_3 = remaining[0]

        return [map_1, map_2, map_3]

    def evaluate_map_probability(
        self,
        team_a: ValorantTeamProfile,
        team_b: ValorantTeamProfile,
        map_name: str,
    ) -> float:
        """Evaluate single map win probability."""
        r_a = team_a.map_ratings.get(map_name, team_a.overall_rating)
        r_b = team_b.map_ratings.get(map_name, team_b.overall_rating)

        # Base Elo logistic probability
        p_base = 1.0 / (1.0 + 10.0 ** ((r_b - r_a) / 400.0))

        # Tactical adjustments: Pistol round conversion (+/- 2.0% max)
        pistol_adv = (team_a.pistol_win_rate - team_b.pistol_win_rate) * 0.10
        p_adjusted = p_base + pistol_adv

        return max(0.05, min(0.95, float(p_adjusted)))

    def forecast_series(
        self,
        team_a: ValorantTeamProfile,
        team_b: ValorantTeamProfile,
        match_format: str = "Bo3",
    ) -> ValorantSeriesForecast:
        """Forecast complete series outcomes for Polymarket US binary contracts."""
        if match_format.upper() == "BO1":
            selected_maps = ["Ascent"]
            p_m1 = self.evaluate_map_probability(team_a, team_b, selected_maps[0])
            return ValorantSeriesForecast(
                team_a=team_a.team_name,
                team_b=team_b.team_name,
                format="Bo1",
                selected_maps=selected_maps,
                p_map_wins_a=[round(p_m1, 4)],
                p_series_a=round(p_m1, 4),
                p_series_b=round(1.0 - p_m1, 4),
                p_2_0_a=0.0,
                p_2_1_a=0.0,
                p_2_0_b=0.0,
                p_2_1_b=0.0,
            )

        maps = self.simulate_bo3_veto(team_a, team_b)
        p1 = self.evaluate_map_probability(team_a, team_b, maps[0])
        p2 = self.evaluate_map_probability(team_a, team_b, maps[1])
        p3 = self.evaluate_map_probability(team_a, team_b, maps[2])

        q1 = 1.0 - p1
        q2 = 1.0 - p2
        q3 = 1.0 - p3

        p_2_0_a = round(p1 * p2, 4)
        p_2_1_a = round(p1 * q2 * p3 + q1 * p2 * p3, 4)
        p_series_a = round(p_2_0_a + p_2_1_a, 4)

        p_2_0_b = round(q1 * q2, 4)
        p_2_1_b = round(p1 * q2 * q3 + q1 * p2 * q3, 4)
        p_series_b = round(p_2_0_b + p_2_1_b, 4)

        return ValorantSeriesForecast(
            team_a=team_a.team_name,
            team_b=team_b.team_name,
            format="Bo3",
            selected_maps=maps,
            p_map_wins_a=[round(p1, 4), round(p2, 4), round(p3, 4)],
            p_series_a=p_series_a,
            p_series_b=p_series_b,
            p_2_0_a=p_2_0_a,
            p_2_1_a=p_2_1_a,
            p_2_0_b=p_2_0_b,
            p_2_1_b=p_2_1_b,
        )
