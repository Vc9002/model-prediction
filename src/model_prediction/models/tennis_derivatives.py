"""Exact Markov chain game-set-match probability engine for tennis derivatives.

Calculates hold-game probabilities, tiebreak distributions, discrete 14-score
set distributions, and full match game margin (spread) and total game mass functions
for Best-of-3 and Best-of-5 tennis matches.
"""

from __future__ import annotations

from dataclasses import dataclass


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


def match_game_distribution(
    pa_point: float, pb_point: float, best_of: int = 3
) -> dict[tuple[int, int], float]:
    """Computes distribution over match games won (total_games_a, total_games_b)."""
    pa_hold = hold_game_probability(pa_point)
    pb_hold = hold_game_probability(pb_point)
    p_tb_a = tiebreak_probability(pa_point, pb_point)
    set_dist = set_score_distribution(pa_hold, pb_hold, p_tb_a)

    target_sets = (best_of // 2) + 1
    match_dp: dict[tuple[int, int, int, int], float] = {(0, 0, 0, 0): 1.0}
    finished_match_games: dict[tuple[int, int], float] = {}

    for _ in range(best_of):
        next_dp: dict[tuple[int, int, int, int], float] = {}
        for (sa, sb, ga, gb), m_prob in match_dp.items():
            if sa == target_sets or sb == target_sets:
                finished_match_games[(ga, gb)] = finished_match_games.get((ga, gb), 0.0) + m_prob
                continue
            for (set_ga, set_gb), set_prob in set_dist.items():
                new_sa = sa + 1 if set_ga > set_gb else sa
                new_sb = sb + 1 if set_gb > set_ga else sb
                new_ga = ga + set_ga
                new_gb = gb + set_gb
                key = (new_sa, new_sb, new_ga, new_gb)
                next_dp[key] = next_dp.get(key, 0.0) + m_prob * set_prob
        match_dp = next_dp

    for (sa, sb, ga, gb), m_prob in match_dp.items():
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
) -> TennisDerivativePricing:
    """Prices moneyline, game spread, and total games using exact Markov distribution."""
    dist = match_game_distribution(p_serve_a, p_serve_b, best_of=best_of)

    p_win_a = sum(p for (ga, gb), p in dist.items() if ga > gb)
    p_win_b = 1.0 - p_win_a
    exp_ga = sum(ga * p for (ga, gb), p in dist.items())
    exp_gb = sum(gb * p for (ga, gb), p in dist.items())
    exp_total = exp_ga + exp_gb

    p1_spread_cov = None
    p2_spread_cov = None
    if spread_line is not None:
        p1_spread_cov = sum(p for (ga, gb), p in dist.items() if (ga - gb) + spread_line > 0)
        p2_spread_cov = 1.0 - p1_spread_cov

    tot_over = None
    tot_under = None
    if total_line is not None:
        tot_over = sum(p for (ga, gb), p in dist.items() if (ga + gb) > total_line)
        tot_under = 1.0 - tot_over

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
