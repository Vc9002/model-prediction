"""Unified tennis model: surface-aware Elo for flat-call match winners.

Also home of ``TennisPlayerForm``, the record the Sackmann CSV loader builds.
Research state until validated through the backtester.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..features.elo_ratings import expected_win_probability
from .base import GamePrediction
from .tennis_derivatives import price_tennis_derivatives

TENNIS_V4_VERSION = "tennis-surface-elo-v4"
TENNIS_V3_VERSION = "tennis-learned-v3"
TENNIS_MARKOV_VERSION = "tennis-markov-hierarchical-v2"
TENNIS_LEGACY_VERSION = "tennis-surface-elo-v1"
TENNIS_MODEL_VERSION = TENNIS_V4_VERSION
DEFAULT_ELO = 1500.0
K_FACTOR = 32.0

# Learned fundamental weights for tennis-learned-v3:
# features: [(blend1 - blend2)/400, (r1_ov - r2_ov)/400, (rest1 - rest2)/10, log(1+exp1) - log(1+exp2), is_atp, surface_cat]
V3_COEFFICIENTS = [1.001068, 1.276491, -0.033573, 0.308867, -0.006848, 0.011058]
V3_INTERCEPT = -0.000578

# Market-residual logistic layer weights:
# logit(p_final) = intercept + w_model * logit(p_fundamental) + w_market * logit(p_market)
RESIDUAL_INTERCEPT = -0.066704
RESIDUAL_W_MODEL = 0.223602
RESIDUAL_W_MARKET = 0.958379


def _logit(probability: float) -> float:
    eps = 1e-6
    clipped = min(1.0 - eps, max(eps, probability))
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def apply_market_residual(
    model_probability: float,
    market_probability: float,
    intercept: float = RESIDUAL_INTERCEPT,
    w_model: float = RESIDUAL_W_MODEL,
    w_market: float = RESIDUAL_W_MARKET,
) -> float:
    """Combine fundamental model win probability with market consensus price."""
    score = intercept + w_model * _logit(model_probability) + w_market * _logit(market_probability)
    return _sigmoid(score)


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
    player_one_form: TennisPlayerForm | None = None
    player_two_form: TennisPlayerForm | None = None
    market_probability: float | None = None


class TennisModel:
    version = TENNIS_MODEL_VERSION
    sport = "tennis"

    def __init__(self, version: str = TENNIS_MODEL_VERSION) -> None:
        self.version = version
        self.last_dates: dict[str, datetime] = {}
        self.recent_form: dict[str, float] = {}

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
        self.last_dates = {}
        self.recent_form = {}

        if self.version == TENNIS_V4_VERSION:
            for match in sorted(
                matches, key=lambda item: str(item.get("match_date", "") or item.get("event_start_utc", ""))
            ):
                winner = str(match.get("winner", ""))
                loser = str(match.get("loser", ""))
                surface = str(match.get("surface", "Hard"))
                d_str = str(match.get("match_date", "") or match.get("event_start_utc", ""))
                if not winner or not loser:
                    continue

                # 1. Base Overall (K=32)
                rw = overall.get(winner, DEFAULT_ELO)
                rl = overall.get(loser, DEFAULT_ELO)
                expected = expected_win_probability(rw, rl)
                overall[winner] = rw + K_FACTOR * (1.0 - expected)
                overall[loser] = rl - K_FACTOR * (1.0 - expected)

                # 2. Surface Elo (initialized at current overall Elo)
                rsw = by_surface.get((winner, surface), rw)
                rsl = by_surface.get((loser, surface), rl)
                expected_surf = expected_win_probability(rsw, rsl)
                by_surface[(winner, surface)] = rsw + K_FACTOR * (1.0 - expected_surf)
                by_surface[(loser, surface)] = rsl - K_FACTOR * (1.0 - expected_surf)

                # 3. Recent Form Elo (recency momentum K=48)
                rfw = self.recent_form.get(winner, DEFAULT_ELO)
                rfl = self.recent_form.get(loser, DEFAULT_ELO)
                expected_form = expected_win_probability(rfw, rfl)
                self.recent_form[winner] = rfw + 48.0 * (1.0 - expected_form)
                self.recent_form[loser] = rfl - 48.0 * (1.0 - expected_form)

                counts[winner] = counts.get(winner, 0) + 1
                counts[loser] = counts.get(loser, 0) + 1
                surface_counts[(winner, surface)] = surface_counts.get((winner, surface), 0) + 1
                surface_counts[(loser, surface)] = surface_counts.get((loser, surface), 0) + 1

                if d_str:
                    try:
                        s_clean = d_str.replace("Z", "+00:00")
                        if "T" in s_clean:
                            dt = datetime.fromisoformat(s_clean)
                        else:
                            dt = datetime.strptime(s_clean.split()[0], "%Y-%m-%d").replace(tzinfo=UTC)
                        self.last_dates[winner] = dt
                        self.last_dates[loser] = dt
                    except (ValueError, TypeError):
                        self.last_dates.setdefault(winner, datetime(2025, 1, 1, tzinfo=UTC))

            return overall, by_surface, counts, surface_counts

        if self.version == TENNIS_LEGACY_VERSION:
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

        # Adaptive K and surface initialization from overall Elo
        for match in sorted(
            matches, key=lambda item: str(item.get("match_date", "") or item.get("event_start_utc", ""))
        ):
            winner = str(match.get("winner", ""))
            loser = str(match.get("loser", ""))
            surface = str(match.get("surface", "Hard"))
            d_str = str(match.get("match_date", "") or match.get("event_start_utc", ""))
            if not winner or not loser:
                continue

            rw = overall.get(winner, DEFAULT_ELO)
            rl = overall.get(loser, DEFAULT_ELO)
            expected = expected_win_probability(rw, rl)

            kw = 40.0 if counts.get(winner, 0) < 15 else 28.0
            kl = 40.0 if counts.get(loser, 0) < 15 else 28.0
            overall[winner] = rw + kw * (1.0 - expected)
            overall[loser] = rl - kl * (1.0 - expected)

            # Surface rating initializes at current overall rating, avoiding collapse to 1500
            rsw = by_surface.get((winner, surface), rw)
            rsl = by_surface.get((loser, surface), rl)
            expected_surf = expected_win_probability(rsw, rsl)
            by_surface[(winner, surface)] = rsw + 20.0 * (1.0 - expected_surf)
            by_surface[(loser, surface)] = rsl - 20.0 * (1.0 - expected_surf)

            counts[winner] = counts.get(winner, 0) + 1
            counts[loser] = counts.get(loser, 0) + 1
            surface_counts[(winner, surface)] = surface_counts.get((winner, surface), 0) + 1
            surface_counts[(loser, surface)] = surface_counts.get((loser, surface), 0) + 1

            if d_str:
                try:
                    dt = datetime.fromisoformat(d_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    else:
                        dt = dt.astimezone(UTC)
                    self.last_dates[winner] = dt
                    self.last_dates[loser] = dt
                except (ValueError, TypeError):
                    self.last_dates.setdefault(winner, datetime(2025, 1, 1, tzinfo=UTC))

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

    def match_probability_v3(
        self,
        overall: dict[str, float],
        by_surface: dict[tuple[str, str], float],
        surface_counts: dict[tuple[str, str], int],
        counts: dict[str, int],
        rest_days: dict[str, float],
        player_one: str,
        player_two: str,
        surface: str,
        tour: str = "ATP",
    ) -> float:
        """Multi-feature learned fundamental match probability."""
        r1_ov = overall.get(player_one, DEFAULT_ELO)
        r2_ov = overall.get(player_two, DEFAULT_ELO)
        r1_s = by_surface.get((player_one, surface), r1_ov)
        r2_s = by_surface.get((player_two, surface), r2_ov)
        c1_s = surface_counts.get((player_one, surface), 0)
        c2_s = surface_counts.get((player_two, surface), 0)
        w1_s = min(0.80, c1_s / (c1_s + 15.0))
        w2_s = min(0.80, c2_s / (c2_s + 15.0))
        blend1 = w1_s * r1_s + (1.0 - w1_s) * r1_ov
        blend2 = w2_s * r2_s + (1.0 - w2_s) * r2_ov

        rest1 = rest_days.get(player_one, 14.0)
        rest2 = rest_days.get(player_two, 14.0)
        exp1 = counts.get(player_one, 0)
        exp2 = counts.get(player_two, 0)

        surf_cat = 1.0 if surface.lower() == "clay" else (2.0 if surface.lower() == "grass" else 0.0)
        is_atp = 1.0 if tour.upper() == "ATP" else 0.0

        feat = [
            (blend1 - blend2) / 400.0,
            (r1_ov - r2_ov) / 400.0,
            (rest1 - rest2) / 10.0,
            math.log(1.0 + exp1) - math.log(1.0 + exp2),
            is_atp,
            surf_cat,
        ]
        score = V3_INTERCEPT + sum(c * f for c, f in zip(V3_COEFFICIENTS, feat, strict=True))
        return _sigmoid(score)

    def match_probability_v4(
        self,
        overall: dict[str, float],
        by_surface: dict[tuple[str, str], float],
        surface_counts: dict[tuple[str, str], int],
        counts: dict[str, int],
        player_one: str,
        player_two: str,
        surface: str,
        rest_days: dict[str, float] | None = None,
        best_of: int = 3,
    ) -> float:
        """Dual-horizon surface Elo with dynamic shrinkage, rust decay, and format translation."""
        p1 = player_one
        p2 = player_two
        ov1 = overall.get(p1, DEFAULT_ELO)
        ov2 = overall.get(p2, DEFAULT_ELO)
        s1 = by_surface.get((p1, surface), ov1)
        s2 = by_surface.get((p2, surface), ov2)
        c1 = surface_counts.get((p1, surface), 0)
        c2 = surface_counts.get((p2, surface), 0)

        # Dynamic Bayesian surface shrinkage
        s_norm = surface.lower()
        if "clay" in s_norm or "grass" in s_norm:
            w_max, c_surf = 0.80, 12.0
        else:
            w_max, c_surf = 0.75, 15.0

        w1_s = w_max * (c1 / (c1 + c_surf))
        w2_s = w_max * (c2 / (c2 + c_surf))
        base1 = w1_s * s1 + (1.0 - w1_s) * ov1
        base2 = w2_s * s2 + (1.0 - w2_s) * ov2

        # Inactivity rust decay via rest_days
        if isinstance(rest_days, dict):
            rests = rest_days
        elif isinstance(rest_days, str):
            rests = {}
            with contextlib.suppress(ValueError, TypeError):
                dt = datetime.fromisoformat(rest_days)
                for p in (p1, p2):
                    if p in getattr(self, "last_dates", {}):
                        rests[p] = max(0.0, (dt - self.last_dates[p]).total_seconds() / 86400.0)
        else:
            rests = {}
        days1 = float(rests.get(p1, 14.0))
        days2 = float(rests.get(p2, 14.0))

        def _apply_rust(elo_val: float, days: float) -> float:
            if days > 35.0:
                decay = math.exp(-0.003 * (days - 35.0))
                return DEFAULT_ELO + (elo_val - DEFAULT_ELO) * decay
            return elo_val

        eff1 = _apply_rust(base1, days1)
        eff2 = _apply_rust(base2, days2)

        # Blend with recent form
        rf1 = getattr(self, "recent_form", {}).get(p1, ov1)
        rf2 = getattr(self, "recent_form", {}).get(p2, ov2)
        final1 = 0.82 * eff1 + 0.18 * rf1
        final2 = 0.82 * eff2 + 0.18 * rf2

        # Expected single set probability
        p_set = expected_win_probability(final1, final2)

        # Best-of-3 / Best-of-5 transformation
        if best_of == 5:
            q = 1.0 - p_set
            p_match = p_set**3 * (1.0 + 3.0 * q + 6.0 * q * q)
        else:
            p_match = p_set * p_set * (3.0 - 2.0 * p_set)

        return max(0.01, min(0.99, p_match))

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

            def _get_rest(p: str, start_utc: Any) -> float:
                last_dt = self.last_dates.get(p)
                if not last_dt or not start_utc:
                    return 14.0
                try:
                    cur_dt = datetime.fromisoformat(str(start_utc))
                    if cur_dt.tzinfo is None:
                        cur_dt = cur_dt.replace(tzinfo=UTC)
                    else:
                        cur_dt = cur_dt.astimezone(UTC)
                    return float(min(60, max(0, (cur_dt - last_dt).days)))
                except (ValueError, TypeError):
                    return 14.0

            rest1 = _get_rest(match.player_one, match.event_start_utc)
            rest2 = _get_rest(match.player_two, match.event_start_utc)
            rest_dict = {match.player_one: rest1, match.player_two: rest2}

            if self.version == TENNIS_V4_VERSION:
                p_fund = self.match_probability_v4(
                    overall=overall,
                    by_surface=by_surface,
                    surface_counts=surface_counts,
                    counts=counts,
                    player_one=match.player_one,
                    player_two=match.player_two,
                    surface=match.surface,
                    rest_days=rest_dict,
                    best_of=match.best_of,
                )
            elif self.version == TENNIS_V3_VERSION:
                p_fund = self.match_probability_v3(
                    overall=overall,
                    by_surface=by_surface,
                    surface_counts=surface_counts,
                    counts=counts,
                    rest_days=rest_dict,
                    player_one=match.player_one,
                    player_two=match.player_two,
                    surface=match.surface,
                    tour=match.tour,
                )
            else:
                p_fund = self.match_probability(
                    overall, by_surface, surface_counts, match.player_one, match.player_two, match.surface
                )

            uncertainty = 0.05  # source of truth: config.TENNIS_MODEL_UNCERTAINTY

            # Base serve point probability estimated from point-level form or Elo differential
            p_serve_a: float | None = None
            p_serve_b: float | None = None
            if match.player_one_form is not None and match.player_two_form is not None:
                g_tour = 0.365 if match.tour.upper() == "ATP" else 0.410
                denom = max(0.01, 1.0 - g_tour)
                p_serve_a = max(
                    0.40,
                    min(
                        0.85,
                        (
                            match.player_one_form.serve_points_won
                            * (1.0 - match.player_two_form.return_points_won)
                        )
                        / denom,
                    ),
                )
                p_serve_b = max(
                    0.40,
                    min(
                        0.85,
                        (
                            match.player_two_form.serve_points_won
                            * (1.0 - match.player_one_form.return_points_won)
                        )
                        / denom,
                    ),
                )

            if p_serve_a is None or p_serve_b is None:
                base_serve = 0.635 if match.tour.upper() == "ATP" else 0.590
                if self.version == TENNIS_V3_VERSION:
                    eps_prob = 1e-6
                    cl_p = min(1.0 - eps_prob, max(eps_prob, p_fund))
                    implied_elo_diff = 400.0 * math.log10(cl_p / (1.0 - cl_p))
                    p_serve_a = max(0.45, min(0.78, base_serve + (implied_elo_diff / 2400.0)))
                    p_serve_b = max(0.45, min(0.78, base_serve - (implied_elo_diff / 2400.0)))
                else:
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

            base_pred: dict[str, Any] = {
                "event_id": match.event_id,
                "event_start_utc": match.event_start_utc,
                "league": match.tour,
                "away_team": match.player_one,
                "home_team": match.player_two,
                "uncertainty": uncertainty,
                "model_version": self.version,
                "feature_basis": feature_basis,
            }

            from ..coded_market_replay import capture_inputs

            players = [match.player_one, match.player_two]
            inference_inputs = capture_inputs(
                "TENNIS",
                {
                    "players": players,
                    "surface": match.surface,
                    "tour": match.tour,
                    "overall_ratings": [overall[p] for p in players],
                    "surface_ratings": [by_surface.get((p, match.surface), overall[p]) for p in players],
                    "surface_counts": [surface_counts.get((p, match.surface), 0) for p in players],
                    "counts": [counts.get(p, 0) for p in players],
                    "recent_form_ratings": [self.recent_form.get(p, overall[p]) for p in players],
                    "rest_days": [rest1, rest2],
                    "market_probability": match.market_probability,
                    "p_serve_a": p_serve_a,
                    "p_serve_b": p_serve_b,
                    "best_of": match.best_of,
                    "event_start_utc": match.event_start_utc,
                    "model_version": self.version,
                },
            )

            # 1. Moneyline
            if self.version == TENNIS_V4_VERSION:
                p_ml_away = p_fund
                p_ml_home = 1.0 - p_fund
                ml_rationale = (
                    f"Tennis Surface Elo v4 Dual-Horizon on {match.surface} (Bo{match.best_of}): "
                    f"{match.player_one} p={p_ml_away:.3f} vs {match.player_two}."
                )
            elif self.version == TENNIS_V3_VERSION:
                if match.market_probability is not None:
                    p_ml_away = apply_market_residual(p_fund, match.market_probability)
                    p_ml_home = 1.0 - p_ml_away
                    ml_rationale = (
                        f"Tennis v3 Learned Residual on {match.surface} (Bo{match.best_of}): "
                        f"{match.player_one} p={p_ml_away:.3f} (fund={p_fund:.3f}, mkt={match.market_probability:.3f}) vs {match.player_two}."
                    )
                else:
                    p_ml_away = p_fund
                    p_ml_home = 1.0 - p_fund
                    ml_rationale = (
                        f"Tennis v3 Learned Fundamental on {match.surface} (Bo{match.best_of}): "
                        f"{match.player_one} p={p_ml_away:.3f} vs {match.player_two}."
                    )
            elif self.version == TENNIS_MARKOV_VERSION:
                p_ml_away = deriv.match_win_a
                p_ml_home = deriv.match_win_b
                ml_rationale = (
                    f"Hierarchical Markov model on {match.surface} (Bo{match.best_of}): "
                    f"{match.player_one} p={p_ml_away:.3f} vs {match.player_two}."
                )
            else:
                p_ml_away = p_fund
                p_ml_home = 1.0 - p_fund
                ml_rationale = (
                    f"Surface-blended Elo on {match.surface}: "
                    f"{match.player_one} p={p_fund:.3f} vs {match.player_two}."
                )

            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    inference_inputs=inference_inputs,
                    line=None,
                    probabilities={
                        "away": round(p_ml_away, 6),  # player_one mapped to "away" slot
                        "home": round(p_ml_home, 6),
                    },
                    rationale=ml_rationale,
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


class TennisV3Model(TennisModel):
    """Tennis v3 learned fundamental and market residual model."""

    def __init__(self) -> None:
        super().__init__(version=TENNIS_V3_VERSION)


def tennis_model(version: str = TENNIS_MODEL_VERSION) -> TennisModel:
    return TennisModel(version=version)
