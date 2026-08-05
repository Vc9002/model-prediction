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


class TennisModel:
    version = TENNIS_MODEL_VERSION
    sport = "tennis"

    def build_elo(
        self, matches: Sequence[dict[str, Any]]
    ) -> tuple[dict[str, float], dict[tuple[str, str], float], dict[str, int]]:
        """Chronological overall and per-surface Elo from match dicts.

        Matches need keys: winner, loser, surface, match_date (sortable).
        Also returns a real per-player match count -- "known" (present in
        `overall`) only means at least one real match; a player one win/loss
        away from cold-start still isn't a genuine model opinion.
        """
        overall: dict[str, float] = {}
        by_surface: dict[tuple[str, str], float] = {}
        counts: dict[str, int] = {}
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
        return overall, by_surface, counts

    def match_probability(
        self,
        overall: dict[str, float],
        by_surface: dict[tuple[str, str], float],
        player_one: str,
        player_two: str,
        surface: str,
        surface_weight: float = 0.6,
    ) -> float:
        blend_one = (
            surface_weight * by_surface.get((player_one, surface), DEFAULT_ELO)
            + (1 - surface_weight) * overall.get(player_one, DEFAULT_ELO)
        )
        blend_two = (
            surface_weight * by_surface.get((player_two, surface), DEFAULT_ELO)
            + (1 - surface_weight) * overall.get(player_two, DEFAULT_ELO)
        )
        return expected_win_probability(blend_one, blend_two)

    def predict_games(
        self,
        matches: Sequence[dict[str, Any]],
        upcoming: Sequence[UpcomingMatch],
    ) -> list[GamePrediction]:
        overall, by_surface, counts = self.build_elo(matches)
        predictions = []
        for match in upcoming:
            known_one = match.player_one in overall
            known_two = match.player_two in overall
            # Too many WTA/ITF matches run every day to call them all; a player
            # missing from `overall` has zero real history, so their side of
            # the prediction would silently fall back to DEFAULT_ELO (a coin
            # flip dressed up as a model call) rather than a genuine edge.
            # Hard skip instead of just widening uncertainty -- see project
            # instruction to only call matches we actually have data for.
            if not (known_one and known_two):
                continue
            p_one = self.match_probability(
                overall, by_surface, match.player_one, match.player_two, match.surface
            )
            uncertainty = 0.05  # source of truth: config.TENNIS_MODEL_UNCERTAINTY
            predictions.append(
                GamePrediction(
                    event_id=match.event_id,
                    event_start_utc=match.event_start_utc,
                    league=match.tour,
                    away_team=match.player_one,
                    home_team=match.player_two,
                    market_type="moneyline",
                    line=None,
                    probabilities={
                        "away": round(p_one, 6),  # player_one mapped to "away" slot
                        "home": round(1 - p_one, 6),
                    },
                    uncertainty=uncertainty,
                    model_version=self.version,
                    feature_basis={
                        "surface": match.surface,
                        "elo_p1_surface": round(by_surface.get((match.player_one, match.surface), DEFAULT_ELO), 1),
                        "elo_p2_surface": round(by_surface.get((match.player_two, match.surface), DEFAULT_ELO), 1),
                        "history_matches": len(matches),
                        # Real per-player match count -- "known" above only
                        # means at least one, which is a thin, noisy rating.
                        "min_player_matches": min(counts.get(match.player_one, 0), counts.get(match.player_two, 0)),
                    },
                    rationale=(
                        f"Surface-blended Elo on {match.surface}: "
                        f"{match.player_one} p={p_one:.3f} vs {match.player_two}."
                    ),
                )
            )
        return predictions


def tennis_model() -> TennisModel:
    return TennisModel()
