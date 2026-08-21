"""Parametrized per-league Poisson-Dixon-Coles model.

One class, not six copy-pasted ones -- the DIFFERENTIATING content per
league (baseline, home advantage, rho; per docs/RESEARCH_BACKLOG.md P2)
lives entirely in each league's own ``LeagueSoccerConfig`` instance
(epl.py, la_liga.py, ...), fit independently from that league's own
history only. The scoring math is identical across leagues by design --
that's the shared infrastructure the backlog's binding rule explicitly
allows ("share ... never predictive assumptions").

EWMA decay rate (halflife/prior_strength) is NOT independently fit per
league in this pass -- kept at the same defaults models.soccer.SoccerModel
already uses. That's a scoping decision (see scripts/soccer_league_split_fit.py's
docstring), not an oversight: baseline/home-advantage/rho are the
parameters this pass actually measured and validated; decay-rate tuning is
flagged as a follow-up, not silently assumed to be done.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..features.base import GameRecord
from ..features.trends import ewm_level
from .poisson_dc import matrix_btts, matrix_outcomes, matrix_over_under, platt_calibrate, score_matrix

DEFAULT_EWMA_HALFLIFE = 10.0
DEFAULT_EWMA_PRIOR_STRENGTH = 8.0
DEFAULT_MAX_GOALS = 10


@dataclass(frozen=True)
class LeagueSoccerConfig:
    league_code: str
    model_version: str
    baseline: float  # league's own average goals/team -- measured, not fit
    home_advantage: float  # home_goals_avg / away_goals_avg in this league's own data
    dc_rho: float  # grid-searched per league on validation, minimizing log-loss
    ewma_halflife: float = DEFAULT_EWMA_HALFLIFE
    ewma_prior_strength: float = DEFAULT_EWMA_PRIOR_STRENGTH
    max_goals: int = DEFAULT_MAX_GOALS
    btts_calibration_intercept: float | None = None
    btts_calibration_slope: float | None = None
    # Hierarchical shrinkage toward a global cross-league prior (theta =
    # w*theta_league + (1-w)*theta_global, w = n/(n+shrinkage_prior_games)).
    # None means "no shrinkage" -- appropriate for leagues with real sample
    # size (the named big-6 modules); other.py's thin-league fallback sets
    # this to blend team strengths toward the neutral 1.0 prior.
    shrinkage_prior_games: float | None = None
    global_baseline: float | None = None  # only used when shrinkage_prior_games is set


@dataclass(frozen=True)
class UpcomingLeagueMatch:
    event_id: str
    event_start_utc: str
    away_team: str
    home_team: str


class LeagueSoccerModel:
    def __init__(self, config: LeagueSoccerConfig) -> None:
        self.config = config
        self.version = config.model_version
        self.sport = "soccer"

    def _strengths(self, history: Sequence[GameRecord]) -> dict[str, dict[str, float]]:
        cfg = self.config
        league_history = [g for g in history if g.league == cfg.league_code]
        by_team: dict[str, dict[str, list[float]]] = {}
        for game in sorted(league_history, key=lambda item: item.start):
            for team, scored, allowed in (
                (game.away_team, game.away_score, game.home_score),
                (game.home_team, game.home_score, game.away_score),
            ):
                entry = by_team.setdefault(team, {"scored": [], "allowed": []})
                entry["scored"].append(float(scored))
                entry["allowed"].append(float(allowed))
        strengths = {}
        for team, entry in by_team.items():
            n = len(entry["scored"])
            attack = (
                ewm_level(entry["scored"], cfg.ewma_halflife, cfg.baseline, cfg.ewma_prior_strength)
                / cfg.baseline
            )
            defense = (
                ewm_level(entry["allowed"], cfg.ewma_halflife, cfg.baseline, cfg.ewma_prior_strength)
                / cfg.baseline
            )
            if cfg.shrinkage_prior_games and cfg.global_baseline:
                w = n / (n + cfg.shrinkage_prior_games)
                attack = w * attack + (1 - w) * 1.0
                defense = w * defense + (1 - w) * 1.0
            strengths[team] = {"attack": attack, "defense": defense, "games": float(n)}
        return strengths

    def predict_one(
        self, strengths: dict[str, dict[str, float]], home_team: str, away_team: str
    ) -> dict[str, float | list[list[float]]]:
        cfg = self.config
        home = strengths.get(home_team, {"attack": 1.0, "defense": 1.0, "games": 0.0})
        away = strengths.get(away_team, {"attack": 1.0, "defense": 1.0, "games": 0.0})
        home_rate = cfg.baseline * home["attack"] * away["defense"] * cfg.home_advantage
        away_rate = cfg.baseline * away["attack"] * home["defense"] / cfg.home_advantage
        matrix = score_matrix(home_rate, away_rate, cfg.dc_rho, cfg.max_goals)
        home_win, away_win, draw = matrix_outcomes(matrix)
        over25 = matrix_over_under(matrix, 2.5)
        raw_btts = matrix_btts(matrix)
        if cfg.btts_calibration_intercept is not None and cfg.btts_calibration_slope is not None:
            btts = platt_calibrate(raw_btts, cfg.btts_calibration_intercept, cfg.btts_calibration_slope)
        else:
            btts = raw_btts
        return {
            "home_win": home_win,
            "away_win": away_win,
            "draw": draw,
            "over_2_5": over25,
            "btts": btts,
            "home_rate": home_rate,
            "away_rate": away_rate,
            "min_team_games": min(home["games"], away["games"]),
        }
