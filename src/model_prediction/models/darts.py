"""Darts match prediction engine using an exact Markov leg-progression distribution.

Supports Professional Darts Corporation (PDC) and MODUS Super Series formats.
Models leg hold/break dynamics conditional on throw advantage and player relative
skill ratings, then computes exact match score probability distributions via dynamic
programming to price moneyline, leg spread, and total legs markets.

Independent probabilities from game records: market prices never enter this model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import exp
from typing import Any

from .base import GamePrediction, GamePredictionBase

DARTS_V1_MODEL_VERSION = "darts-markov-v1"
DARTS_V2_MODEL_VERSION = "darts-markov-v2"
DARTS_MODEL_VERSION = DARTS_V2_MODEL_VERSION
# Baseline throw advantage in log-odds: a player holds throw ~62.2% of the time against an equal opponent
DEFAULT_THROW_ADVANTAGE_LOGIT = 0.50
# Scale factor converting Elo rating differences into logit probability shifts.
# The textbook ln(10)/400 is ~3.3x too steep for darts leg odds: on 2,251 PDC
# matches (2024-2026, Wikipedia brackets) a walk-forward fit tuned on the middle
# 40% scored holdout log-loss 0.642 at 0.3x versus 0.751 at 1.0x (coin flip 0.693).
ELO_SCALE_LOGIT = 0.3 / 173.7178


def _logistic(x: float) -> float:
    """Standard logistic function bounded away from exact 0 and 1."""
    if x > 35.0:
        return 0.999999
    if x < -35.0:
        return 0.000001
    return 1.0 / (1.0 + exp(-x))


@dataclass(slots=True)
class DartsMatchRecord:
    """Historical or completed darts match record."""

    match_id: str
    match_date: str
    league: str
    player_a: str
    player_b: str
    legs_a: int
    legs_b: int
    winner: str


@dataclass(frozen=True)
class UpcomingDartsMatch:
    """Specification of an upcoming darts match for prediction."""

    event_id: str
    event_start_utc: str
    player_a: str
    player_b: str
    best_of_legs: int = 11  # e.g., 7 for MODUS, 11 for PDC European Tour, 19 for major finals
    league: str = "DARTS_PDC"
    spread_line: float | None = None
    total_line: float | None = None


class DartsLegMarkovEngine:
    """Exact Markov leg progression engine for best-of-N legs darts matches."""

    def __init__(
        self,
        throw_advantage_logit: float = DEFAULT_THROW_ADVANTAGE_LOGIT,
        scale: float = ELO_SCALE_LOGIT,
    ) -> None:
        self.throw_advantage_logit = throw_advantage_logit
        self.scale = scale

    def compute_leg_probabilities(
        self,
        rating_a: float,
        rating_b: float,
    ) -> tuple[float, float, float, float]:
        """Compute (p_hold_a, p_break_a, p_hold_b, p_break_b).

        - p_hold_a: P(A wins leg | A throws first)
        - p_break_a: P(A wins leg | B throws first) = 1 - p_hold_b
        - p_hold_b: P(B wins leg | B throws first)
        - p_break_b: P(B wins leg | A throws first) = 1 - p_hold_a
        """
        diff_a_b = (rating_a - rating_b) * self.scale
        p_hold_a = _logistic(self.throw_advantage_logit + diff_a_b)
        p_hold_b = _logistic(self.throw_advantage_logit - diff_a_b)
        p_break_a = 1.0 - p_hold_b
        p_break_b = 1.0 - p_hold_a
        return p_hold_a, p_break_a, p_hold_b, p_break_b

    def score_distribution(
        self,
        rating_a: float,
        rating_b: float,
        best_of_legs: int = 11,
        first_throw: str = "A",
    ) -> dict[tuple[int, int], float]:
        """Compute exact joint probability distribution of final leg scores (legs_a, legs_b).

        best_of_legs: must be an odd positive integer (e.g. 7, 9, 11, 19).
        legs_to_win: (best_of_legs + 1) // 2.
        """
        legs_to_win = (best_of_legs + 1) // 2
        p_hold_a, p_break_a, _, _ = self.compute_leg_probabilities(rating_a, rating_b)

        # State representation: dp[i][j][throw] is probability of reaching (i legs for A, j legs for B)
        # with next thrower 'throw' (0 for A, 1 for B).
        # We start at (0, 0) with throw = 0 (A) or 1 (B) with probability 1.0.
        dp: dict[tuple[int, int, int], float] = {(0, 0, 0 if first_throw == "A" else 1): 1.0}
        final_scores: dict[tuple[int, int], float] = {}

        for _ in range(best_of_legs):
            next_dp: dict[tuple[int, int, int], float] = {}
            for (i, j, throw), prob in dp.items():
                if prob <= 0.0:
                    continue
                # If either player has already won, this state shouldn't progress
                if i == legs_to_win or j == legs_to_win:
                    final_scores[(i, j)] = final_scores.get((i, j), 0.0) + prob
                    continue

                if throw == 0:
                    # Player A throws:
                    # A wins leg with p_hold_a
                    next_dp[(i + 1, j, 1)] = next_dp.get((i + 1, j, 1), 0.0) + prob * p_hold_a
                    # B wins leg with (1 - p_hold_a)
                    next_dp[(i, j + 1, 1)] = next_dp.get((i, j + 1, 1), 0.0) + prob * (1.0 - p_hold_a)
                else:
                    # Player B throws:
                    # A wins leg with p_break_a
                    next_dp[(i + 1, j, 0)] = next_dp.get((i + 1, j, 0), 0.0) + prob * p_break_a
                    # B wins leg with (1 - p_break_a)
                    next_dp[(i, j + 1, 0)] = next_dp.get((i, j + 1, 0), 0.0) + prob * (1.0 - p_break_a)
            dp = next_dp

        # Collect any remaining states that reached legs_to_win
        for (i, j, _), prob in dp.items():
            if prob > 0.0:
                final_scores[(i, j)] = final_scores.get((i, j), 0.0) + prob

        # Normalize to ensure exactly 1.0 sum
        total_p = sum(final_scores.values())
        if total_p > 0.0:
            return {k: v / total_p for k, v in final_scores.items()}
        return final_scores

    def evaluate_markets(
        self,
        rating_a: float,
        rating_b: float,
        best_of_legs: int = 11,
    ) -> dict[str, Any]:
        """Evaluate moneyline, spreads, and totals over a 50/50 toss prior for first throw."""
        # Assume 50% probability A throws first, 50% B throws first
        dist_a_first = self.score_distribution(rating_a, rating_b, best_of_legs, first_throw="A")
        dist_b_first = self.score_distribution(rating_a, rating_b, best_of_legs, first_throw="B")

        scores: dict[tuple[int, int], float] = {}
        all_keys = set(dist_a_first.keys()).union(dist_b_first.keys())
        for k in all_keys:
            scores[k] = 0.5 * dist_a_first.get(k, 0.0) + 0.5 * dist_b_first.get(k, 0.0)

        legs_to_win = (best_of_legs + 1) // 2
        p_win_a = sum(p for (i, j), p in scores.items() if i == legs_to_win)
        p_win_b = sum(p for (i, j), p in scores.items() if j == legs_to_win)

        expected_legs_a = sum(i * p for (i, j), p in scores.items())
        expected_legs_b = sum(j * p for (i, j), p in scores.items())
        expected_total_legs = sum((i + j) * p for (i, j), p in scores.items())

        return {
            "p_win_a": p_win_a,
            "p_win_b": p_win_b,
            "expected_legs_a": expected_legs_a,
            "expected_legs_b": expected_legs_b,
            "expected_total_legs": expected_total_legs,
            "score_distribution": scores,
        }


class DartsModel:
    """Production-ready Darts model tracking player ratings and leg distributions."""

    @staticmethod
    def compute_dynamic_units(edge: float, min_edge: float = 0.02, max_edge: float = 0.08) -> float:
        """Dynamic unit sizing scaling from 1.00U to 2.00U with edge."""
        abs_edge = abs(edge)
        scaled = 1.0 + min(1.0, max(0.0, (abs_edge - min_edge) / (max_edge - min_edge)))
        return round(float(scaled), 2)

    sport = "darts"

    def __init__(
        self,
        version: str = DARTS_MODEL_VERSION,
        k_factor: float = 24.0,
        default_rating: float = 1500.0,
        throw_advantage_logit: float = DEFAULT_THROW_ADVANTAGE_LOGIT,
    ) -> None:
        self.version = version
        self.k_factor = k_factor
        self.default_rating = default_rating
        self.engine = DartsLegMarkovEngine(throw_advantage_logit=throw_advantage_logit)
        self.ratings: dict[str, float] = {}
        self.match_counts: dict[str, int] = {}

    def get_rating(self, player: str) -> float:
        """Return player Elo rating with default fallback."""
        return self.ratings.get(player, self.default_rating)

    def update_match(self, record: DartsMatchRecord) -> None:
        """Update Elo ratings based on a completed match record."""
        ra = self.get_rating(record.player_a)
        rb = self.get_rating(record.player_b)

        # Match outcome
        actual_a = (
            1.0 if record.winner == record.player_a else (0.5 if record.legs_a == record.legs_b else 0.0)
        )

        # Expected outcome
        exp_a = 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))

        # Margin of victory multiplier
        tot_legs = max(1, record.legs_a + record.legs_b)
        margin_mult = 1.0 + (abs(record.legs_a - record.legs_b) / float(tot_legs))

        shift = self.k_factor * margin_mult * (actual_a - exp_a)
        self.ratings[record.player_a] = ra + shift
        self.ratings[record.player_b] = rb - shift

        self.match_counts[record.player_a] = self.match_counts.get(record.player_a, 0) + 1
        self.match_counts[record.player_b] = self.match_counts.get(record.player_b, 0) + 1

    def fit(self, history: Sequence[DartsMatchRecord]) -> None:
        """Sequential chronological fit on match records."""
        self.ratings.clear()
        self.match_counts.clear()
        for match in sorted(history, key=lambda m: m.match_date):
            self.update_match(match)

    def predict_match(
        self,
        player_a: str,
        player_b: str,
        best_of_legs: int = 11,
        spread_line: float | None = None,
        total_line: float | None = None,
    ) -> dict[str, Any]:
        """Compute full market predictions for a matchup."""
        ra = self.get_rating(player_a)
        rb = self.get_rating(player_b)
        result = self.engine.evaluate_markets(ra, rb, best_of_legs=best_of_legs)

        sample_a = self.match_counts.get(player_a, 0)
        sample_b = self.match_counts.get(player_b, 0)
        sample = min(sample_a, sample_b)

        p_win_a = result["p_win_a"]
        p_win_b = result["p_win_b"]
        # In v2, if player sample is below threshold, shrink win probability toward 0.50
        if self.version != DARTS_V1_MODEL_VERSION and sample < 3:
            weight = sample / 3.0
            p_win_a = weight * p_win_a + (1.0 - weight) * 0.50
            p_win_b = weight * p_win_b + (1.0 - weight) * 0.50
            tot = p_win_a + p_win_b
            if tot > 0:
                p_win_a /= tot
                p_win_b /= tot

        scores = result["score_distribution"]

        # Spread probability: player_a covers line S if legs_a - legs_b + S > 0
        spread_probs: dict[float, dict[str, float]] = {}
        candidate_spreads = [-2.5, -1.5, 1.5, 2.5] if spread_line is None else [spread_line]
        for s in candidate_spreads:
            p_cov_a = sum(p for (i, j), p in scores.items() if (i - j) + s > 0)
            p_cov_b = sum(p for (i, j), p in scores.items() if (i - j) + s < 0)
            p_push = sum(p for (i, j), p in scores.items() if (i - j) + s == 0)
            eff_a = p_cov_a + 0.5 * p_push
            eff_b = p_cov_b + 0.5 * p_push
            spread_probs[s] = {"a": eff_a, "b": eff_b}

        # Totals probability: over line T if (legs_a + legs_b) > T
        total_probs: dict[float, dict[str, float]] = {}
        default_totals = [
            float((best_of_legs + 1) // 2 + 1.5),
            float((best_of_legs + 1) // 2 + 2.5),
            float(best_of_legs - 1.5),
        ]
        candidate_totals = default_totals if total_line is None else [total_line]
        for t in candidate_totals:
            p_over = sum(p for (i, j), p in scores.items() if (i + j) > t)
            p_under = sum(p for (i, j), p in scores.items() if (i + j) < t)
            p_push = sum(p for (i, j), p in scores.items() if (i + j) == t)
            eff_over = p_over + 0.5 * p_push
            eff_under = p_under + 0.5 * p_push
            total_probs[t] = {"over": eff_over, "under": eff_under}

        return {
            "player_a": player_a,
            "player_b": player_b,
            "rating_a": ra,
            "rating_b": rb,
            "best_of_legs": best_of_legs,
            "moneyline": {"a": p_win_a, "b": p_win_b},
            "spread": spread_probs,
            "total": total_probs,
            "expected_legs_a": result["expected_legs_a"],
            "expected_legs_b": result["expected_legs_b"],
            "expected_total_legs": result["expected_total_legs"],
        }

    def predict_games(
        self,
        history: Sequence[DartsMatchRecord],
        upcoming: Sequence[UpcomingDartsMatch],
    ) -> list[GamePrediction]:
        """Standard pipeline batch prediction method returning GamePrediction items."""
        if history:
            self.fit(history)

        predictions: list[GamePrediction] = []
        for game in upcoming:
            eval_res = self.predict_match(
                player_a=game.player_a,
                player_b=game.player_b,
                best_of_legs=game.best_of_legs,
                spread_line=game.spread_line,
                total_line=game.total_line,
            )

            p_a = eval_res["moneyline"]["a"]
            p_b = eval_res["moneyline"]["b"]

            matches_a = self.match_counts.get(game.player_a, 0)
            matches_b = self.match_counts.get(game.player_b, 0)
            sample = min(matches_a, matches_b)
            uncertainty = max(0.04, min(0.20, 0.20 - 0.01 * sample))

            base: GamePredictionBase = {
                "event_id": game.event_id,
                "event_start_utc": game.event_start_utc,
                "league": game.league,
                "away_team": game.player_b,
                "home_team": game.player_a,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.version,
                "feature_basis": {
                    "rating_a": round(eval_res["rating_a"], 1),
                    "rating_b": round(eval_res["rating_b"], 1),
                    "expected_legs_a": round(eval_res["expected_legs_a"], 2),
                    "expected_legs_b": round(eval_res["expected_legs_b"], 2),
                    "expected_total_legs": round(eval_res["expected_total_legs"], 2),
                    "best_of_legs": game.best_of_legs,
                    "sample_matches": sample,
                },
            }

            # Moneyline prediction
            rationale_ml = (
                f"Markov leg model: {game.player_a} ({eval_res['rating_a']:.0f}) vs "
                f"{game.player_b} ({eval_res['rating_b']:.0f}), best of {game.best_of_legs} legs."
            )
            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    probabilities={
                        "home": round(p_a, 6),
                        "away": round(p_b, 6),
                    },
                    rationale=rationale_ml,
                    **base,
                )
            )

            # Spread predictions
            for s_line, s_prob in eval_res["spread"].items():
                predictions.append(
                    GamePrediction(
                        market_type="spread",
                        line=s_line,
                        probabilities={
                            "home": round(s_prob["a"], 6),
                            "away": round(s_prob["b"], 6),
                            "home_cover": round(s_prob["a"], 6),
                            "away_cover": round(s_prob["b"], 6),
                        },
                        rationale=f"Leg spread {s_line:+.1f}: {game.player_a} cover p={s_prob['a']:.3f}.",
                        **base,
                    )
                )

            # Total predictions
            for t_line, t_prob in eval_res["total"].items():
                predictions.append(
                    GamePrediction(
                        market_type="total",
                        line=t_line,
                        probabilities={
                            "over": round(t_prob["over"], 6),
                            "under": round(t_prob["under"], 6),
                        },
                        rationale=f"Total legs {t_line:.1f}: over p={t_prob['over']:.3f}, exp={eval_res['expected_total_legs']:.1f}.",
                        **base,
                    )
                )

        return predictions


def darts_v1_model() -> DartsModel:
    """Legacy Darts v1 model factory."""
    return DartsModel(version=DARTS_V1_MODEL_VERSION)


def darts_v2_model() -> DartsModel:
    """Production Darts v2 model factory."""
    return DartsModel(version=DARTS_V2_MODEL_VERSION)


def darts_model() -> DartsModel:
    """Default Darts model factory (points to v2)."""
    return darts_v2_model()
