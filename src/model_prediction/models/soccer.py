"""Unified soccer model: independent Poisson goal model with a Dixon-Coles-style
low-score correction. Markets: three-way ML, O/U 2.5, BTTS.

Attack/defense strengths come from the soccer_form feature (EWMA goals for and
against, shrunk toward league mean). Market prices never enter this model.
Research state until validated through the backtester.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import exp, log

from ..features.base import GameRecord
from ..features.trends import ewm_level
from .base import GamePrediction, GamePredictionBase

SOCCER_MODEL_VERSION = "soccer-poisson-dc-v1"
HOME_GOAL_BOOST = 1.15
DC_RHO = -0.10  # Dixon-Coles low-score dependence parameter
MAX_GOALS = 10
# BTTS calibration (added 2026-07-31): the raw joint-matrix BTTS probability
# is measurably overconfident on real data -- checked directly, not assumed,
# since BTTS is a much harder derived quantity from this same score matrix
# than moneyline/totals (it depends on the joint tail of both teams'
# distributions, not just their marginal win/total behavior, and the DC low-
# score correction above is tuned for the 0-0/1-0/0-1/1-1 cells specifically,
# not this derived probability). Real walk-forward check: moneyline/totals
# from this identical model score ~62.5%/well-calibrated; raw BTTS only hit
# 55.0% with meaningfully overconfident buckets (e.g. predicted 72% actually
# hit 59%). Fit via Platt scaling (logistic regression of real outcome on
# logit(raw_probability)) on the validation cohort only, then checked on the
# separate locked holdout: accuracy improved 55.0% -> 56.7%, and holdout
# calibration buckets came back close to the diagonal (e.g. 55.3% predicted
# vs 55.96% actual, 62.2% vs 65.95%). Still real but weak signal -- BTTS
# stays research-only regardless of any future promotion decision for
# moneyline/totals from the same model.
BTTS_CALIBRATION_INTERCEPT = 0.1393
BTTS_CALIBRATION_SLOPE = 0.4205


@dataclass(frozen=True)
class UpcomingMatch:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str
    league: str = "SOCCER"


def _apply_btts_calibration(raw_probability: float) -> float:
    """Platt-scale the raw joint-matrix BTTS probability -- see
    BTTS_CALIBRATION_INTERCEPT/SLOPE's module-level comment for the real
    fit and holdout evidence."""
    clipped = min(1 - 1e-9, max(1e-9, raw_probability))
    logit = log(clipped / (1 - clipped))
    calibrated = BTTS_CALIBRATION_INTERCEPT + BTTS_CALIBRATION_SLOPE * logit
    return 1 / (1 + exp(-calibrated))


def _poisson_pmf(rate: float, k: int) -> float:
    result = exp(-rate)
    for i in range(1, k + 1):
        result *= rate / i
    return result


def _dc_adjustment(home_goals: int, away_goals: int, home_rate: float, away_rate: float) -> float:
    """Dixon-Coles tau for the four low-score cells."""
    if home_goals == 0 and away_goals == 0:
        return 1 - home_rate * away_rate * DC_RHO
    if home_goals == 0 and away_goals == 1:
        return 1 + home_rate * DC_RHO
    if home_goals == 1 and away_goals == 0:
        return 1 + away_rate * DC_RHO
    if home_goals == 1 and away_goals == 1:
        return 1 - DC_RHO
    return 1.0


class SoccerModel:
    version = SOCCER_MODEL_VERSION
    sport = "soccer"

    def _strengths(self, history: Sequence[GameRecord]) -> tuple[dict[str, dict[str, float]], float]:
        by_team: dict[str, dict[str, list[float]]] = {}
        goals: list[float] = []
        for game in sorted(history, key=lambda item: item.start):
            goals.extend([float(game.away_score), float(game.home_score)])
            for team, scored, allowed in (
                (game.away_team, game.away_score, game.home_score),
                (game.home_team, game.home_score, game.away_score),
            ):
                entry = by_team.setdefault(team, {"scored": [], "allowed": []})
                entry["scored"].append(float(scored))
                entry["allowed"].append(float(allowed))
        baseline = sum(goals) / len(goals) if goals else 1.35
        strengths = {}
        for team, entry in by_team.items():
            strengths[team] = {
                "attack": ewm_level(entry["scored"], 10.0, baseline, 8.0) / baseline,
                "defense": ewm_level(entry["allowed"], 10.0, baseline, 8.0) / baseline,
                "games": float(len(entry["scored"])),
            }
        return strengths, baseline

    def score_matrix(
        self, home_rate: float, away_rate: float
    ) -> list[list[float]]:
        matrix = []
        for h in range(MAX_GOALS + 1):
            row = []
            for a in range(MAX_GOALS + 1):
                probability = (
                    _poisson_pmf(home_rate, h)
                    * _poisson_pmf(away_rate, a)
                    * _dc_adjustment(h, a, home_rate, away_rate)
                )
                row.append(max(probability, 0.0))
            matrix.append(row)
        total = sum(sum(row) for row in matrix)
        return [[cell / total for cell in row] for row in matrix]

    def predict_games(
        self,
        history: Sequence[GameRecord],
        upcoming: Sequence[UpcomingMatch],
    ) -> list[GamePrediction]:
        strengths, baseline = self._strengths(history)
        predictions: list[GamePrediction] = []
        for match in upcoming:
            home = strengths.get(match.home_team, {"attack": 1.0, "defense": 1.0, "games": 0.0})
            away = strengths.get(match.away_team, {"attack": 1.0, "defense": 1.0, "games": 0.0})
            home_rate = baseline * home["attack"] * away["defense"] * HOME_GOAL_BOOST
            away_rate = baseline * away["attack"] * home["defense"] / HOME_GOAL_BOOST
            matrix = self.score_matrix(home_rate, away_rate)
            home_win = sum(matrix[h][a] for h in range(MAX_GOALS + 1) for a in range(h))
            away_win = sum(matrix[h][a] for a in range(MAX_GOALS + 1) for h in range(a))
            draw = 1 - home_win - away_win
            over25 = sum(
                matrix[h][a]
                for h in range(MAX_GOALS + 1)
                for a in range(MAX_GOALS + 1)
                if h + a > 2.5
            )
            raw_btts = sum(
                matrix[h][a] for h in range(1, MAX_GOALS + 1) for a in range(1, MAX_GOALS + 1)
            )
            btts = _apply_btts_calibration(raw_btts)
            sample = min(home["games"], away["games"])
            uncertainty = max(0.04, min(0.20, 0.20 - 0.01 * sample))
            base: GamePredictionBase = {
                "event_id": match.event_id,
                "event_start_utc": match.event_start_utc,
                "league": match.league,
                "away_team": match.away_team,
                "home_team": match.home_team,
                "uncertainty": round(uncertainty, 4),
                "model_version": self.version,
                "feature_basis": {
                    "home_goal_rate": round(home_rate, 4),
                    "away_goal_rate": round(away_rate, 4),
                    "league_goals_per_team": round(baseline, 4),
                    "history_games": len(history),
                },
            }
            rationale = (
                f"Poisson-DC rates {home_rate:.2f} (home) / {away_rate:.2f} (away) "
                f"from EWMA attack/defense strengths."
            )
            predictions.append(
                GamePrediction(
                    market_type="moneyline",
                    line=None,
                    probabilities={
                        "home": round(home_win, 6),
                        "away": round(away_win, 6),
                        "draw": round(draw, 6),
                    },
                    rationale=rationale,
                    **base,
                )
            )
            predictions.append(
                GamePrediction(
                    market_type="total",
                    line=2.5,
                    probabilities={"over": round(over25, 6), "under": round(1 - over25, 6)},
                    rationale=rationale,
                    **base,
                )
            )
            predictions.append(
                GamePrediction(
                    market_type="btts",
                    line=None,
                    probabilities={"yes": round(btts, 6), "no": round(1 - btts, 6)},
                    rationale=rationale,
                    **base,
                )
            )
        return predictions


def soccer_model() -> SoccerModel:
    return SoccerModel()
