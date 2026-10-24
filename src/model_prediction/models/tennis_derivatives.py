"""Exact Markov chain game-set-match probability engine for tennis derivatives.

Calculates hold-game probabilities, tiebreak distributions, discrete 14-score
set distributions, and full match game margin (spread) and total game mass functions
for Best-of-3 and Best-of-5 tennis matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

# Fitted on completed singles matches (ATP 2021-2026 via TennisMyLife; WTA 2024-2026
# via ESPN linescores) with the pre-match Elo probability fixed as the match-win
# target; fit on the early period, validated on the later one (ATP: predicted mean
# total 23.9 vs 24.0 actual out of sample, WTA 22.6 vs 22.1). ``base`` is the
# effective serve-point level (below raw serve-point-won rates because iid points
# overstate game counts) and ``FORM_SIGMA`` the per-match serve-level spread that
# produces realistic blowout/tight-match mixes.
TENNIS_GAMES_VERSION = "tennis-games-markov-v1"
TENNIS_GAMES_V2_VERSION = "tennis-games-markov-v2"
TENNIS_GAMES_V3_VERSION = "tennis-games-markov-v3"
TENNIS_BASE_SERVE = {"ATP": 0.62, "WTA": 0.555}
TENNIS_BASE_SERVE_V1 = {"ATP": 0.62, "WTA": 0.58}
TENNIS_SURFACE_BASE_SERVE: dict[str, dict[str, float]] = {
    "Hard": {"ATP": 0.620, "WTA": 0.555},
    "Clay": {"ATP": 0.585, "WTA": 0.525},
    "Grass": {"ATP": 0.650, "WTA": 0.590},
    "Carpet": {"ATP": 0.640, "WTA": 0.580},
    "Default": {"ATP": 0.620, "WTA": 0.555},
}
TENNIS_FORM_SIGMA = 0.08
TENNIS_MOMENTUM_SHIFT = 0.035
_GAUSS_HERMITE_3 = ((-1.2247, 1.0 / 6.0), (0.0, 2.0 / 3.0), (1.2247, 1.0 / 6.0))


def hold_game_probability(p: float) -> float:
    """Exact probability of holding serve given single point win probability p."""
    p_clamped = max(0.01, min(0.99, p))
    q = 1.0 - p_clamped
    num = (p_clamped**4) * (15.0 - 34.0 * p_clamped + 28.0 * (p_clamped**2) - 8.0 * (p_clamped**3))
    den = 1.0 - 2.0 * p_clamped * q
    return num / den if den > 0 else 0.5


def _tiebreak_prob_conditional(pa: float, pb: float, a_serves_first: bool, points_to_win: int = 7) -> float:
    pa_c = max(0.01, min(0.99, pa))
    pb_c = max(0.01, min(0.99, pb))
    dp: dict[tuple[int, int], float] = {(0, 0): 1.0}

    for pt in range(100):
        new_dp: dict[tuple[int, int], float] = {}
        for (a, b), prob in dp.items():
            if (a >= points_to_win and a - b >= 2) or (b >= points_to_win and b - a >= 2):
                new_dp[(a, b)] = new_dp.get((a, b), 0.0) + prob
                continue
            # Alternating serve pattern
            server_is_a = (pt == 0) or ((pt + 1) // 2 % 2 == 1)
            if not a_serves_first:
                server_is_a = not server_is_a
            p_a_point = pa_c if server_is_a else (1.0 - pb_c)
            new_dp[(a + 1, b)] = new_dp.get((a + 1, b), 0.0) + prob * p_a_point
            new_dp[(a, b + 1)] = new_dp.get((a, b + 1), 0.0) + prob * (1.0 - p_a_point)
        dp = new_dp
        unresolved = sum(
            prob
            for (a, b), prob in dp.items()
            if not ((a >= points_to_win and a - b >= 2) or (b >= points_to_win and b - a >= 2))
        )
        if unresolved < 1e-7:
            break

    return sum(prob for (a, b), prob in dp.items() if a > b and a >= points_to_win and a - b >= 2)


def tiebreak_probability(pa: float, pb: float, points_to_win: int = 7) -> float:
    """Exact probability Player A wins a tiebreak, averaged over initial service order."""
    p_a_first = _tiebreak_prob_conditional(pa, pb, a_serves_first=True, points_to_win=points_to_win)
    p_b_first = _tiebreak_prob_conditional(pa, pb, a_serves_first=False, points_to_win=points_to_win)
    return 0.5 * (p_a_first + p_b_first)


def set_score_distribution(pa_hold: float, pb_hold: float, p_tb_a: float) -> dict[tuple[int, int], float]:
    """Computes exact distribution over the 14 set outcomes (ga, gb)."""
    dp: dict[tuple[int, int], float] = {(0, 0): 1.0}
    final_scores: dict[tuple[int, int], float] = {}

    for _ in range(13):
        new_dp: dict[tuple[int, int], float] = {}
        for (a, b), prob in dp.items():
            if (a == 6 and b <= 4) or (a == 7 and b == 5):
                final_scores[(a, b)] = final_scores.get((a, b), 0.0) + prob
                continue
            if (b == 6 and a <= 4) or (b == 7 and a == 5):
                final_scores[(a, b)] = final_scores.get((a, b), 0.0) + prob
                continue
            if a == 6 and b == 6:
                final_scores[(7, 6)] = final_scores.get((7, 6), 0.0) + prob * p_tb_a
                final_scores[(6, 7)] = final_scores.get((6, 7), 0.0) + prob * (1.0 - p_tb_a)
                continue

            server_is_a = (a + b) % 2 == 0
            p_a_win_game = pa_hold if server_is_a else (1.0 - pb_hold)
            new_dp[(a + 1, b)] = new_dp.get((a + 1, b), 0.0) + prob * p_a_win_game
            new_dp[(a, b + 1)] = new_dp.get((a, b + 1), 0.0) + prob * (1.0 - p_a_win_game)
        dp = new_dp

    return final_scores


def match_state_distribution(
    pa_point: float,
    pb_point: float,
    best_of: int = 3,
    form_sigma: float = 0.0,
    momentum_shift: float = 0.0,
) -> dict[tuple[int, int, int, int], float]:
    """Computes distribution over match states (sets_a, sets_b, total_games_a, total_games_b).

    ``form_sigma`` > 0 mixes the distribution over independent per-player serve-level
    shifts (3-point Gauss-Hermite), which fattens the blowout and tight-match tails
    that a fixed-point-probability Markov chain understates.
    ``momentum_shift`` > 0 captures empirical set-level momentum between sets.
    """
    if form_sigma > 0:
        mixed: dict[tuple[int, int, int, int], float] = {}
        for z_a, w_a in _GAUSS_HERMITE_3:
            for z_b, w_b in _GAUSS_HERMITE_3:
                shifted = match_state_distribution(
                    min(0.9, max(0.4, pa_point + form_sigma * z_a)),
                    min(0.9, max(0.4, pb_point + form_sigma * z_b)),
                    best_of,
                    form_sigma=0.0,
                    momentum_shift=momentum_shift,
                )
                for key, probability in shifted.items():
                    mixed[key] = mixed.get(key, 0.0) + probability * w_a * w_b
        return mixed
    pa_hold = hold_game_probability(pa_point)
    pb_hold = hold_game_probability(pb_point)
    p_tb_a = tiebreak_probability(pa_point, pb_point)
    dist_even = set_score_distribution(pa_hold, pb_hold, p_tb_a)

    if momentum_shift > 0.0:
        pa_lead = min(0.90, pa_point + momentum_shift)
        pb_trail = max(0.35, pb_point - momentum_shift)
        dist_a_leads = set_score_distribution(
            hold_game_probability(pa_lead),
            hold_game_probability(pb_trail),
            tiebreak_probability(pa_lead, pb_trail),
        )

        pb_lead = min(0.90, pb_point + momentum_shift)
        pa_trail = max(0.35, pa_point - momentum_shift)
        dist_b_leads = set_score_distribution(
            hold_game_probability(pa_trail),
            hold_game_probability(pb_lead),
            tiebreak_probability(pa_trail, pb_lead),
        )
    else:
        dist_a_leads = dist_even
        dist_b_leads = dist_even

    target_sets = (best_of // 2) + 1
    match_dp: dict[tuple[int, int, int, int], float] = {(0, 0, 0, 0): 1.0}
    finished_states: dict[tuple[int, int, int, int], float] = {}

    for set_idx in range(best_of):
        next_dp: dict[tuple[int, int, int, int], float] = {}
        for (sa, sb, ga, gb), m_prob in match_dp.items():
            if sa == target_sets or sb == target_sets:
                finished_states[(sa, sb, ga, gb)] = finished_states.get((sa, sb, ga, gb), 0.0) + m_prob
                continue

            if set_idx > 0 and sa > sb:
                cur_set_dist = dist_a_leads
            elif set_idx > 0 and sb > sa:
                cur_set_dist = dist_b_leads
            else:
                cur_set_dist = dist_even

            for (set_ga, set_gb), set_prob in cur_set_dist.items():
                new_sa = sa + 1 if set_ga > set_gb else sa
                new_sb = sb + 1 if set_gb > set_ga else sb
                new_ga = ga + set_ga
                new_gb = gb + set_gb
                key = (new_sa, new_sb, new_ga, new_gb)
                next_dp[key] = next_dp.get(key, 0.0) + m_prob * set_prob
        match_dp = next_dp

    for (sa, sb, ga, gb), m_prob in match_dp.items():
        finished_states[(sa, sb, ga, gb)] = finished_states.get((sa, sb, ga, gb), 0.0) + m_prob

    return finished_states


@lru_cache(maxsize=4096)
def _serve_shift(
    p_win_milli: int,
    base: float,
    best_of: int,
    form_sigma: float,
    momentum_shift: float = 0.0,
) -> float:
    target = p_win_milli / 1000.0
    low, high = -0.2, 0.2
    for _ in range(16):
        mid = (low + high) / 2.0
        states = match_state_distribution(base + mid, base - mid, best_of, form_sigma, momentum_shift)
        if sum(p for (sa, sb, _, _), p in states.items() if sa > sb) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def serve_pair_for_win_probability(
    p_win: float,
    tour: str,
    best_of: int = 3,
    surface: str = "Hard",
    form_sigma: float = TENNIS_FORM_SIGMA,
    momentum_shift: float = 0.0,
) -> tuple[float, float]:
    """Serve-point probabilities (a, b) whose Markov match-win probability for A is ``p_win``.

    Calibrated dynamically per-surface (Hard, Clay, Grass) and per-tour (ATP, WTA).
    """
    surf_key = surface if surface in TENNIS_SURFACE_BASE_SERVE else "Default"
    surf_dict = TENNIS_SURFACE_BASE_SERVE[surf_key]
    base = surf_dict.get(tour.upper(), surf_dict["ATP"])
    target = min(0.995, max(0.005, p_win))
    shift = _serve_shift(round(target * 1000), base, best_of, form_sigma, momentum_shift)
    return base + shift, base - shift


def match_game_distribution(
    pa_point: float, pb_point: float, best_of: int = 3
) -> dict[tuple[int, int], float]:
    """Computes distribution over match games won (total_games_a, total_games_b)."""
    states = match_state_distribution(pa_point, pb_point, best_of=best_of)
    finished_match_games: dict[tuple[int, int], float] = {}
    for (_, _, ga, gb), m_prob in states.items():
        finished_match_games[(ga, gb)] = finished_match_games.get((ga, gb), 0.0) + m_prob
    return finished_match_games


@dataclass(frozen=True)
class TennisDerivativePricing:
    match_win_a: float
    match_win_b: float
    expected_games_a: float
    expected_games_b: float
    expected_total_games: float
    spread_p1_cover: float | None = None
    spread_p2_cover: float | None = None
    total_over: float | None = None
    total_under: float | None = None


def price_tennis_derivatives(
    p_serve_a: float,
    p_serve_b: float,
    spread_line: float | None = None,
    total_line: float | None = None,
    best_of: int = 3,
    form_sigma: float = 0.0,
    momentum_shift: float = 0.0,
) -> TennisDerivativePricing:
    """Prices moneyline, game spread, and total games using exact Markov distribution."""
    states = match_state_distribution(
        p_serve_a, p_serve_b, best_of=best_of, form_sigma=form_sigma, momentum_shift=momentum_shift
    )

    p_win_a = sum(p for (sa, sb, ga, gb), p in states.items() if sa > sb)
    p_win_b = sum(p for (sa, sb, ga, gb), p in states.items() if sb > sa)
    exp_ga = sum(ga * p for (sa, sb, ga, gb), p in states.items())
    exp_gb = sum(gb * p for (sa, sb, ga, gb), p in states.items())
    exp_total = exp_ga + exp_gb

    p1_spread_cov = None
    p2_spread_cov = None
    if spread_line is not None:
        p1_cov = sum(p for (sa, sb, ga, gb), p in states.items() if (ga - gb) + spread_line > 0)
        p2_cov = sum(p for (sa, sb, ga, gb), p in states.items() if (ga - gb) + spread_line < 0)
        tot_sp = p1_cov + p2_cov
        if tot_sp > 0:
            p1_spread_cov = p1_cov / tot_sp
            p2_spread_cov = p2_cov / tot_sp
        else:
            p1_spread_cov = 0.5
            p2_spread_cov = 0.5

    tot_over = None
    tot_under = None
    if total_line is not None:
        p_over = sum(p for (sa, sb, ga, gb), p in states.items() if (ga + gb) > total_line)
        p_under = sum(p for (sa, sb, ga, gb), p in states.items() if (ga + gb) < total_line)
        tot_t = p_over + p_under
        if tot_t > 0:
            tot_over = p_over / tot_t
            tot_under = p_under / tot_t
        else:
            tot_over = 0.5
            tot_under = 0.5

    return TennisDerivativePricing(
        match_win_a=round(p_win_a, 6),
        match_win_b=round(p_win_b, 6),
        expected_games_a=round(exp_ga, 2),
        expected_games_b=round(exp_gb, 2),
        expected_total_games=round(exp_total, 2),
        spread_p1_cover=round(p1_spread_cov, 6) if p1_spread_cov is not None else None,
        spread_p2_cover=round(p2_spread_cov, 6) if p2_spread_cov is not None else None,
        total_over=round(tot_over, 6) if tot_over is not None else None,
        total_under=round(tot_under, 6) if tot_under is not None else None,
    )


class TennisGamesMarkovModel:
    """Serving identity of the game spread/total lane (see ``tennis_forward``)."""

    sport = "tennis"

    def __init__(self, version: str = TENNIS_GAMES_V3_VERSION) -> None:
        self.version = version


def tennis_games_model(version: str = TENNIS_GAMES_V3_VERSION) -> TennisGamesMarkovModel:
    return TennisGamesMarkovModel(version=version)
