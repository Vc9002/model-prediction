"""MLB two-head model — separate run-intensity and run-differential heads
reconciled into one coherent joint score distribution.

Moneyline, run line, total, push, and expected score all derive from the same
distribution. No disconnected classifier.

Architecture:
    RunIntensityHead -> predicts total scoring environment
    RunDifferentialHead -> predicts which team owns the run advantage
    JointScoreDistribution -> independent/bivariate Poisson/NB simulation
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import nbinom, poisson, skellam
from sklearn.linear_model import ElasticNet, LogisticRegression
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.preprocessing import StandardScaler


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    return math.exp(x) / (1.0 + math.exp(x))


# ── Run-Intensity Head (predicts total scoring environment) ──────────────────


class RunIntensityHead:
    """Predicts expected total runs in the game from both lineups, starters,
    bullpens, park, weather, and league environment."""

    def __init__(self, alpha: float = 0.1) -> None:
        self.model: HistGradientBoostingRegressor | None = None
        self.scaler = StandardScaler()
        self.alpha = alpha
        self._feature_names: list[str] = []

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list[str] | None = None) -> RunIntensityHead:
        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]
        X_scaled = self.scaler.fit_transform(X)
        self.model = HistGradientBoostingRegressor(
            max_iter=200, max_depth=4, learning_rate=0.05,
            min_samples_leaf=50, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.2,
            random_state=42,
        )
        self.model.fit(X_scaled, y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("RunIntensityHead not fitted")
        return self.model.predict(self.scaler.transform(X))


# ── Run-Differential Head (predicts which team owns run advantage) ───────────


class RunDifferentialHead:
    """Predicts home run advantage from lineup, starter, bullpen differentials,
    defense, handedness matchup, and home field."""

    def __init__(self, alpha: float = 0.1) -> None:
        self.model = ElasticNet(alpha=alpha, l1_ratio=0.5, max_iter=5000, random_state=42)
        self.scaler = StandardScaler()
        self._feature_names: list[str] = []

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list[str] | None = None) -> RunDifferentialHead:
        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled, y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("RunDifferentialHead not fitted")
        return self.model.predict(self.scaler.transform(X))


# ── Joint Score Distribution ─────────────────────────────────────────────────


@dataclass
class GamePrediction:
    """Coherent prediction for one MLB game derived from the joint distribution."""
    event_id: str
    home_expected_runs: float
    away_expected_runs: float
    home_win_prob: float
    away_win_prob: float
    total_mean: float
    total_std: float
    # Derived markets
    moneyline: dict[str, float] = field(default_factory=dict)   # {home, away}
    spreads: dict[float, dict[str, float]] = field(default_factory=dict)  # {line: {home, away}}
    totals: dict[float, dict[str, float]] = field(default_factory=dict)   # {line: {over, under}}
    push_prob: float = 0.0
    uncertainty: float = 0.05
    model_version: str = "mlb-two-head-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "home_expected_runs": self.home_expected_runs,
            "away_expected_runs": self.away_expected_runs,
            "home_win_prob": self.home_win_prob,
            "moneyline": self.moneyline,
            "total_mean": self.total_mean,
            "uncertainty": self.uncertainty,
            "model_version": self.model_version,
        }


class JointScoreDistribution:
    """Reconciles intensity and differential heads into away/home expected runs,
    then simulates the joint distribution via independent Poisson (default) or
    bivariate count models."""

    def __init__(self, method: str = "independent_poisson", n_sim: int = 10000, seed: int = 42) -> None:
        self.method = method
        self.n_sim = n_sim
        self.rng = np.random.default_rng(seed)

    def predict_game(
        self,
        event_id: str,
        total_intensity: float,
        home_advantage: float,
        uncertainty: float = 0.05,
    ) -> GamePrediction:
        """total_intensity = expected total runs, home_advantage = expected home margin.
        Decompose into away/home expected runs, then simulate.

        Supports 'independent_poisson' (default) and 'negative_binomial' methods.
        """
        home_exp = max(0.5, (total_intensity + home_advantage) / 2)
        away_exp = max(0.5, (total_intensity - home_advantage) / 2)

        if self.method == "negative_binomial":
            # NB with dispersion parameter for overdispersion in run scoring
            # n parameter estimated from mean-variance relationship in MLB (~1.2×)
            home_n = max(1, home_exp / 1.5)
            away_n = max(1, away_exp / 1.5)
            home_p = home_n / (home_n + home_exp)
            away_p = away_n / (away_n + away_exp)
            away_scores = self.rng.negative_binomial(home_n, home_p, self.n_sim)
            home_scores = self.rng.negative_binomial(away_n, away_p, self.n_sim)
        else:
            away_scores = self.rng.poisson(away_exp, self.n_sim)
            home_scores = self.rng.poisson(home_exp, self.n_sim)

        # Moneyline
        home_wins = int((home_scores > away_scores).sum())
        away_wins = int((away_scores > home_scores).sum())
        pushes = int((home_scores == away_scores).sum())
        n = self.n_sim
        home_prob = (home_wins + 0.5 * pushes) / n
        away_prob = (away_wins + 0.5 * pushes) / n

        # Totals
        totals = away_scores + home_scores

        return GamePrediction(
            event_id=event_id,
            home_expected_runs=float(home_exp),
            away_expected_runs=float(away_exp),
            home_win_prob=float(home_prob),
            away_win_prob=float(away_prob),
            total_mean=float(totals.mean()),
            total_std=float(totals.std()),
            moneyline={"home": float(home_prob), "away": float(away_prob)},
            push_prob=float(pushes / n),
            uncertainty=uncertainty,
        )

    def probability_for_market(
        self, pred: GamePrediction, market_type: str, selection: str, line: float | None = None,
    ) -> float:
        """Derive probability for any market from the joint distribution.

        market_type: 'moneyline', 'spread', 'total', 'btts'
        selection: 'home', 'away', 'over', 'under'
        """
        # Re-simulate for the specific query (or use cached)
        away_exp = pred.away_expected_runs
        home_exp = pred.home_expected_runs
        away_scores = self.rng.poisson(away_exp, self.n_sim)
        home_scores = self.rng.poisson(home_exp, self.n_sim)

        if market_type == "moneyline":
            if selection == "home":
                wins = (home_scores > away_scores).sum()
                pushes = (home_scores == away_scores).sum()
            else:
                wins = (away_scores > home_scores).sum()
                pushes = (away_scores == home_scores).sum()
            return float((wins + 0.5 * pushes) / self.n_sim)

        elif market_type == "spread":
            if line is None:
                raise ValueError("spread requires a line")
            if selection == "home":
                margin = home_scores - away_scores + line
            else:
                margin = away_scores - home_scores + line
            wins = (margin > 0).sum()
            pushes = (margin == 0).sum()
            return float((wins + 0.5 * pushes) / self.n_sim)

        elif market_type == "total":
            if line is None:
                raise ValueError("total requires a line")
            totals = away_scores + home_scores
            if selection == "over":
                wins = (totals > line).sum()
            else:
                wins = (totals < line).sum()
            pushes = (totals == line).sum()
            return float((wins + 0.5 * pushes) / self.n_sim)

        raise ValueError(f"unsupported market: {market_type}")


# ── Full MLB Model ───────────────────────────────────────────────────────────


class MLBTwoHeadModel:
    """Complete MLB model: intensity head + differential head -> joint distribution.

    Usage:
        model = MLBTwoHeadModel()
        model.fit(features_df)
        pred = model.predict_game(features_row)
        prob = model.probability(pred, 'moneyline', 'home')
    """

    MODEL_VERSION = "mlb-two-head-v1"

    def __init__(self, seed: int = 42) -> None:
        self.intensity_head = RunIntensityHead()
        self.differential_head = RunDifferentialHead()
        self.distribution = JointScoreDistribution(seed=seed)
        self._fitted = False
        self._intensity_features: list[str] = []
        self._differential_features: list[str] = []

    def fit(
        self,
        data: pl.DataFrame,
        intensity_features: list[str],
        differential_features: list[str],
        total_runs_col: str = "total_runs",
        home_margin_col: str = "home_margin",
    ) -> MLBTwoHeadModel:
        """Fit both heads on chronological training data."""
        self._intensity_features = intensity_features
        self._differential_features = differential_features

        X_intensity = data.select(intensity_features).to_numpy()
        y_intensity = data[total_runs_col].to_numpy()

        X_diff = data.select(differential_features).to_numpy()
        y_diff = data[home_margin_col].to_numpy()

        self.intensity_head.fit(X_intensity, y_intensity, intensity_features)
        self.differential_head.fit(X_diff, y_diff, differential_features)
        self._fitted = True
        return self

    def predict_row(self, event_id: str, row: dict[str, float]) -> GamePrediction:
        if not self._fitted:
            raise RuntimeError("Model not fitted")
        X_intensity = np.array([[row.get(f, 0.0) for f in self._intensity_features]])
        X_diff = np.array([[row.get(f, 0.0) for f in self._differential_features]])
        total = float(self.intensity_head.predict(X_intensity)[0])
        margin = float(self.differential_head.predict(X_diff)[0])
        return self.distribution.predict_game(event_id, max(1.0, total), margin)

    def probability(self, pred: GamePrediction, market_type: str, selection: str, line: float | None = None) -> float:
        return self.distribution.probability_for_market(pred, market_type, selection, line)

    def to_artifact(self) -> dict[str, Any]:
        import hashlib, json
        raw = {
            "model_id": self.MODEL_VERSION,
            "method": self.distribution.method,
            "intensity_features": self._intensity_features,
            "differential_features": self._differential_features,
            "fitted": self._fitted,
        }
        artifact_hash = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        raw["artifact_hash"] = artifact_hash
        return raw
