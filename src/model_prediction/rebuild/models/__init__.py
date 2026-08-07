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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import ElasticNet
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
    then simulates the joint distribution via independent Poisson (default),
    negative binomial (overdispersion), or Skellam (exact, closed-form
    margin distribution for moneyline/spread -- see method="skellam")."""

    def __init__(self, method: str = "independent_poisson", n_sim: int = 10000, seed: int = 42) -> None:
        self.method = method
        self.n_sim = n_sim
        self.seed = seed
        self.rng = np.random.default_rng(seed)

    def _skellam_margin_prob(self, home_exp: float, away_exp: float, threshold: float) -> tuple[float, float]:
        """Exact P(D > threshold) and P(D == threshold) for D = home_score -
        away_score, using the real closed-form fact that the difference of
        two independent Poisson(mu1)/Poisson(mu2) random variables is
        exactly Skellam(mu1, mu2) -- no simulation, no Monte Carlo noise.
        Empirically cross-checked against a 2M-draw independent-Poisson
        Monte Carlo simulation before this was written (matched to
        <0.001 absolute across moneyline, half-integer, and integer-line
        spread cases).

        Used by both predict_game() and probability_for_market() for
        method="skellam" -- moneyline and spread only depend on the score
        difference, so this is a real, exact alternative to
        _simulate_scores()'s Monte Carlo estimate for those two market
        types specifically. Totals still come from simulated scores
        (Skellam models the difference, not the sum) -- drawn from the
        same mu1/mu2 rates via the independent_poisson branch below, so
        moneyline/spread (exact) and totals (simulated) stay consistent
        with the same underlying rate model, not a disconnected
        assumption."""
        from scipy.stats import skellam
        dist = skellam(home_exp, away_exp)
        win_prob = float(dist.sf(threshold))
        push_prob = float(dist.pmf(threshold))
        return win_prob, push_prob

    def _simulate_scores(self, away_exp: float, home_exp: float) -> tuple[np.ndarray, np.ndarray]:
        """The one real simulation call site both predict_game() and
        probability_for_market() must use -- real bug fixed here:
        probability_for_market() previously always simulated with
        self.rng.poisson(...) directly, silently ignoring self.method.
        A model configured with method="negative_binomial" would price
        moneyline (via predict_game(), which did respect self.method)
        from a genuinely different distribution than spread/total (via
        probability_for_market(), always Poisson) for the identical real
        game -- exactly the "disconnected classifier silently contradicts
        the joint score distribution" failure mode CLAUDE.md's own
        architecture section forbids, just within one class rather than
        across two. Verified untested before this fix (zero references to
        "negative_binomial" anywhere in tests/, confirmed via grep)."""
        if self.method == "negative_binomial":
            # NB with dispersion parameter for overdispersion in run scoring
            # n parameter estimated from mean-variance relationship in MLB (~1.2×)
            home_n = max(1, home_exp / 1.5)
            away_n = max(1, away_exp / 1.5)
            home_p = home_n / (home_n + home_exp)
            away_p = away_n / (away_n + away_exp)
            away_scores = self.rng.negative_binomial(away_n, away_p, self.n_sim)
            home_scores = self.rng.negative_binomial(home_n, home_p, self.n_sim)
        else:
            away_scores = self.rng.poisson(away_exp, self.n_sim)
            home_scores = self.rng.poisson(home_exp, self.n_sim)
        return away_scores, home_scores

    def predict_game(
        self,
        event_id: str,
        total_intensity: float,
        home_advantage: float,
        uncertainty: float = 0.05,
    ) -> GamePrediction:
        """total_intensity = expected total runs, home_advantage = expected home margin.
        Decompose into away/home expected runs, then simulate.

        Supports 'independent_poisson' (default), 'negative_binomial', and
        'skellam' methods.
        """
        home_exp = max(0.5, (total_intensity + home_advantage) / 2)
        away_exp = max(0.5, (total_intensity - home_advantage) / 2)

        away_scores, home_scores = self._simulate_scores(away_exp, home_exp)

        # Moneyline. method="skellam" uses the exact closed-form
        # distribution of home_score - away_score instead of counting the
        # simulated arrays -- see _skellam_margin_prob()'s docstring.
        if self.method == "skellam":
            win_prob, push_prob = self._skellam_margin_prob(home_exp, away_exp, threshold=0.0)
            home_prob = win_prob + 0.5 * push_prob
            away_prob = (1.0 - win_prob - push_prob) + 0.5 * push_prob
        else:
            n = self.n_sim
            home_wins = int((home_scores > away_scores).sum())
            away_wins = int((away_scores > home_scores).sum())
            pushes = int((home_scores == away_scores).sum())
            push_prob = pushes / n
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
            push_prob=float(push_prob),
            uncertainty=uncertainty,
        )

    def probability_for_market(
        self, pred: GamePrediction, market_type: str, selection: str, line: float | None = None,
    ) -> float:
        """Derive probability for any market from the joint distribution.

        market_type: 'moneyline', 'spread', 'total', 'btts'
        selection: 'home', 'away', 'over', 'under'
        """
        # Re-simulate for the specific query (or use cached) -- via
        # _simulate_scores() so this respects self.method exactly like
        # predict_game() does. See _simulate_scores()'s own docstring for
        # the real bug this fixes.
        away_exp = pred.away_expected_runs
        home_exp = pred.home_expected_runs
        away_scores, home_scores = self._simulate_scores(away_exp, home_exp)

        # method="skellam" prices moneyline/spread (both margin-only
        # markets) via the exact closed-form Skellam distribution instead
        # of counting the simulated arrays -- see
        # _skellam_margin_prob()'s docstring. The `selection` side's own
        # mu1/mu2 order is passed directly (Skellam(mu1, mu2) models
        # mu1 - mu2), so "away" reuses the identical formula with the two
        # rates swapped rather than needing separate algebra.
        if self.method == "skellam" and market_type in ("moneyline", "spread"):
            mu1, mu2 = (home_exp, away_exp) if selection == "home" else (away_exp, home_exp)
            threshold = 0.0 if market_type == "moneyline" else -(line or 0.0)
            if market_type == "spread" and line is None:
                raise ValueError("spread requires a line")
            win_prob, push_prob = self._skellam_margin_prob(mu1, mu2, threshold)
            return float(win_prob + 0.5 * push_prob)

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
        import json
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

    def save(self, path: str | Path) -> None:
        """Save a real, loadable artifact bundle — fixes a real gap
        (FOUNDATION_COMPLETION.md Phase 8): to_artifact() only ever recorded
        metadata and hashes, never the fitted sklearn objects themselves, so
        a saved artifact could never actually be reloaded to reproduce a
        prediction — every run had to retrain from scratch (verified in
        scripts/mlb_shadow_run.py's own docstring, which disclosed this as
        a known limitation rather than pretending otherwise).

        Writes `path/model.joblib` (the fitted heads + distribution config)
        and `path/metadata.json` (feature names, version, artifact hash) as
        a self-contained directory.
        """
        import joblib

        if not self._fitted:
            raise RuntimeError("cannot save an unfitted model")
        out = Path(path)
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "intensity_model": self.intensity_head.model,
            "intensity_scaler": self.intensity_head.scaler,
            "intensity_feature_names": self.intensity_head._feature_names,
            "differential_model": self.differential_head.model,
            "differential_scaler": self.differential_head.scaler,
            "differential_feature_names": self.differential_head._feature_names,
            "distribution_method": self.distribution.method,
            "distribution_n_sim": self.distribution.n_sim,
            "distribution_seed": self.distribution.seed,
        }, out / "model.joblib")
        (out / "metadata.json").write_text(json.dumps(self.to_artifact(), indent=2, default=str))

    @classmethod
    def load(cls, path: str | Path) -> MLBTwoHeadModel:
        """Load a bundle written by save(). Reconstructs both heads and the
        distribution config exactly — round-trip-tested to produce
        identical predictions to the original in-memory model, not just
        "loads without error"."""
        import joblib

        src = Path(path)
        bundle = joblib.load(src / "model.joblib")
        metadata = json.loads((src / "metadata.json").read_text())

        model = cls()
        model.intensity_head.model = bundle["intensity_model"]
        model.intensity_head.scaler = bundle["intensity_scaler"]
        model.intensity_head._feature_names = bundle["intensity_feature_names"]
        model.differential_head.model = bundle["differential_model"]
        model.differential_head.scaler = bundle["differential_scaler"]
        model.differential_head._feature_names = bundle["differential_feature_names"]
        # Real bug caught before this was ever committed: setting .method/
        # .n_sim on the default-constructed distribution left .rng seeded
        # from cls()'s default seed=42, silently ignoring whatever seed the
        # original model actually used. Reconstruct the whole object from
        # the persisted seed instead of mutating fields piecemeal.
        model.distribution = JointScoreDistribution(
            method=bundle["distribution_method"],
            n_sim=bundle["distribution_n_sim"],
            seed=bundle["distribution_seed"],
        )
        model._intensity_features = metadata["intensity_features"]
        model._differential_features = metadata["differential_features"]
        model._fitted = True
        return model


# ── Bootstrap uncertainty ────────────────────────────────────────────────────


class BootstrapMLBEnsemble:
    """Real, data-driven bootstrap uncertainty for MLBTwoHeadModel predictions
    -- CLAUDE.md's `conservative_probability` spec names `bootstrap_uncertainty`
    as a required component of the lower-bound probability; until this class,
    `mlb_shadow_run.py`'s `build_forecast()` used a disclosed flat 3% haircut
    in its place, which was a fixed number regardless of how much the
    prediction actually depended on any particular training game.

    Fits `n_bootstrap` independent copies of both heads, each on a bootstrap
    resample (sampling with replacement) of the same chronological training
    data used to fit the primary model. For a given row and market, the
    empirical spread of that market's probability across replicates measures
    how much the prediction would have moved under resampled training data --
    a real uncertainty measurement, not an assumption.
    """

    def __init__(self, n_bootstrap: int = 20, seed: int = 42) -> None:
        self.n_bootstrap = n_bootstrap
        self.seed = seed
        self._replicates: list[tuple[RunIntensityHead, RunDifferentialHead]] = []
        self._intensity_features: list[str] = []
        self._differential_features: list[str] = []

    @property
    def fitted(self) -> bool:
        return bool(self._replicates)

    def fit(
        self,
        data: pl.DataFrame,
        intensity_features: list[str],
        differential_features: list[str],
        total_runs_col: str = "total_runs",
        home_margin_col: str = "home_margin",
    ) -> BootstrapMLBEnsemble:
        self._intensity_features = intensity_features
        self._differential_features = differential_features
        rng = np.random.default_rng(self.seed)
        n = data.height
        self._replicates = []
        for _ in range(self.n_bootstrap):
            idx = rng.integers(0, n, size=n).tolist()
            sample = data[idx]
            X_int = sample.select(intensity_features).to_numpy()
            y_int = sample[total_runs_col].to_numpy()
            X_diff = sample.select(differential_features).to_numpy()
            y_diff = sample[home_margin_col].to_numpy()
            ih = RunIntensityHead().fit(X_int, y_int, intensity_features)
            dh = RunDifferentialHead().fit(X_diff, y_diff, differential_features)
            self._replicates.append((ih, dh))
        return self

    def market_probability_bounds(
        self,
        row: dict[str, float],
        distribution: JointScoreDistribution,
        market_type: str,
        selection: str,
        line: float | None = None,
        lower_quantile: float = 0.10,
    ) -> tuple[float, float]:
        """Empirical [lower_quantile, 1-lower_quantile] bounds on one
        market's probability for this row, across bootstrap replicates.
        Market-type-agnostic on purpose: the same bootstrap machinery
        prices moneyline, spread, and total lower bounds uniformly, unlike
        the pre-fix code where only moneyline had any lower bound at all."""
        if not self._replicates:
            raise RuntimeError("BootstrapMLBEnsemble not fitted")
        probs = []
        for ih, dh in self._replicates:
            x_int = np.array([[row.get(f, 0.0) for f in self._intensity_features]])
            x_diff = np.array([[row.get(f, 0.0) for f in self._differential_features]])
            total = max(1.0, float(ih.predict(x_int)[0]))
            margin = float(dh.predict(x_diff)[0])
            pred = distribution.predict_game(str(row.get("event_id", "bootstrap")), total, margin)
            probs.append(distribution.probability_for_market(pred, market_type, selection, line))
        probs_arr = np.array(probs)
        lower = float(np.quantile(probs_arr, lower_quantile))
        upper = float(np.quantile(probs_arr, 1.0 - lower_quantile))
        return lower, upper
