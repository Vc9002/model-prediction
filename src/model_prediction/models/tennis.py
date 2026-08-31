"""Unified tennis model: surface-aware Elo for flat-call match winners.

Also home of ``TennisPlayerForm``, the record the Sackmann CSV loader builds.
Research state until validated through the backtester.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..features.elo_ratings import expected_win_probability
from .base import GamePrediction
from .tennis_derivatives import price_tennis_derivatives

TENNIS_MODEL_VERSION = "tennis-surface-elo-v1"
DEFAULT_ELO = 1500.0
K_FACTOR = 32.0


@dataclass(frozen=True)
class TennisPlayerForm:
    """Point-in-time player form built by the Sackmann CSV loader.

    Field shape is preserved from the original loader contract so cached
    consumers keep working.
    """

    player_id: str
    name: str
    serve_points_won: float
    serve_points: int
    return_points_won: float
    return_points: int
    surface_elo: float
    overall_elo: float
    recent_serve_points_won: float | None = None
    recent_serve_points: int = 0
    recent_return_points_won: float | None = None
    recent_return_points: int = 0
    status: str = "available"


@dataclass(frozen=True)
class UpcomingMatch:
    event_id: str
    event_start_utc: str
    player_one: str
    player_two: str
    surface: str = "Hard"
    tour: str = "ATP"
    spread_player_one_line: float | None = None
    total_games_line: float | None = None
    best_of: int = 3


class TennisModel:
    version = TENNIS_MODEL_VERSION
    sport = "tennis"

    def build_elo(
        self, matches: Sequence[dict[str, Any]]
    ) -> tuple[dict[str, float], dict[tuple[str, str], float], dict[str, int], dict[tuple[str, str], int]]:
        """Chronological overall and per-surface Elo from match dicts.

        Matches need keys: winner, loser, surface, match_date (sortable).
        Also returns per-player and per-surface match counts for Bayesian shrinkage.
        """
        overall: dict[str, float] = {}
        by_surface: dict[tuple[str, str], float] = {}
        counts: dict[str, int] = {}
        surface_counts: dict[tuple[str, str], int] = {}
        for match in sorted(matches, key=lambda item: str(item.get("match_date", ""))):
            winner = str(match.get("winner", ""))
            loser = str(match.get("loser", ""))
            surface = str(match.get("surface", "Hard"))
            if not winner or not loser:
                continue
            for book, key_w, key_l in (
                (overall, winner, loser),
                (by_surface, (winner, surface), (loser, surface)),
            ):
                rating_w = book.get(key_w, DEFAULT_ELO)  # type: ignore[arg-type]
                rating_l = book.get(key_l, DEFAULT_ELO)  # type: ignore[arg-type]
                expected = expected_win_probability(rating_w, rating_l)
                book[key_w] = rating_w + K_FACTOR * (1 - expected)  # type: ignore[index]
                book[key_l] = rating_l - K_FACTOR * (1 - expected)  # type: ignore[index]
            counts[winner] = counts.get(winner, 0) + 1
            counts[loser] = counts.get(loser, 0) + 1
            surface_counts[(winner, surface)] = surface_counts.get((winner, surface), 0) + 1
            surface_counts[(loser, surface)] = surface_counts.get((loser, surface), 0) + 1
        return overall, by_surface, counts, surface_counts

    def match_probability(
        self,
        overall: dict[str, float],
        by_surface: dict[tuple[str, str], float],
        surface_counts: dict[tuple[str, str], int],
        player_one: str,
        player_two: str,
        surface: str,
        shrinkage_prior_matches: float = 15.0,
        max_surface_weight: float = 0.85,
    ) -> float:
        """Bayesian sample-weighted surface Elo shrinkage:
        w = (n_surface / (n_surface + 15)) * 0.85
        """
        n1 = surface_counts.get((player_one, surface), 0)
        n2 = surface_counts.get((player_two, surface), 0)
        w1 = (n1 / (n1 + shrinkage_prior_matches)) * max_surface_weight
        w2 = (n2 / (n2 + shrinkage_prior_matches)) * max_surface_weight

        blend_one = w1 * by_surface.get((player_one, surface), DEFAULT_ELO) + (1 - w1) * overall.get(
            player_one, DEFAULT_ELO
        )
        blend_two = w2 * by_surface.get((player_two, surface), DEFAULT_ELO) + (1 - w2) * overall.get(
            player_two, DEFAULT_ELO
        )
        return expected_win_probability(blend_one, blend_two)

    def predict_games(
        self,
        matches: Sequence[dict[str, Any]],
        upcoming: Sequence[UpcomingMatch],
    ) -> list[GamePrediction]:
        overall, by_surface, counts, surface_counts = self.build_elo(matches)
        predictions = []
        for match in upcoming:
            known_one = match.player_one in overall
            known_two = match.player_two in overall
            if not (known_one and known_two):
                continue
            p_one = self.match_probability(
                overall, by_surface, surface_counts, match.player_one, match.player_two, match.surface
            )
            uncertainty = 0.05  # source of truth: config.TENNIS_MODEL_UNCERTAINTY

            # Base serve point probability estimated from tour and surface Elo differential
            base_serve = 0.635 if match.tour.upper() == "ATP" else 0.590
            r1 = by_surface.get((match.player_one, match.surface), DEFAULT_ELO)
            r2 = by_surface.get((match.player_two, match.surface), DEFAULT_ELO)
            elo_diff = r1 - r2
            p_serve_a = max(0.45, min(0.78, base_serve + (elo_diff / 2400.0)))
            p_serve_b = max(0.45, min(0.78, base_serve - (elo_diff / 2400.0)))

            deriv = price_tennis_derivatives(
                p_serve_a=p_serve_a,
                p_serve_b=p_serve_b,
                spread_line=match.spread_player_one_line,
                total_line=match.total_games_line,
                best_of=match.best_of,
            )

            feature_basis = {
                "surface": match.surface,
                "elo_p1_surface": round(by_surface.get((match.player_one, match.surface), DEFAULT_ELO), 1),
                "elo_p2_surface": round(by_surface.get((match.player_two, match.surface), DEFAULT_ELO), 1),
                "history_matches": len(matches),
                "min_player_matches": min(counts.get(match.player_one, 0), counts.get(match.player_two, 0)),
                "expected_games_p1": deriv.expected_games_a,
                "expected_games_p2": deriv.expected_games_b,
                "expected_total_games": deriv.expected_total_games,
            }

            base_pred = {
                "event_id": match.event_id,
                "event_start_utc": match.event_start_utc,
                "league": match.tour,
                "away_team": match.player_one,
                "home_team": match.player_two,
                "uncertainty": uncertainty,
                "model_version": self.version,
                "feature_basis": feature_basis,
            }

            # 1. Moneyline
            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    probabilities={
                        "away": round(p_one, 6),  # player_one mapped to "away" slot
                        "home": round(1 - p_one, 6),
                    },
                    rationale=(
                        f"Surface-blended Elo on {match.surface}: "
                        f"{match.player_one} p={p_one:.3f} vs {match.player_two}."
                    ),
                    **base_pred,
                )
            )

            # 2. Game Spread
            if (
                match.spread_player_one_line is not None
                and deriv.spread_p1_cover is not None
                and deriv.spread_p2_cover is not None
            ):
                predictions.append(
                    GamePrediction(
                        market_type="spread",
                        line=match.spread_player_one_line,
                        probabilities={
                            "away": round(deriv.spread_p1_cover, 6),
                            "home": round(deriv.spread_p2_cover, 6),
                            "away_cover": round(deriv.spread_p1_cover, 6),
                            "home_cover": round(deriv.spread_p2_cover, 6),
                        },
                        rationale=(
                            f"Tennis Markov spread line {match.spread_player_one_line:+.1f} games: "
                            f"{match.player_one} cover {deriv.spread_p1_cover * 100:.1f}% vs {match.player_two}."
                        ),
                        **base_pred,
                    )
                )

            # 3. Game Total
            if (
                match.total_games_line is not None
                and deriv.total_over is not None
                and deriv.total_under is not None
            ):
                predictions.append(
                    GamePrediction(
                        market_type="total",
                        line=match.total_games_line,
                        probabilities={
                            "over": round(deriv.total_over, 6),
                            "under": round(deriv.total_under, 6),
                        },
                        rationale=(
                            f"Tennis Markov total games {match.total_games_line:.1f}: "
                            f"Over {deriv.total_over * 100:.1f}%, Under {deriv.total_under * 100:.1f}% "
                            f"(exp {deriv.expected_total_games:.1f} games)."
                        ),
                        **base_pred,
                    )
                )

        return predictions


def tennis_model() -> TennisModel:
    return TennisModel()
