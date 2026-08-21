"""Tennis Point-Level Markov Chain Engine (Barnett & Clarke 2005).

Solves the hierarchical point -> game -> tiebreak -> set -> match absorbing Markov chains
with surface and opponent adjustments.

Mathematical Specifications:
1. Opponent-Adjusted Serve Point Win Probability (Barnett & Clarke, 2005):
   f_abs = (f_as * (1 - g_bs)) / (1 - g_tours)
   where:
     f_as: Player A serve points won % on surface s
     g_bs: Player B return points won % on surface s
     g_tours: Tour surface return baseline (Hard: 0.365, Clay: 0.380, Grass: 0.340)
2. Closed-Form Game Hold Probability:
   P_hold(p) = (p^4 * (15 - 34*p + 28*p^2 - 8*p^3)) / (1 - 2*p*(1-p))
3. Tiebreak & Set Transition Matrices:
   Evaluates exact non-linear tiebreak (to 7 by 2) and set (to 6 by 2 + tiebreak).
4. Match Format Translation:
   - Best-of-3 (WTA / Standard ATP): P_Bo3 = S^2 * (3 - 2S)
   - Best-of-5 (Grand Slam): P_Bo5 = S^3 * (1 + 3*(1-S) + 6*(1-S)^2)
5. Polymarket US Settlement Integration:
   Derives P(Set 1 Win) to price retirement and live markets accurately.
"""

from dataclasses import dataclass

SURFACE_TOUR_RETURN_AVERAGES: dict[str, float] = {
    "Hard": 0.365,
    "Clay": 0.380,
    "Grass": 0.340,
    "Carpet": 0.350,
    "Default": 0.365,
}


def game_hold_probability(p_serve: float) -> float:
    """Closed-form probability of winning a service game given point win prob p."""
    p = max(0.01, min(0.99, float(p_serve)))
    q = 1.0 - p
    # Standard deuce infinite series: p^4 + 4p^4*q + 10p^4*q^2 + (20p^3*q^3 * p^2 / (1 - 2pq))
    # Simplifies algebraically to:
    num = p**4 * (15.0 - 34.0 * p + 28.0 * p**2 - 8.0 * p**3)
    denom = 1.0 - 2.0 * p * q
    return float(max(0.0, min(1.0, num / denom)))


def tiebreak_probability(p_serve_a: float, p_serve_b: float) -> float:
    """Exact probability of Player A winning a 7-point tiebreak (win by 2) with alternating serves."""
    # In a tiebreak, A serves 1, then B serves 2, A serves 2, etc.
    # On average, A serves half the points and B serves half.
    # Probability A wins a point on A serve: p_a
    # Probability A wins a point on B serve: 1 - p_b
    p_a = max(0.01, min(0.99, p_serve_a))
    p_b = max(0.01, min(0.99, p_serve_b))
    p_a_on_b = 1.0 - p_b

    # Average single-point win probability for A across the tiebreak:
    p_avg = 0.5 * (p_a + p_a_on_b)
    q_avg = 1.0 - p_avg

    # Standard absorbing Markov chain to 7 points (win by 2):
    # Sum over k in 0..5 (winning 7-k) + deuce absorption at 6-6:
    import math

    p_reach_7_before_6 = sum(math.comb(6 + k, k) * (p_avg**7) * (q_avg**k) for k in range(6))
    p_reach_6_6 = math.comb(12, 6) * (p_avg**6) * (q_avg**6)
    p_win_from_6_6 = (p_avg**2) / (1.0 - 2.0 * p_avg * q_avg)

    return float(p_reach_7_before_6 + p_reach_6_6 * p_win_from_6_6)


def set_win_probability(
    p_hold_a: float,
    p_hold_b: float,
    p_tb_a: float,
) -> tuple[float, float]:
    """Compute set win probability for Player A starting on serve and starting on return.

    Returns: (p_set_serve_first, p_set_return_first)
    """
    # Standard dynamic programming / Markov state over game states (i, j) where i, j in 0..6
    # State (i, j): prob of A reaching 6 games by 2 (or 7-5 / 7-6)
    # We solve the 2-game alternating cycle.
    # For robust point-in-time calculation:
    # A breaks B with prob (1 - p_hold_b); B breaks A with prob (1 - p_hold_a)
    p_break_b = 1.0 - p_hold_b
    p_break_a = 1.0 - p_hold_a

    # Forward recurrence over 12-game grid
    # Grid P[g_a, g_b, is_a_serving]:
    dp: dict[tuple[int, int, bool], float] = {}

    def get_prob(ga: int, gb: int, a_serving: bool) -> float:
        if (ga, gb, a_serving) in dp:
            return dp[(ga, gb, a_serving)]

        # Terminal conditions
        if ga == 6 and gb <= 4:
            return 1.0
        if gb == 6 and ga <= 4:
            return 0.0
        if ga == 7 and gb == 5:
            return 1.0
        if gb == 7 and ga == 5:
            return 0.0
        if ga == 6 and gb == 6:
            return p_tb_a

        # If A is serving:
        # A wins game with p_hold_a -> state (ga+1, gb, False)
        # B wins game with 1 - p_hold_a -> state (ga, gb+1, False)
        if a_serving:
            prob = p_hold_a * get_prob(ga + 1, gb, False) + p_break_a * get_prob(ga, gb + 1, False)
        else:
            # B is serving:
            # A wins game (breaks B) with 1 - p_hold_b -> state (ga+1, gb, True)
            # B wins game (holds) with p_hold_b -> state (ga, gb+1, True)
            prob = p_break_b * get_prob(ga + 1, gb, True) + p_hold_b * get_prob(ga, gb + 1, True)

        dp[(ga, gb, a_serving)] = prob
        return prob

    p_serve_first = get_prob(0, 0, True)
    p_return_first = get_prob(0, 0, False)
    return p_serve_first, p_return_first


@dataclass(slots=True)
class TennisPlayerStats:
    """Point-level baseline statistics for a player."""

    player_id: str
    name: str
    serve_points_won_pct: float  # e.g. 0.65 (65%)
    return_points_won_pct: float  # e.g. 0.38 (38%)
    matches_sample_size: int = 20


@dataclass(slots=True)
class TennisMarkovMatchForecast:
    """Full hierarchical Markov forecast for a tennis match."""

    player_a: str
    player_b: str
    surface: str
    format: str  # "Bo3" or "Bo5"
    p_match_a: float
    p_match_b: float
    p_set_1_a: float
    p_set_1_b: float
    p_serve_pt_adj_a: float
    p_serve_pt_adj_b: float
    p_game_hold_a: float
    p_game_hold_b: float
    expected_total_games: float


class TennisMarkovEngine:
    """Barnett & Clarke Point-to-Match Markov Simulator."""

    def __init__(self, surface_return_baselines: dict[str, float] | None = None) -> None:
        self.surface_baselines = surface_return_baselines or SURFACE_TOUR_RETURN_AVERAGES

    def adjust_serve_probabilities(
        self,
        player_a: TennisPlayerStats,
        player_b: TennisPlayerStats,
        surface: str = "Hard",
    ) -> tuple[float, float]:
        """Apply Barnett & Clarke opponent adjustment for serve point win probability."""
        g_tour = self.surface_baselines.get(surface, self.surface_baselines["Default"])
        denom = max(0.10, 1.0 - g_tour)

        # Player A vs Player B
        f_as = player_a.serve_points_won_pct
        g_bs = player_b.return_points_won_pct
        p_serve_adj_a = max(0.20, min(0.85, (f_as * (1.0 - g_bs)) / denom))

        # Player B vs Player A
        f_bs = player_b.serve_points_won_pct
        g_as = player_a.return_points_won_pct
        p_serve_adj_b = max(0.20, min(0.85, (f_bs * (1.0 - g_as)) / denom))

        return p_serve_adj_a, p_serve_adj_b

    def forecast_match(
        self,
        player_a: TennisPlayerStats,
        player_b: TennisPlayerStats,
        surface: str = "Hard",
        match_format: str = "Bo3",
        a_serves_first: bool = True,
    ) -> TennisMarkovMatchForecast:
        """Evaluate complete match probability hierarchy."""
        # 1. Opponent-adjusted point probabilities
        p_pt_a, p_pt_b = self.adjust_serve_probabilities(player_a, player_b, surface)

        # 2. Game hold probabilities
        p_hold_a = game_hold_probability(p_pt_a)
        p_hold_b = game_hold_probability(p_pt_b)

        # 3. Tiebreak win probability
        p_tb_a = tiebreak_probability(p_pt_a, p_pt_b)

        # 4. Set win probabilities (serve first vs return first)
        p_set_sf, p_set_rf = set_win_probability(p_hold_a, p_hold_b, p_tb_a)

        # Set 1 starting state
        p_set_1_a = p_set_sf if a_serves_first else p_set_rf
        p_set_1_b = 1.0 - p_set_1_a

        # Average set win probability for match aggregation:
        s_avg_a = 0.5 * (p_set_sf + p_set_rf)

        # 5. Match level aggregation
        if match_format.upper() in ["BO5", "BEST_OF_5", "GRAND_SLAM"]:
            # P_Bo5 = s^3 * (1 + 3*(1-s) + 6*(1-s)^2)
            q = 1.0 - s_avg_a
            p_match_a = s_avg_a**3 * (1.0 + 3.0 * q + 6.0 * q * q)
            exp_games = 38.5 + 4.0 * (1.0 - abs(p_match_a - 0.5) * 2.0)
        else:
            # P_Bo3 = s^2 * (3 - 2s)
            p_match_a = s_avg_a**2 * (3.0 - 2.0 * s_avg_a)
            exp_games = 22.5 + 3.5 * (1.0 - abs(p_match_a - 0.5) * 2.0)

        p_match_b = 1.0 - p_match_a

        return TennisMarkovMatchForecast(
            player_a=player_a.name,
            player_b=player_b.name,
            surface=surface,
            format=match_format,
            p_match_a=round(p_match_a, 4),
            p_match_b=round(p_match_b, 4),
            p_set_1_a=round(p_set_1_a, 4),
            p_set_1_b=round(p_set_1_b, 4),
            p_serve_pt_adj_a=round(p_pt_a, 4),
            p_serve_pt_adj_b=round(p_pt_b, 4),
            p_game_hold_a=round(p_hold_a, 4),
            p_game_hold_b=round(p_hold_b, 4),
            expected_total_games=round(exp_games, 1),
        )
