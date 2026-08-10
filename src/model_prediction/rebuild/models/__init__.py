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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet
from sklearn.preprocessing import StandardScaler


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    return math.exp(x) / (1.0 + math.exp(x))


def _neutralize_always_missing_columns(X: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Replace every value in a column flagged True in `mask` with a
    neutral 0.0 constant.

    Real bug caught by live verification against the actual current
    backfilled dataset (see outputs/rebuild/takeover_status.md Task 5),
    not a hypothetical: weather is currently unavailable for every real
    historical game (Task 3's fix), so temp_f_first_pitch is 100% NaN
    across the whole real training set. StandardScaler.fit_transform()
    on an all-NaN column silently produces an all-NaN mean/variance, and
    HistGradientBoostingRegressor's binning step then raised
    `ValueError: window shape cannot be larger than input array shape`
    trying to find split thresholds among zero real distinct values.
    Separately, SimpleImputer(strategy="mean") *drops* an all-NaN column
    from its output entirely (confirmed live) rather than erroring --
    which would have silently shifted every later column's position out
    of alignment with the model's own stored feature-name list.

    A feature never once observed in the training window carries no real
    signal for that fit either way (0.0 vs. dropped vs. left NaN all mean
    "the model learned nothing from this column") -- 0.0 is simply the
    choice that keeps the feature matrix's shape and column identity
    stable for both heads, matching what every other real observed value
    is scaled/imputed relative to."""
    if not mask.any():
        return X
    X = X.copy()
    X[:, mask] = 0.0
    return X


def _low_variance_columns(X: np.ndarray) -> np.ndarray:
    """True for any column with fewer than 2 distinct real (non-NaN)
    values.

    Real second bug caught by live verification (BootstrapMLBEnsemble,
    see outputs/rebuild/takeover_status.md Task 16): the all-NaN case
    above is real, but a *bootstrap resample* can trigger the identical
    HistGradientBoostingRegressor binning crash even for a column that is
    perfectly fine in the full real training set -- sampling with
    replacement can, by real chance, draw only one distinct real value
    for a naturally low-cardinality feature (e.g. `park_factor`, which
    only takes ~30 real distinct values across MLB parks) even though
    zero values are missing. `np.all(np.isnan(X))` alone does not catch
    this; checking real distinct-value count does."""
    mask = np.zeros(X.shape[1], dtype=bool)
    for j in range(X.shape[1]):
        col = X[:, j]
        distinct = np.unique(col[~np.isnan(col)])
        mask[j] = len(distinct) < 2
    return mask


# ── Run-Intensity Head (predicts total scoring environment) ──────────────────


class RunIntensityHead:
    """Predicts expected total runs in the game from both lineups, starters,
    bullpens, park, weather, and league environment."""

    def __init__(self, alpha: float = 0.1) -> None:
        self.model: HistGradientBoostingRegressor | None = None
        self.scaler = StandardScaler()
        self.alpha = alpha
        self._feature_names: list[str] = []
        self._always_missing_mask: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list[str] | None = None) -> RunIntensityHead:
        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]
        X = np.asarray(X, dtype=float)
        self._always_missing_mask = _low_variance_columns(X)
        X = _neutralize_always_missing_columns(X, self._always_missing_mask)
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
        X = np.asarray(X, dtype=float)
        if self._always_missing_mask is not None:
            X = _neutralize_always_missing_columns(X, self._always_missing_mask)
        return self.model.predict(self.scaler.transform(X))


# ── Run-Differential Head (predicts which team owns run advantage) ───────────


class RunDifferentialHead:
    """Predicts home run advantage from lineup, starter, bullpen differentials,
    defense, handedness matchup, and home field.

    Real gap fixed here (see outputs/rebuild/takeover_status.md Task 5):
    ElasticNet has no native NaN support (confirmed live: raises
    ValueError on any missing value) unlike RunIntensityHead's
    HistGradientBoostingRegressor, which handles NaN natively. Since
    mlb_features.py's rolling/weather feature builders now return NaN
    (not an apparently-real 0.0) for a continuous statistic with no real
    prior history, this head needs its own real imputation step, not a
    shared assumption that "whatever RunIntensityHead does is fine here
    too." Uses a mean imputer fit only on training data (never
    prediction-time data, which would be leakage-prone and inconsistent
    across calls) -- the paired "missingness indicator" half of CLAUDE.md's
    "imputed value + missingness indicator must be paired" requirement is
    the real, named `*_availability` columns already present in the row
    and included directly in DIFFERENTIAL_FEATURES by every real caller,
    not an anonymous auto-generated indicator column."""

    def __init__(self, alpha: float = 0.1) -> None:
        self.model = ElasticNet(alpha=alpha, l1_ratio=0.5, max_iter=5000, random_state=42)
        self.imputer = SimpleImputer(strategy="mean")
        self.scaler = StandardScaler()
        self._feature_names: list[str] = []
        self._always_missing_mask: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list[str] | None = None) -> RunDifferentialHead:
        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]
        X = np.asarray(X, dtype=float)
        # SimpleImputer(strategy="mean") silently *drops* an all-NaN
        # column from its output rather than erroring (confirmed live) --
        # neutralizing first keeps every column present and the feature
        # matrix's width/order aligned with self._feature_names.
        self._always_missing_mask = _low_variance_columns(X)
        X = _neutralize_always_missing_columns(X, self._always_missing_mask)
        X_imputed = self.imputer.fit_transform(X)
        X_scaled = self.scaler.fit_transform(X_imputed)
        self.model.fit(X_scaled, y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("RunDifferentialHead not fitted")
        X = np.asarray(X, dtype=float)
        if self._always_missing_mask is not None:
            X = _neutralize_always_missing_columns(X, self._always_missing_mask)
        return self.model.predict(self.scaler.transform(self.imputer.transform(X)))


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

    def total_market_breakdown(self, pred: GamePrediction, line: float) -> dict[str, float]:
        """Real over/under/push probabilities for one total line, in one
        simulation call.

        probability_for_market()'s own "total" branch always splits push
        mass 50/50 into both the over and under prices (matching real
        market convention for a tradeable quote), so over+under sum to
        1.0 regardless of push probability and the real push mass can
        never be recovered from those two numbers alone. This method
        exposes it directly -- needed for Task 13.5's real totals
        diagnostic, which must report push probability explicitly, not
        silently fold it into over/under."""
        away_exp = pred.away_expected_runs
        home_exp = pred.home_expected_runs
        away_scores, home_scores = self._simulate_scores(away_exp, home_exp)
        totals = away_scores + home_scores
        over = float((totals > line).sum()) / self.n_sim
        under = float((totals < line).sum()) / self.n_sim
        push = float((totals == line).sum()) / self.n_sim
        return {
            "over": over + 0.5 * push,
            "under": under + 0.5 * push,
            "over_win": over,
            "under_win": under,
            "push": push,
        }

    def spread_market_breakdown(self, pred: GamePrediction, home_line: float) -> dict[str, float]:
        """Real home/away/push probabilities for one signed run line, in one
        simulation call.

        Same real bug as total_market_breakdown() but for spread:
        probability_for_market()'s "spread" branch always splits push mass
        50/50 into home and away (matching real market convention for a
        tradeable quote), so over+under -- home+away here -- sum to 1.0
        regardless of push probability and push can never be recovered from
        those two numbers alone. This method exposes it directly.

        home_line is the signed line applied to the home team's margin
        (e.g. home_line=-1.5 prices "home -1.5"; home_line=+1.5 prices
        "home +1.5"). The away side is the mirrored line (home_line=-1.5
        implies away +1.5)."""
        away_exp = pred.away_expected_runs
        home_exp = pred.home_expected_runs
        away_scores, home_scores = self._simulate_scores(away_exp, home_exp)
        home_margin = home_scores - away_scores + home_line
        home_win = float((home_margin > 0).sum()) / self.n_sim
        away_win = float((home_margin < 0).sum()) / self.n_sim
        push = float((home_margin == 0).sum()) / self.n_sim
        return {
            "home": home_win + 0.5 * push,
            "away": away_win + 0.5 * push,
            "home_win": home_win,
            "away_win": away_win,
            "push": push,
        }


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

    def __init__(self, seed: int = 42, method: str = "independent_poisson") -> None:
        self.intensity_head = RunIntensityHead()
        self.differential_head = RunDifferentialHead()
        # `method` lets a real caller compare distribution families
        # (independent_poisson/negative_binomial/skellam) on the identical
        # two fitted heads -- the same expected-run heads can feed
        # different score distributions (CLAUDE.md Part 2 SS12's "The same
        # expected-run heads can feed different score distributions").
        self.distribution = JointScoreDistribution(method=method, seed=seed)
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
        X_intensity = np.array([[row.get(f, float("nan")) for f in self._intensity_features]])
        X_diff = np.array([[row.get(f, float("nan")) for f in self._differential_features]])
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
            "intensity_always_missing_mask": self.intensity_head._always_missing_mask,
            "differential_model": self.differential_head.model,
            "differential_scaler": self.differential_head.scaler,
            "differential_imputer": self.differential_head.imputer,
            "differential_feature_names": self.differential_head._feature_names,
            "differential_always_missing_mask": self.differential_head._always_missing_mask,
            "distribution_method": self.distribution.method,
            "distribution_n_sim": self.distribution.n_sim,
            "distribution_seed": self.distribution.seed,
        }, out / "model.joblib")
        metadata = self.to_artifact()
        metadata["bundle_sha256"] = hashlib.sha256((out / "model.joblib").read_bytes()).hexdigest()
        (out / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))

    @classmethod
    def load(cls, path: str | Path) -> MLBTwoHeadModel:
        """Load a bundle written by save(). Reconstructs both heads and the
        distribution config exactly — round-trip-tested to produce
        identical predictions to the original in-memory model, not just
        "loads without error"."""
        import joblib

        src = Path(path)
        metadata = json.loads((src / "metadata.json").read_text())
        expected_hash = metadata.get("artifact_hash")
        hash_payload = {
            key: value for key, value in metadata.items() if key not in {"artifact_hash", "bundle_sha256"}
        }
        actual_hash = hashlib.sha256(json.dumps(hash_payload, sort_keys=True).encode()).hexdigest()
        if not expected_hash or expected_hash != actual_hash:
            raise ValueError("model artifact metadata hash mismatch")
        bundle_hash = hashlib.sha256((src / "model.joblib").read_bytes()).hexdigest()
        if not metadata.get("bundle_sha256") or metadata["bundle_sha256"] != bundle_hash:
            raise ValueError("model artifact bundle hash mismatch")
        bundle = joblib.load(src / "model.joblib")

        model = cls()
        model.intensity_head.model = bundle["intensity_model"]
        model.intensity_head.scaler = bundle["intensity_scaler"]
        model.intensity_head._feature_names = bundle["intensity_feature_names"]
        model.intensity_head._always_missing_mask = bundle.get("intensity_always_missing_mask")
        model.differential_head.model = bundle["differential_model"]
        model.differential_head.scaler = bundle["differential_scaler"]
        model.differential_head.imputer = bundle["differential_imputer"]
        model.differential_head._feature_names = bundle["differential_feature_names"]
        model.differential_head._always_missing_mask = bundle.get("differential_always_missing_mask")
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


# ── Coherent XGBoost score-distribution challenger ───────────────────────────
#
# CLAUDE.md's next-phase Task 13: the direct XGBoost binary classifier
# (XGBoostChallenger, xgboost_stress.py) is a useful independent moneyline
# challenger on its own, but it has no joint score distribution behind it --
# nothing stops it from silently driving spread/total probabilities that
# contradict what a real score model would say, which CLAUDE.md's own
# architecture section forbids ("No disconnected classifier may silently
# contradict the joint score distribution"). XGBoostRunHead/
# XGBoostTwoHeadModel below are a *coherent* XGBoost-based challenger built
# the same way MLBTwoHeadModel is -- two expected-run regression heads
# feeding the identical JointScoreDistribution reconciliation -- so
# moneyline/spread/total all derive from one real joint distribution either
# way; only which regressor produces the two expected-run numbers differs.


class XGBoostRunHead:
    """XGBoost regression head -- predicts either total-run intensity or
    home-run differential, reused for both roles (unlike RunIntensityHead/
    RunDifferentialHead, which need different sklearn estimators because
    ElasticNet has no native NaN support). XGBoost handles the real NaN
    values mlb_features.py now produces for missing continuous stats
    (Task 5) natively -- no imputation or always-missing-column
    neutralization needed here, unlike RunDifferentialHead."""

    def __init__(self, seed: int = 42) -> None:
        self.model: Any = None
        self._feature_names: list[str] = []

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: list[str] | None = None, seed: int = 42) -> XGBoostRunHead:
        import xgboost as xgb

        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]
        self.model = xgb.XGBRegressor(
            objective="reg:squarederror",
            max_depth=3, learning_rate=0.05, n_estimators=200,
            min_child_weight=5, subsample=0.85, colsample_bytree=0.8,
            reg_alpha=1.0, reg_lambda=2.0,
            random_state=seed, n_jobs=2, verbosity=0,
        )
        self.model.fit(np.asarray(X, dtype=float), y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("XGBoostRunHead not fitted")
        return self.model.predict(np.asarray(X, dtype=float))


class XGBoostTwoHeadModel:
    """Coherent XGBoost score-distribution model: XGBoost intensity head +
    XGBoost differential head -> the same JointScoreDistribution
    reconciliation MLBTwoHeadModel uses. Same real predict_row()/
    probability() interface as MLBTwoHeadModel so a real comparison script
    can swap one for the other without duplicating fold/OOF logic.

    Real, disclosed scope: unlike MLBTwoHeadModel, this does not yet
    implement save()/load() artifact persistence -- it exists for real
    chronological OOF comparison (its actual current purpose), not as a
    deployable artifact. Add persistence if/when this challenger is
    promoted past comparison.
    """

    MODEL_VERSION = "mlb-xgboost-two-head-v1"

    def __init__(self, seed: int = 42, method: str = "independent_poisson") -> None:
        self.seed = seed
        self.intensity_head = XGBoostRunHead()
        self.differential_head = XGBoostRunHead()
        self.distribution = JointScoreDistribution(method=method, seed=seed)
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
    ) -> XGBoostTwoHeadModel:
        self._intensity_features = intensity_features
        self._differential_features = differential_features

        X_intensity = data.select(intensity_features).to_numpy()
        y_intensity = data[total_runs_col].to_numpy()
        X_diff = data.select(differential_features).to_numpy()
        y_diff = data[home_margin_col].to_numpy()

        self.intensity_head.fit(X_intensity, y_intensity, intensity_features, seed=self.seed)
        self.differential_head.fit(X_diff, y_diff, differential_features, seed=self.seed)
        self._fitted = True
        return self

    def predict_row(self, event_id: str, row: dict[str, float]) -> GamePrediction:
        if not self._fitted:
            raise RuntimeError("XGBoostTwoHeadModel not fitted")
        X_intensity = np.array([[row.get(f, float("nan")) for f in self._intensity_features]])
        X_diff = np.array([[row.get(f, float("nan")) for f in self._differential_features]])
        total = float(self.intensity_head.predict(X_intensity)[0])
        margin = float(self.differential_head.predict(X_diff)[0])
        # Same real clamp MLBTwoHeadModel.predict_row() uses -- a
        # regression head (XGBoost included) can predict a non-positive
        # total, which the Poisson/NB simulation below can't accept as an
        # expected-run rate.
        return self.distribution.predict_game(event_id, max(1.0, total), margin)

    def probability(self, pred: GamePrediction, market_type: str, selection: str, line: float | None = None) -> float:
        return self.distribution.probability_for_market(pred, market_type, selection, line)

    def to_artifact(self) -> dict[str, Any]:
        """Mirrors MLBTwoHeadModel.to_artifact() field-for-field (same real
        consumers: shadow_ledger.record_model_artifact(), the live
        pipeline's forecast.model_artifact_hash) so the live pipeline can
        swap model families without a second, differently-shaped artifact
        schema."""
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
        """Real, loadable artifact bundle -- same real gap MLBTwoHeadModel's
        save()/load() closed (FOUNDATION_COMPLETION.md Phase 8), for the
        XGBoost head family: until this, XGBoostTwoHeadModel could only be
        exercised inside one comparison-script process, never persisted for
        live inference to load without a full retrain.

        Writes `path/model.joblib` (both fitted XGBRegressor boosters +
        distribution config) and `path/metadata.json` (feature names,
        version, artifact hash)."""
        import joblib

        if not self._fitted:
            raise RuntimeError("cannot save an unfitted model")
        out = Path(path)
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "intensity_model": self.intensity_head.model,
            "intensity_feature_names": self.intensity_head._feature_names,
            "differential_model": self.differential_head.model,
            "differential_feature_names": self.differential_head._feature_names,
            "distribution_method": self.distribution.method,
            "distribution_n_sim": self.distribution.n_sim,
            "distribution_seed": self.distribution.seed,
            "seed": self.seed,
        }, out / "model.joblib")
        metadata = self.to_artifact()
        metadata["bundle_sha256"] = hashlib.sha256((out / "model.joblib").read_bytes()).hexdigest()
        (out / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))

    @classmethod
    def load(cls, path: str | Path) -> XGBoostTwoHeadModel:
        """Load a bundle written by save(). Round-trip-tested (see
        test_mlb_model_persistence.py) to produce identical predictions to
        the original in-memory model."""
        import json

        import joblib

        src = Path(path)
        metadata = json.loads((src / "metadata.json").read_text())
        expected_hash = metadata.get("artifact_hash")
        hash_payload = {
            key: value for key, value in metadata.items() if key not in {"artifact_hash", "bundle_sha256"}
        }
        actual_hash = hashlib.sha256(json.dumps(hash_payload, sort_keys=True).encode()).hexdigest()
        if not expected_hash or expected_hash != actual_hash:
            raise ValueError("model artifact metadata hash mismatch")
        bundle_hash = hashlib.sha256((src / "model.joblib").read_bytes()).hexdigest()
        if not metadata.get("bundle_sha256") or metadata["bundle_sha256"] != bundle_hash:
            raise ValueError("model artifact bundle hash mismatch")
        bundle = joblib.load(src / "model.joblib")

        model = cls(seed=bundle.get("seed", 42), method=bundle["distribution_method"])
        model.intensity_head.model = bundle["intensity_model"]
        model.intensity_head._feature_names = bundle["intensity_feature_names"]
        model.differential_head.model = bundle["differential_model"]
        model.differential_head._feature_names = bundle["differential_feature_names"]
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

    `head_family` (multi-sport execution spec MLB-1/MLB-5): the live model
    switched from sklearn (ElasticNet/HistGradientBoosting) heads to
    XGBoost heads (the frozen mlb_moneyline_v2 combination), but this class
    was hardcoded to sklearn heads only -- bootstrapping the wrong head
    family would silently measure the uncertainty of a model that isn't
    actually running live. `"xgboost"` fits `XGBoostRunHead` replicates
    instead, matching whichever family the primary model actually uses.
    """

    def __init__(self, n_bootstrap: int = 20, seed: int = 42, head_family: str = "sklearn") -> None:
        self.n_bootstrap = n_bootstrap
        self.seed = seed
        self.head_family = head_family
        self._replicates: list[tuple[Any, Any]] = []
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
            if self.head_family == "xgboost":
                ih_x: Any = XGBoostRunHead().fit(X_int, y_int, intensity_features, seed=self.seed)
                dh_x: Any = XGBoostRunHead().fit(X_diff, y_diff, differential_features, seed=self.seed)
                self._replicates.append((ih_x, dh_x))
            else:
                ih_s: Any = RunIntensityHead().fit(X_int, y_int, intensity_features)
                dh_s: Any = RunDifferentialHead().fit(X_diff, y_diff, differential_features)
                self._replicates.append((ih_s, dh_s))
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
            x_int = np.array([[row.get(f, float("nan")) for f in self._intensity_features]])
            x_diff = np.array([[row.get(f, float("nan")) for f in self._differential_features]])
            total = max(1.0, float(ih.predict(x_int)[0]))
            margin = float(dh.predict(x_diff)[0])
            pred = distribution.predict_game(str(row.get("event_id", "bootstrap")), total, margin)
            probs.append(distribution.probability_for_market(pred, market_type, selection, line))
        probs_arr = np.array(probs)
        lower = float(np.quantile(probs_arr, lower_quantile))
        upper = float(np.quantile(probs_arr, 1.0 - lower_quantile))
        return lower, upper

    def save(self, path: str | Path) -> None:
        """Persist every fitted bootstrap replicate as one hash-bound bundle.

        The frozen prospective candidate must restore the exact uncertainty
        distribution in a new process.  Persisting only the primary model (the
        former resume behavior) changes lower bounds and is therefore not an
        equivalent resume.
        """
        import joblib

        if not self.fitted:
            raise RuntimeError("cannot save an unfitted bootstrap ensemble")
        out = Path(path)
        out.mkdir(parents=True, exist_ok=True)
        payload = {
            "replicates": self._replicates,
            "n_bootstrap": self.n_bootstrap,
            "seed": self.seed,
            "head_family": self.head_family,
            "intensity_features": self._intensity_features,
            "differential_features": self._differential_features,
        }
        bundle_path = out / "bootstrap.joblib"
        joblib.dump(payload, bundle_path)
        metadata = {
            "n_bootstrap": self.n_bootstrap,
            "seed": self.seed,
            "head_family": self.head_family,
            "intensity_features": self._intensity_features,
            "differential_features": self._differential_features,
            "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        }
        identity = {key: value for key, value in metadata.items() if key != "metadata_sha256"}
        metadata["metadata_sha256"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()
        (out / "metadata.json").write_text(json.dumps(metadata, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> BootstrapMLBEnsemble:
        """Load a bundle written by :meth:`save`, verifying bytes first."""
        import joblib

        src = Path(path)
        metadata = json.loads((src / "metadata.json").read_text())
        expected_metadata_hash = metadata.get("metadata_sha256")
        identity = {key: value for key, value in metadata.items() if key != "metadata_sha256"}
        actual_metadata_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if not expected_metadata_hash or expected_metadata_hash != actual_metadata_hash:
            raise ValueError("bootstrap metadata hash mismatch")
        bundle_path = src / "bootstrap.joblib"
        bundle_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        if not metadata.get("bundle_sha256") or metadata["bundle_sha256"] != bundle_hash:
            raise ValueError("bootstrap bundle hash mismatch")
        payload = joblib.load(bundle_path)
        model = cls(
            n_bootstrap=int(payload["n_bootstrap"]),
            seed=int(payload["seed"]),
            head_family=str(payload["head_family"]),
        )
        model._replicates = payload["replicates"]
        model._intensity_features = list(payload["intensity_features"])
        model._differential_features = list(payload["differential_features"])
        if not model.fitted:
            raise ValueError("bootstrap bundle contains no fitted replicates")
        return model
