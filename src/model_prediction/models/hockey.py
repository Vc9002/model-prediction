"""Unified hockey model: Dixon-Coles bivariate Poisson goal model with overtime resolution.

Supports all 8 Polymarket hockey leagues:
  * NHL (National Hockey League)
  * AHL (American Hockey League)
  * KHL (Kontinental Hockey League)
  * DEL (Deutsche Eishockey Liga)
  * SHL (Swedish Hockey League)
  * SNHL (Swiss National League)
  * CEHL (Czech Extraliga)
  * LIIGA (Finnish Liiga)

Markets:
  * moneyline (2-way full-game winner, including OT / shootout settlement)
  * spread (home-relative goal handicaps +/-1.5, 2.5, 3.5, OT/SO included)
  * total (full-game over/under: 2.5 through 9.5)

Point-in-time safety: ratings and feature inputs are calculated strictly from
games completed before the target event start time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import exp, factorial
from typing import Any

from ..features.trends import ewm_level
from .base import GamePrediction, GamePredictionBase

HOCKEY_MODEL_VERSION = "hockey-poisson-dc-v1"
MAX_GOALS = 10
# Home-handicap and total lines Polymarket lists for NHL (spreads +/-1.5..3.5, totals 2.5..9.5).
SPREAD_LINES = (-3.5, -2.5, -1.5, 1.5, 2.5, 3.5)
TOTAL_LINES = (2.5, 3.5, 4.5, 5.5, 6.0, 6.5, 7.5, 8.5, 9.5)

# Baseline goals per team per game and home advantage boost by league
LEAGUE_CONSTANTS: dict[str, dict[str, float]] = {
    "NHL": {"baseline": 3.12, "home_boost": 1.12, "rho": -0.06, "ot_home_edge": 0.515},
    "AHL": {"baseline": 3.02, "home_boost": 1.10, "rho": -0.05, "ot_home_edge": 0.510},
    "KHL": {"baseline": 2.58, "home_boost": 1.10, "rho": -0.05, "ot_home_edge": 0.510},
    "DEL": {"baseline": 3.08, "home_boost": 1.12, "rho": -0.06, "ot_home_edge": 0.515},
    "SHL": {"baseline": 2.68, "home_boost": 1.11, "rho": -0.05, "ot_home_edge": 0.512},
    "SNHL": {"baseline": 2.98, "home_boost": 1.12, "rho": -0.06, "ot_home_edge": 0.515},
    "CEHL": {"baseline": 2.82, "home_boost": 1.12, "rho": -0.06, "ot_home_edge": 0.512},
    "LIIGA": {"baseline": 2.65, "home_boost": 1.10, "rho": -0.05, "ot_home_edge": 0.510},
}
DEFAULT_CONSTANTS: dict[str, float] = {
    "baseline": 2.95,
    "home_boost": 1.11,
    "rho": -0.05,
    "ot_home_edge": 0.512,
}


@dataclass(frozen=True)
class UpcomingHockeyMatch:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    league: str = "NHL"


def _poisson_pmf(rate: float, k: int) -> float:
    if rate <= 0:
        return 1.0 if k == 0 else 0.0
    return exp(-rate) * (rate**k) / factorial(k)


def _dc_adjustment(
    home_goals: int,
    away_goals: int,
    home_rate: float,
    away_rate: float,
    rho: float = -0.05,
) -> float:
    """Dixon-Coles adjustment for low-scoring states (0-0, 1-0, 0-1, 1-1)."""
    if home_goals == 0 and away_goals == 0:
        return max(0.0, 1.0 - home_rate * away_rate * rho)
    if home_goals == 0 and away_goals == 1:
        return max(0.0, 1.0 + home_rate * rho)
    if home_goals == 1 and away_goals == 0:
        return max(0.0, 1.0 + away_rate * rho)
    if home_goals == 1 and away_goals == 1:
        return max(0.0, 1.0 - rho)
    return 1.0


class HockeyModel:
    sport = "hockey"

    def __init__(
        self,
        version: str = HOCKEY_MODEL_VERSION,
        league_constants: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.version = version
        self.league_constants = league_constants or LEAGUE_CONSTANTS

    def get_constants(self, league: str) -> dict[str, float]:
        norm = str(league or "").strip().upper()
        return self.league_constants.get(norm, DEFAULT_CONSTANTS)

    def _strengths(
        self,
        history: Sequence[Any],
        league: str | None = None,
    ) -> tuple[dict[str, dict[str, float]], float]:
        """Compute point-in-time EWMA attack/defense strengths per team."""
        norm_lg = str(league or "").strip().upper() if league else None
        by_team: dict[str, dict[str, list[float]]] = {}
        all_goals: list[float] = []

        filtered = [
            g
            for g in history
            if not norm_lg
            or norm_lg in {"HOCKEY", "UNKNOWN"}
            or str(getattr(g, "league", "")).strip().upper() == norm_lg
        ]
        # Fallback to entire hockey history if league sample is small
        if len(filtered) < 20 and len(history) >= 20:
            filtered = list(history)

        for game in sorted(filtered, key=lambda item: getattr(item, "start", "")):
            try:
                a_score = float(getattr(game, "away_score", 0))
                h_score = float(getattr(game, "home_score", 0))
                a_team = str(getattr(game, "away_team", ""))
                h_team = str(getattr(game, "home_team", ""))
            except (AttributeError, TypeError, ValueError):
                continue
            if not a_team or not h_team:
                continue
            all_goals.extend([a_score, h_score])
            for team, scored, allowed in ((a_team, a_score, h_score), (h_team, h_score, a_score)):
                entry = by_team.setdefault(team, {"scored": [], "allowed": []})
                entry["scored"].append(scored)
                entry["allowed"].append(allowed)

        consts = self.get_constants(league or "NHL")
        baseline = sum(all_goals) / len(all_goals) if all_goals else consts["baseline"]

        strengths: dict[str, dict[str, float]] = {}
        for team, entry in by_team.items():
            scored_series = entry["scored"]
            allowed_series = entry["allowed"]
            strengths[team] = {
                "attack": ewm_level(scored_series, 10.0, baseline, 8.0) / baseline,
                "defense": ewm_level(allowed_series, 10.0, baseline, 8.0) / baseline,
                "games": float(len(scored_series)),
            }
        return strengths, baseline

    def score_matrix(
        self,
        home_rate: float,
        away_rate: float,
        rho: float = -0.05,
    ) -> list[list[float]]:
        """Calculate bivariate Poisson score grid with Dixon-Coles adjustment."""
        matrix: list[list[float]] = []
        for h in range(MAX_GOALS + 1):
            row: list[float] = []
            for a in range(MAX_GOALS + 1):
                prob = (
                    _poisson_pmf(home_rate, h)
                    * _poisson_pmf(away_rate, a)
                    * _dc_adjustment(h, a, home_rate, away_rate, rho=rho)
                )
                row.append(max(0.0, prob))
            matrix.append(row)
        total = sum(sum(r) for r in matrix)
        if total > 0:
            return [[cell / total for cell in r] for r in matrix]
        return [[1.0 / ((MAX_GOALS + 1) ** 2) for _ in r] for r in matrix]

    def predict_games(
        self,
        history: Sequence[Any],
        upcoming: Sequence[UpcomingHockeyMatch],
    ) -> list[GamePrediction]:
        """Generate point-in-time calibrated predictions across all hockey markets."""
        league_cache: dict[str, tuple[dict[str, dict[str, float]], float]] = {}

        def _get_league_strengths(lg: str) -> tuple[dict[str, dict[str, float]], float]:
            norm = str(lg or "NHL").strip().upper()
            if norm not in league_cache:
                league_cache[norm] = self._strengths(history, norm)
            return league_cache[norm]

        predictions: list[GamePrediction] = []
        for match in upcoming:
            lg_name = str(match.league or "NHL").strip().upper()
            consts = self.get_constants(lg_name)
            strengths, baseline = _get_league_strengths(lg_name)

            home = strengths.get(match.home_team, {"attack": 1.0, "defense": 1.0, "games": 0.0})
            away = strengths.get(match.away_team, {"attack": 1.0, "defense": 1.0, "games": 0.0})

            home_boost = consts["home_boost"]
            rho = consts["rho"]
            ot_home_edge = consts["ot_home_edge"]

            home_rate = baseline * home["attack"] * away["defense"] * home_boost
            away_rate = baseline * away["attack"] * home["defense"]
            # Enforce reasonable goal expectations
            home_rate = max(1.2, min(5.5, home_rate))
            away_rate = max(1.0, min(5.2, away_rate))

            matrix = self.score_matrix(home_rate, away_rate, rho=rho)

            # Regulation probabilities
            home_reg_win = sum(matrix[h][a] for h in range(MAX_GOALS + 1) for a in range(h))
            away_reg_win = sum(matrix[h][a] for a in range(MAX_GOALS + 1) for h in range(a))
            draw_reg = sum(matrix[d][d] for d in range(MAX_GOALS + 1))

            # Overtime / Shootout win probability:
            # Polymarket settles hockey_team_full_game_winner as a 2-way market including OT
            relative_strength = home_rate / (home_rate + away_rate) if (home_rate + away_rate) > 0 else 0.5
            p_ot_home = 0.5 * relative_strength + 0.5 * ot_home_edge
            p_ot_away = 1.0 - p_ot_home

            home_ml = home_reg_win + draw_reg * p_ot_home
            away_ml = away_reg_win + draw_reg * p_ot_away
            total_ml = home_ml + away_ml
            if total_ml > 0:
                home_ml /= total_ml
                away_ml /= total_ml

            # Final goal-margin distribution (home minus away) including OT/SO: a
            # regulation tie is decided by exactly one goal, so its margin is +/-1.
            margin_pmf: dict[int, float] = {}
            for h in range(MAX_GOALS + 1):
                for a in range(MAX_GOALS + 1):
                    if h == a:
                        margin_pmf[1] = margin_pmf.get(1, 0.0) + matrix[h][a] * p_ot_home
                        margin_pmf[-1] = margin_pmf.get(-1, 0.0) + matrix[h][a] * p_ot_away
                    else:
                        margin_pmf[h - a] = margin_pmf.get(h - a, 0.0) + matrix[h][a]
            margin_total = sum(margin_pmf.values())

            # Uncertainty calculation based on team observation count
            sample = min(home["games"], away["games"])
            uncertainty = max(0.04, min(0.20, 0.20 - 0.01 * sample))

            base: GamePredictionBase = {
                "event_id": match.event_id,
                "event_start_utc": match.event_start_utc,
                "league": lg_name,
                "away_team": match.away_team,
                "home_team": match.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.version,
                "feature_basis": {
                    "home_goal_rate": round(home_rate, 4),
                    "away_goal_rate": round(away_rate, 4),
                    "league_baseline_goals": round(baseline, 4),
                    "league": lg_name,
                    "draw_regulation_prob": round(draw_reg, 4),
                    "min_team_games": sample,
                },
            }

            rationale = (
                f"Poisson-DC hockey model ({lg_name}): expected {home_rate:.2f} (home) "
                f"vs {away_rate:.2f} (away) goals. 2-way ML: {home_ml:.1%} H / {away_ml:.1%} A."
            )

            # 1. Moneyline
            predictions.append(
                GamePrediction(
                    event_id=base["event_id"],
                    event_start_utc=base["event_start_utc"],
                    league=base["league"],
                    away_team=base["away_team"],
                    home_team=base["home_team"],
                    uncertainty=base["uncertainty"],
                    model_version=base["model_version"],
                    feature_basis=base["feature_basis"],
                    market_type="moneyline",
                    line=None,
                    probabilities={
                        "home": round(home_ml, 6),
                        "away": round(away_ml, 6),
                        "draw": round(draw_reg, 6),
                    },
                    rationale=rationale,
                )
            )

            # 2. Spread: ``line`` is the HOME team's handicap and ``home`` the probability
            # that home + line beats away; ``away`` is the complementary away cover.
            for spread_line in SPREAD_LINES:
                home_cover = sum(p for m, p in margin_pmf.items() if m + spread_line > 0) / margin_total
                predictions.append(
                    GamePrediction(
                        event_id=base["event_id"],
                        event_start_utc=base["event_start_utc"],
                        league=base["league"],
                        away_team=base["away_team"],
                        home_team=base["home_team"],
                        uncertainty=base["uncertainty"],
                        model_version=base["model_version"],
                        feature_basis=base["feature_basis"],
                        market_type="spread",
                        line=spread_line,
                        probabilities={
                            "home": round(home_cover, 6),
                            "away": round(1.0 - home_cover, 6),
                        },
                        rationale=f"Home {spread_line:+.1f} goals cover p={home_cover:.1%}",
                    )
                )

            # 3. Totals for lines 5.5, 6.0, 6.5 (including OT / shootout settlement)
            # In regulation, if h == a (tied game), exactly 1 game-winning goal is awarded in OT/SO,
            # so the realized game total is (h + a + 1). If h != a, the realized total is (h + a).
            for line_val in TOTAL_LINES:
                prob_over = sum(
                    matrix[h][a]
                    for h in range(MAX_GOALS + 1)
                    for a in range(MAX_GOALS + 1)
                    if ((h + a + 1) if h == a else (h + a)) > line_val
                )
                prob_under = sum(
                    matrix[h][a]
                    for h in range(MAX_GOALS + 1)
                    for a in range(MAX_GOALS + 1)
                    if ((h + a + 1) if h == a else (h + a)) < line_val
                )
                prob_push = sum(
                    matrix[h][a]
                    for h in range(MAX_GOALS + 1)
                    for a in range(MAX_GOALS + 1)
                    if ((h + a + 1) if h == a else (h + a)) == line_val
                )
                eff_over = prob_over + 0.5 * prob_push
                eff_under = prob_under + 0.5 * prob_push
                predictions.append(
                    GamePrediction(
                        event_id=base["event_id"],
                        event_start_utc=base["event_start_utc"],
                        league=base["league"],
                        away_team=base["away_team"],
                        home_team=base["home_team"],
                        uncertainty=base["uncertainty"],
                        model_version=base["model_version"],
                        feature_basis=base["feature_basis"],
                        market_type="total",
                        line=line_val,
                        probabilities={
                            "over": round(eff_over, 6),
                            "under": round(eff_under, 6),
                        },
                        rationale=f"O/U {line_val}: {eff_over:.1%} over",
                    )
                )

        return predictions


def hockey_model() -> HockeyModel:
    return HockeyModel()
