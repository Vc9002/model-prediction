"""Tests for MLBTwoHeadModel.save()/load() (FOUNDATION_COMPLETION.md Phase 8).

Real gap fixed: to_artifact() only ever recorded metadata and hashes, never
the fitted sklearn objects — a saved artifact could never actually be
reloaded to reproduce a prediction, so every real run had to retrain from
scratch (scripts/mlb_shadow_run.py's own docstring disclosed this as a
known limitation). Verified against a real model trained on real Statcast
data before writing these deterministic unit tests (see
outputs/rebuild/takeover_status.md).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from model_prediction.rebuild.models import JointScoreDistribution, MLBTwoHeadModel, XGBoostTwoHeadModel

INTENSITY_FEATURES = ["f1", "f2"]
DIFFERENTIAL_FEATURES = ["g1", "g2"]


def test_hash_invalid_model_bundle_fails_before_deserialization(tmp_path):
    bundle_path = tmp_path / "model.joblib"
    bundle_path.write_bytes(b"not a trusted model bundle")
    payload = {
        "model_id": "mlb-two-head-v1",
        "method": "poisson",
        "intensity_features": ["f1"],
        "differential_features": ["g1"],
        "fitted": True,
    }
    payload["artifact_hash"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    payload["bundle_sha256"] = "0" * 64
    (tmp_path / "metadata.json").write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="bundle hash mismatch"):
        MLBTwoHeadModel.load(tmp_path)


def _synthetic_training_data(n: int = 40, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    f1 = rng.uniform(3, 6, n)
    f2 = rng.uniform(3, 6, n)
    g1 = rng.uniform(-2, 2, n)
    g2 = rng.uniform(-1, 1, n)
    total_runs = f1 + f2 + rng.normal(0, 0.5, n)
    home_margin = g1 + g2 + rng.normal(0, 0.5, n)
    return pl.DataFrame(
        {
            "f1": f1,
            "f2": f2,
            "g1": g1,
            "g2": g2,
            "total_runs": total_runs,
            "home_margin": home_margin,
        }
    )


class TestSeedIsIntrospectable:
    def test_distribution_stores_its_own_seed(self):
        # Real bug caught before ever being committed: JointScoreDistribution
        # never stored the seed it was constructed with, so save()/load()
        # had no way to know what seed to restore — load() silently used
        # the class default instead of the model's real seed.
        dist = JointScoreDistribution(seed=777)
        assert dist.seed == 777


class TestNegativeBinomialMethodConsistency:
    """Real bug fixed: probability_for_market() previously always
    simulated with self.rng.poisson(...) directly, ignoring self.method --
    a model configured with method="negative_binomial" priced moneyline
    (via predict_game(), which did respect self.method) from a genuinely
    different distribution than spread/total (via probability_for_market(),
    always Poisson) for the identical real game. Both now route through
    the one shared _simulate_scores() helper."""

    def test_probability_for_market_uses_negative_binomial_when_configured(self):
        dist = JointScoreDistribution(method="negative_binomial", n_sim=500, seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=0.5)

        # If probability_for_market() fell back to Poisson, calling the
        # poisson sampler would succeed silently and the bug would pass
        # undetected -- instead, swap in a fake rng whose .poisson() raises,
        # proving the negative-binomial path is what actually executes.
        # numpy's real Generator is a C type and doesn't allow monkeypatching
        # individual bound methods, so the whole rng is replaced.
        class _PoissonForbiddenRng:
            def __init__(self, real_rng):
                self._real = real_rng

            def poisson(self, *args, **kwargs):
                raise AssertionError(
                    "probability_for_market() called rng.poisson() despite method='negative_binomial'"
                )

            def negative_binomial(self, *args, **kwargs):
                return self._real.negative_binomial(*args, **kwargs)

        dist.rng = _PoissonForbiddenRng(np.random.default_rng(1))  # type: ignore[assignment]

        prob = dist.probability_for_market(pred, "total", "over", line=8.5)
        assert 0.0 <= prob <= 1.0

    def test_predict_game_and_probability_for_market_agree_on_moneyline(self):
        # Both paths must derive the same real moneyline probability from
        # the identical configured method -- recomputing moneyline via
        # probability_for_market() should closely match predict_game()'s
        # own value (small Monte Carlo noise aside, both draw from the
        # same distribution family and expected values).
        dist = JointScoreDistribution(method="negative_binomial", n_sim=20000, seed=2)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=1.0)

        recomputed_home_prob = dist.probability_for_market(pred, "moneyline", "home")

        assert abs(recomputed_home_prob - pred.home_win_prob) < 0.05


class TestSkellamMethod:
    """method="skellam" prices moneyline/spread via the exact closed-form
    fact that the difference of two independent Poisson random variables
    is exactly Skellam(mu1, mu2) -- no simulation, no Monte Carlo noise.
    Cross-checked against a real large-sample independent-Poisson Monte
    Carlo simulation (not just internal self-consistency) before being
    wired into predict_game()/probability_for_market()."""

    def test_moneyline_matches_a_real_independent_poisson_monte_carlo(self):
        home_exp, away_exp = 4.5, 4.0
        rng = np.random.default_rng(0)
        n = 2_000_000
        home_mc = rng.poisson(home_exp, n)
        away_mc = rng.poisson(away_exp, n)
        mc_home_prob = ((home_mc > away_mc).sum() + 0.5 * (home_mc == away_mc).sum()) / n

        dist = JointScoreDistribution(method="skellam", seed=1)
        pred = dist.predict_game(
            "g1", total_intensity=home_exp + away_exp, home_advantage=home_exp - away_exp
        )

        assert abs(pred.home_win_prob - mc_home_prob) < 0.002

    def test_spread_matches_a_real_independent_poisson_monte_carlo(self):
        home_exp, away_exp = 5.0, 3.5
        rng = np.random.default_rng(0)
        n = 2_000_000
        home_mc = rng.poisson(home_exp, n)
        away_mc = rng.poisson(away_exp, n)
        line = -1.5
        margin = home_mc - away_mc + line
        mc_home_cover = ((margin > 0).sum() + 0.5 * (margin == 0).sum()) / n

        dist = JointScoreDistribution(method="skellam", seed=1)
        pred = dist.predict_game(
            "g1", total_intensity=home_exp + away_exp, home_advantage=home_exp - away_exp
        )
        cover_prob = dist.probability_for_market(pred, "spread", "home", line=line)

        assert abs(cover_prob - mc_home_cover) < 0.002

    def test_integer_line_push_probability_matches_monte_carlo(self):
        # Integer spread/moneyline lines have a real, nonzero push
        # probability -- the case a naive half-integer-only implementation
        # would get wrong.
        home_exp, away_exp = 4.0, 4.0
        rng = np.random.default_rng(0)
        n = 2_000_000
        home_mc = rng.poisson(home_exp, n)
        away_mc = rng.poisson(away_exp, n)
        mc_push = (home_mc == away_mc).mean()

        dist = JointScoreDistribution(method="skellam", seed=1)
        _, push_prob = dist._skellam_margin_prob(home_exp, away_exp, threshold=0.0)

        assert abs(push_prob - mc_push) < 0.002

    def test_home_and_away_probabilities_sum_to_one(self):
        dist = JointScoreDistribution(method="skellam", seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=0.5)
        assert pred.home_win_prob + pred.away_win_prob == pytest.approx(1.0, abs=1e-9)

    def test_moneyline_via_probability_for_market_matches_predict_game(self):
        dist = JointScoreDistribution(method="skellam", seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=1.0)

        recomputed = dist.probability_for_market(pred, "moneyline", "home")

        # Exact, not approximate -- both paths use the identical closed-form
        # computation, so there's no Monte Carlo noise to allow for.
        assert recomputed == pytest.approx(pred.home_win_prob, abs=1e-12)

    def test_totals_still_come_from_simulated_scores_not_fabricated(self):
        # Skellam models the score difference, not the sum -- totals must
        # still come from real simulated scores (independent Poisson, the
        # same underlying rates), not a value skipped or guessed.
        dist = JointScoreDistribution(method="skellam", n_sim=20000, seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=0.5)
        over_prob = dist.probability_for_market(pred, "total", "over", line=8.5)
        assert 0.0 < over_prob < 1.0


class TestTotalMarketBreakdown:
    """Task 13.5: probability_for_market()'s "total" branch always splits
    push mass 50/50 into both over and under (real market convention), so
    over+under always sum to 1.0 regardless of real push probability --
    it can never be recovered from those two numbers alone.
    total_market_breakdown() exposes push directly, in one simulation
    call."""

    def test_over_under_and_push_sum_to_one(self):
        dist = JointScoreDistribution(n_sim=20000, seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=0.5)
        breakdown = dist.total_market_breakdown(pred, line=9.0)
        assert breakdown["over"] + breakdown["under"] == pytest.approx(1.0, abs=1e-9)
        assert breakdown["over_win"] + breakdown["under_win"] + breakdown["push"] == pytest.approx(1.0)

    def test_half_integer_line_has_zero_real_push_probability(self):
        # MLB total runs are always integers -- a half-integer line can
        # never push in reality, and the simulation (integer-valued
        # Poisson/NB draws) must reflect that.
        dist = JointScoreDistribution(n_sim=20000, seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=0.5)
        breakdown = dist.total_market_breakdown(pred, line=8.5)
        assert breakdown["push"] == 0.0

    def test_whole_integer_line_has_real_nonzero_push_probability(self):
        dist = JointScoreDistribution(n_sim=20000, seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=0.5)
        breakdown = dist.total_market_breakdown(pred, line=9.0)
        assert breakdown["push"] > 0.0

    def test_matches_probability_for_market_for_over_and_under(self):
        # The two independently-computed probabilities (one via a fresh
        # simulation each, one via the shared breakdown) must agree --
        # both derive from the identical distribution and method.
        dist = JointScoreDistribution(n_sim=50000, seed=7)
        pred = dist.predict_game("g1", total_intensity=8.5, home_advantage=-0.3)
        breakdown = dist.total_market_breakdown(pred, line=8.5)
        over_direct = dist.probability_for_market(pred, "total", "over", line=8.5)
        under_direct = dist.probability_for_market(pred, "total", "under", line=8.5)
        assert breakdown["over"] == pytest.approx(over_direct, abs=0.02)
        assert breakdown["under"] == pytest.approx(under_direct, abs=0.02)


class TestSpreadMarketBreakdown:
    """Task 17: the same real push-folding bug as TestTotalMarketBreakdown,
    but for spread -- probability_for_market()'s "spread" branch always
    splits push mass 50/50 into home and away, so home+away always sum to
    1.0 regardless of real push probability. spread_market_breakdown()
    exposes push directly, in one simulation call."""

    def test_home_away_and_push_sum_to_one(self):
        dist = JointScoreDistribution(n_sim=20000, seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=0.5)
        breakdown = dist.spread_market_breakdown(pred, home_line=-1.5)
        assert breakdown["home"] + breakdown["away"] == pytest.approx(1.0, abs=1e-9)
        assert breakdown["home_win"] + breakdown["away_win"] + breakdown["push"] == pytest.approx(1.0)

    def test_half_integer_line_has_zero_real_push_probability(self):
        # MLB run margins are always integers -- a half-integer line can
        # never push in reality.
        dist = JointScoreDistribution(n_sim=20000, seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=0.5)
        breakdown = dist.spread_market_breakdown(pred, home_line=-1.5)
        assert breakdown["push"] == 0.0

    def test_whole_integer_line_has_real_nonzero_push_probability(self):
        dist = JointScoreDistribution(n_sim=20000, seed=1)
        pred = dist.predict_game("g1", total_intensity=9.0, home_advantage=0.5)
        breakdown = dist.spread_market_breakdown(pred, home_line=0.0)
        assert breakdown["push"] > 0.0

    def test_matches_probability_for_market_for_home_and_away(self):
        dist = JointScoreDistribution(n_sim=50000, seed=7)
        pred = dist.predict_game("g1", total_intensity=8.5, home_advantage=-0.3)
        breakdown = dist.spread_market_breakdown(pred, home_line=-1.5)
        home_direct = dist.probability_for_market(pred, "spread", "home", line=-1.5)
        away_direct = dist.probability_for_market(pred, "spread", "away", line=1.5)
        assert breakdown["home"] == pytest.approx(home_direct, abs=0.02)
        assert breakdown["away"] == pytest.approx(away_direct, abs=0.02)


class TestModelSaveLoadRoundTrip:
    def test_deterministic_predictions_match_exactly_after_reload(self):
        data = _synthetic_training_data()
        model = MLBTwoHeadModel(seed=42)
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        row = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2}

        pred_before = model.predict_row("e1", row)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = MLBTwoHeadModel.load(tmp)
            pred_after = loaded.predict_row("e1", row)

        assert pred_after.home_expected_runs == pred_before.home_expected_runs
        assert pred_after.away_expected_runs == pred_before.away_expected_runs

    def test_non_default_seed_is_preserved_not_silently_reset(self):
        """The regression this whole test class exists for: a save()/load()
        that silently used the default seed=42 regardless of what the
        original model was actually constructed with would still "work"
        (no exception) but would quietly diverge — the failure mode is
        silent drift, not a crash, which is exactly the kind of bug this
        session has spent most of its time hunting."""
        data = _synthetic_training_data()
        model = MLBTwoHeadModel(seed=777)
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        row = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2}

        pred_before = model.predict_row("e1", row)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = MLBTwoHeadModel.load(tmp)

        assert loaded.distribution.seed == 777
        pred_after = loaded.predict_row("e1", row)
        # With the real seed correctly restored, even the Monte Carlo
        # simulation output matches exactly (both are the first
        # predict_game() call on a freshly-seeded distribution).
        assert pred_after.home_win_prob == pred_before.home_win_prob

    def test_loaded_model_is_marked_fitted(self):
        data = _synthetic_training_data()
        model = MLBTwoHeadModel(seed=1)
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = MLBTwoHeadModel.load(tmp)

        assert loaded._fitted is True
        loaded.predict_row("e1", {"f1": 4.0, "f2": 4.0, "g1": 0.0, "g2": 0.0})  # must not raise

    def test_feature_names_are_preserved(self):
        data = _synthetic_training_data()
        model = MLBTwoHeadModel(seed=1)
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = MLBTwoHeadModel.load(tmp)

        assert loaded._intensity_features == INTENSITY_FEATURES
        assert loaded._differential_features == DIFFERENTIAL_FEATURES

    def test_distribution_method_is_configurable_and_preserved_through_reload(self):
        # Task 12: a real caller needs to compare distribution families
        # (independent_poisson/negative_binomial/skellam) on the
        # identical two fitted heads -- MLBTwoHeadModel.__init__() didn't
        # expose `method` at all before, hardcoding independent_poisson.
        data = _synthetic_training_data()
        model = MLBTwoHeadModel(seed=1, method="skellam")
        assert model.distribution.method == "skellam"
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = MLBTwoHeadModel.load(tmp)

        assert loaded.distribution.method == "skellam"

    def test_save_writes_a_real_metadata_json(self):
        data = _synthetic_training_data()
        model = MLBTwoHeadModel(seed=1)
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            assert (Path(tmp) / "model.joblib").exists()
            assert (Path(tmp) / "metadata.json").exists()

    def test_saving_an_unfitted_model_raises(self):
        model = MLBTwoHeadModel(seed=1)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                model.save(tmp)
                raised = False
            except RuntimeError:
                raised = True
            assert raised, "saving an unfitted model must fail loudly, not silently write a broken bundle"


class TestXGBoostTwoHeadModelSaveLoadRoundTrip:
    """MLB-3 (multi-sport execution spec): XGBoostTwoHeadModel previously had
    no save()/load() at all -- every live use had to retrain from scratch.
    Mirrors TestModelSaveLoadRoundTrip's real assertions for the sklearn
    head family, applied to the XGBoost head family the live pipeline is
    being switched to."""

    def test_deterministic_predictions_match_exactly_after_reload(self):
        data = _synthetic_training_data()
        model = XGBoostTwoHeadModel(seed=42, method="negative_binomial")
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        row = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2}

        pred_before = model.predict_row("e1", row)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = XGBoostTwoHeadModel.load(tmp)
            pred_after = loaded.predict_row("e1", row)

        assert pred_after.home_expected_runs == pred_before.home_expected_runs
        assert pred_after.away_expected_runs == pred_before.away_expected_runs

    def test_non_default_seed_and_method_preserved_not_silently_reset(self):
        data = _synthetic_training_data()
        model = XGBoostTwoHeadModel(seed=777, method="negative_binomial")
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        row = {"f1": 4.5, "f2": 4.2, "g1": 0.5, "g2": -0.2}

        pred_before = model.predict_row("e1", row)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = XGBoostTwoHeadModel.load(tmp)

        assert loaded.distribution.seed == 777
        assert loaded.distribution.method == "negative_binomial"
        pred_after = loaded.predict_row("e1", row)
        assert pred_after.home_win_prob == pred_before.home_win_prob

    def test_loaded_model_is_marked_fitted(self):
        data = _synthetic_training_data()
        model = XGBoostTwoHeadModel(seed=1, method="negative_binomial")
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = XGBoostTwoHeadModel.load(tmp)

        assert loaded._fitted is True
        loaded.predict_row("e1", {"f1": 4.0, "f2": 4.0, "g1": 0.0, "g2": 0.0})  # must not raise

    def test_feature_names_are_preserved(self):
        data = _synthetic_training_data()
        model = XGBoostTwoHeadModel(seed=1, method="negative_binomial")
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = XGBoostTwoHeadModel.load(tmp)

        assert loaded._intensity_features == INTENSITY_FEATURES
        assert loaded._differential_features == DIFFERENTIAL_FEATURES

    def test_save_writes_a_real_metadata_json(self):
        data = _synthetic_training_data()
        model = XGBoostTwoHeadModel(seed=1, method="negative_binomial")
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            assert (Path(tmp) / "model.joblib").exists()
            assert (Path(tmp) / "metadata.json").exists()

    def test_saving_an_unfitted_model_raises(self):
        model = XGBoostTwoHeadModel(seed=1)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                model.save(tmp)
                raised = False
            except RuntimeError:
                raised = True
            assert raised, "saving an unfitted model must fail loudly, not silently write a broken bundle"


def _training_data_with_an_always_missing_column(n: int = 40, seed: int = 0) -> pl.DataFrame:
    """Same shape as _synthetic_training_data(), except f2/g2 are 100% NaN
    -- the exact real scenario Task 5's live verification hit: a feature
    (weather, in the real data) that is currently unavailable for every
    single real historical game."""
    rng = np.random.default_rng(seed)
    f1 = rng.uniform(3, 6, n)
    g1 = rng.uniform(-2, 2, n)
    total_runs = f1 + rng.normal(0, 0.5, n)
    home_margin = g1 + rng.normal(0, 0.5, n)
    return pl.DataFrame(
        {
            "f1": f1,
            "f2": [float("nan")] * n,
            "g1": g1,
            "g2": [float("nan")] * n,
            "total_runs": total_runs,
            "home_margin": home_margin,
        }
    )


class TestAlwaysMissingColumnNeutralization:
    """Task 5 (explicit missingness): real bug caught by live verification
    against the actual current backfilled dataset, not a hypothetical --
    weather (temp_f_first_pitch) is 100% NaN for every real historical
    game right now (Task 3's PIT-safe weather fix leaves the 3 pre-fix
    legacy snapshots unusable). StandardScaler.fit_transform() on an
    all-NaN column silently produced all-NaN mean/variance, and
    HistGradientBoostingRegressor's binning step then raised a real
    ValueError trying to find split thresholds among zero real distinct
    values. Separately, SimpleImputer(strategy="mean") drops an all-NaN
    column from its output entirely (confirmed live), which would have
    silently shifted every later column out of alignment with the
    model's own stored feature-name list."""

    def test_fit_does_not_crash_when_an_intensity_feature_is_always_missing(self):
        data = _training_data_with_an_always_missing_column()
        model = MLBTwoHeadModel(seed=1)
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        assert model._fitted

    def test_predict_row_does_not_crash_with_an_always_missing_feature(self):
        data = _training_data_with_an_always_missing_column()
        model = MLBTwoHeadModel(seed=1)
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)

        row = data.row(0, named=True)
        pred = model.predict_row("event1", row)

        assert pred.total_mean == pred.total_mean  # not NaN (self-equality check)
        assert 0.0 <= pred.home_win_prob <= 1.0

    def test_differential_head_imputer_does_not_silently_drop_the_column(self):
        # Real bug: SimpleImputer(strategy="mean") drops an all-NaN
        # column from its *output* entirely rather than erroring --
        # confirmed live. If RunDifferentialHead didn't neutralize first,
        # a 2-feature fit would silently become a 1-feature fit, with
        # g1's values landing in the position g2 was supposed to occupy.
        from model_prediction.rebuild.models import RunDifferentialHead

        data = _training_data_with_an_always_missing_column()
        X = data.select(["g1", "g2"]).to_numpy()
        y = data["home_margin"].to_numpy()

        head = RunDifferentialHead().fit(X, y, ["g1", "g2"])

        assert head._always_missing_mask.tolist() == [False, True]
        # predict() must accept the original 2-column shape, not a
        # silently-narrowed 1-column one.
        pred = head.predict(X[:1])
        assert pred.shape == (1,)

    def test_reload_predicts_identically_with_an_always_missing_feature(self):
        data = _training_data_with_an_always_missing_column()
        model = MLBTwoHeadModel(seed=1)
        model.fit(data, INTENSITY_FEATURES, DIFFERENTIAL_FEATURES)
        row = data.row(0, named=True)
        pred_before = model.predict_row("event1", row)

        with tempfile.TemporaryDirectory() as tmp:
            model.save(tmp)
            loaded = MLBTwoHeadModel.load(tmp)
        pred_after = loaded.predict_row("event1", row)

        assert pred_before.home_win_prob == pred_after.home_win_prob
        assert pred_before.total_mean == pred_after.total_mean


class TestLowVarianceColumnNeutralization:
    """Task 16: real second bug caught by live verification of
    BootstrapMLBEnsemble against the real backfilled dataset (not the
    Task 5 all-NaN case -- a real, separate trigger). Bootstrap
    resampling (sampling with replacement) can, by real chance, draw only
    one distinct real value for a naturally low-cardinality feature
    (e.g. park_factor, ~30 real distinct MLB values) even though the
    column has zero missing values in the full real training set --
    np.all(np.isnan(X)) alone never catches this, since nothing is
    actually NaN; HistGradientBoostingRegressor's binning step still
    raised the identical real ValueError trying to find a split threshold
    among 1 real distinct value."""

    def test_fit_does_not_crash_when_a_resample_has_only_one_distinct_value(self):
        from model_prediction.rebuild.models import RunIntensityHead

        n = 40
        X = np.column_stack(
            [
                np.full(n, 100.0),  # constant column -- zero real variance, no NaN at all
                np.linspace(3.0, 6.0, n),
            ]
        )
        y = np.linspace(7.0, 10.0, n)

        head = RunIntensityHead().fit(X, y, ["park_factor", "f2"])
        assert head._always_missing_mask.tolist() == [True, False]
        pred = head.predict(X[:1])
        assert pred.shape == (1,)

    def test_bootstrap_ensemble_fit_survives_a_real_low_cardinality_column(self):
        # Real regression coverage for the exact live crash: a real
        # BootstrapMLBEnsemble fit over many resamples of a small real
        # dataset containing a genuinely low-cardinality column (a
        # handful of distinct park factors), which a real resample can
        # draw down to a single distinct value purely by chance.
        from model_prediction.rebuild.models import BootstrapMLBEnsemble

        rng = np.random.default_rng(0)
        n = 30
        park_factors = rng.choice([96.0, 100.0, 104.0], size=n)
        f2 = rng.uniform(3, 6, n)
        g1 = rng.uniform(-2, 2, n)
        g2 = rng.uniform(-1, 1, n)
        data = pl.DataFrame(
            {
                "park_factor": park_factors,
                "f2": f2,
                "g1": g1,
                "g2": g2,
                "total_runs": park_factors / 20 + f2 + rng.normal(0, 0.5, n),
                "home_margin": g1 + g2 + rng.normal(0, 0.5, n),
            }
        )

        bootstrap = BootstrapMLBEnsemble(n_bootstrap=30, seed=1)
        bootstrap.fit(data, ["park_factor", "f2"], ["g1", "g2"])
        assert bootstrap.fitted
