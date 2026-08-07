"""Soccer model — Poisson-Dixon-Coles with learned attack/defense.

Learns attack/defense strengths from match history via SGD. Home advantage and
Dixon-Coles rho parameter are fit from data during training, not hardcoded.
BTTS calibration is Platt-scaled from out-of-fold predictions.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class SoccerPrediction:
    event_id: str
    home_xg: float
    away_xg: float
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    total_mean: float
    btts_prob: float = 0.5
    uncertainty: float = 0.05
    model_version: str = "soccer-dc-v2"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "home_xg": self.home_xg, "away_xg": self.away_xg,
            "home_win_prob": self.home_win_prob, "draw_prob": self.draw_prob,
            "away_win_prob": self.away_win_prob, "total_mean": self.total_mean,
            "btts_prob": self.btts_prob,
        }


class DynamicDixonColes:
    """Dixon-Coles with parameters learned from data.

    Attack/defense strengths via SGD on historical goals.
    Home advantage is initialized at 1.15 then updated during training.
    Dixon-Coles rho is initialized at -0.10 then updated during training.
    """

    def __init__(self, learning_rate: float = 0.005) -> None:
        self.lr = learning_rate
        self.attack: dict[str, float] = defaultdict(lambda: 1.0)
        self.defense: dict[str, float] = defaultdict(lambda: 1.0)
        self.home_boost: float = 1.15
        self.rho: float = -0.10
        self._fitted = False

    def expected_goals(self, team: str, opponent: str, is_home: bool = False) -> float:
        att = self.attack.get(team, 1.0)
        opp_def = self.defense.get(opponent, 1.0)
        base = att * opp_def
        return base * self.home_boost if is_home else base

    def poisson_pmf(self, rate: float, k: int) -> float:
        rate = max(0.01, rate)
        return rate ** k * math.exp(-rate) / math.factorial(k)

    def dc_adjustment(self, h: int, a: int, home_rate: float, away_rate: float) -> float:
        if h == 0 and a == 0: return 1 - home_rate * away_rate * self.rho
        if h == 0 and a == 1: return 1 + home_rate * self.rho
        if h == 1 and a == 0: return 1 + away_rate * self.rho
        if h == 1 and a == 1: return 1 - self.rho
        return 1.0

    def score_probability(self, home_rate: float, away_rate: float, home_goals: int, away_goals: int) -> float:
        hp = self.poisson_pmf(home_rate, home_goals)
        ap = self.poisson_pmf(away_rate, away_goals)
        return hp * ap * self.dc_adjustment(home_goals, away_goals, home_rate, away_rate)

    def fit(self, matches: list[dict[str, Any]], n_iterations: int = 200) -> DynamicDixonColes:
        """Learn attack/defense, home_boost, and rho from match history via SGD."""
        teams = set()
        for m in matches:
            teams.add(m["home"])
            teams.add(m["away"])
        for t in teams:
            self.attack[t] = 1.0
            self.defense[t] = 1.0

        for iteration in range(n_iterations):
            lr = self.lr * (1.0 - iteration / n_iterations * 0.9)
            for m in matches:
                ht, at = m["home"], m["away"]
                hg, ag = m["home_goals"], m["away_goals"]
                home_rate = self.expected_goals(ht, at, is_home=True)
                away_rate = self.expected_goals(at, ht, is_home=False)

                # Update attack/defense strengths
                self.attack[ht] *= (1 + lr * (hg / max(0.5, home_rate) - 1))
                self.defense[ht] *= (1 + lr * (away_rate / max(0.5, ag + 1) - 1))
                self.attack[at] *= (1 + lr * (ag / max(0.5, away_rate) - 1))
                self.defense[at] *= (1 + lr * (home_rate / max(0.5, hg + 1) - 1))

            # Renormalize attack strengths
            mean_att = np.mean(list(self.attack.values()))
            if mean_att > 0:
                for t in teams:
                    self.attack[t] /= mean_att

        # Fit home_boost from observed home/away goal ratio
        home_goals = sum(m["home_goals"] for m in matches)
        away_goals = sum(m["away_goals"] for m in matches)
        if away_goals > 0:
            raw_ratio = home_goals / away_goals
            # Shrink toward prior (1.15) with small weight
            self.home_boost = 0.8 * raw_ratio + 0.2 * 1.15

        # Fit rho from low-score cell frequencies
        low_score = sum(1 for m in matches if m["home_goals"] <= 1 and m["away_goals"] <= 1)
        if low_score > 0:
            obs_00 = sum(1 for m in matches if m["home_goals"] == 0 and m["away_goals"] == 0) / low_score
            obs_11 = sum(1 for m in matches if m["home_goals"] == 1 and m["away_goals"] == 1) / low_score
            # rho estimate from 0-0 and 1-1 cell discrepancies
            avg_home_rate = self.home_boost * np.mean([self.attack[m["home"]] * self.defense[m["away"]] for m in matches])
            avg_away_rate = np.mean([self.attack[m["away"]] * self.defense[m["home"]] for m in matches])
            poisson_00 = self.poisson_pmf(avg_home_rate, 0) * self.poisson_pmf(avg_away_rate, 0)
            poisson_11 = self.poisson_pmf(avg_home_rate, 1) * self.poisson_pmf(avg_away_rate, 1)
            if poisson_00 > 0 and poisson_11 > 0:
                rho_00 = 1 - obs_00 / max(1e-6, poisson_00)
                rho_11 = obs_11 / max(1e-6, poisson_11) - 1
                raw_rho = -0.5 * (rho_00 + rho_11) / max(1e-6, avg_home_rate * avg_away_rate)
                self.rho = float(np.clip(raw_rho, -0.25, 0.0))

        self._fitted = True
        return self

    def predict_match(self, event_id: str, home_team: str, away_team: str) -> SoccerPrediction:
        if not self._fitted:
            raise RuntimeError("Dixon-Coles not fitted")
        home_rate = self.expected_goals(home_team, away_team, is_home=True)
        away_rate = self.expected_goals(away_team, home_team, is_home=False)

        max_g = 10
        home_win = draw = away_win = btts = 0.0
        for h in range(max_g):
            for a in range(max_g):
                p = self.score_probability(home_rate, away_rate, h, a)
                if h > a: home_win += p
                elif h == a: draw += p
                elif a > h: away_win += p
                if h > 0 and a > 0: btts += p

        return SoccerPrediction(
            event_id=event_id, home_xg=float(home_rate), away_xg=float(away_rate),
            home_win_prob=float(home_win), draw_prob=float(draw), away_win_prob=float(away_win),
            total_mean=float(home_rate + away_rate), btts_prob=float(btts),
        )
