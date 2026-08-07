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

import tempfile
from pathlib import Path

import numpy as np
import polars as pl

from model_prediction.rebuild.models import JointScoreDistribution, MLBTwoHeadModel

INTENSITY_FEATURES = ["f1", "f2"]
DIFFERENTIAL_FEATURES = ["g1", "g2"]


def _synthetic_training_data(n: int = 40, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    f1 = rng.uniform(3, 6, n)
    f2 = rng.uniform(3, 6, n)
    g1 = rng.uniform(-2, 2, n)
    g2 = rng.uniform(-1, 1, n)
    total_runs = f1 + f2 + rng.normal(0, 0.5, n)
    home_margin = g1 + g2 + rng.normal(0, 0.5, n)
    return pl.DataFrame({
        "f1": f1, "f2": f2, "g1": g1, "g2": g2,
        "total_runs": total_runs, "home_margin": home_margin,
    })


class TestSeedIsIntrospectable:
    def test_distribution_stores_its_own_seed(self):
        # Real bug caught before ever being committed: JointScoreDistribution
        # never stored the seed it was constructed with, so save()/load()
        # had no way to know what seed to restore — load() silently used
        # the class default instead of the model's real seed.
        dist = JointScoreDistribution(seed=777)
        assert dist.seed == 777


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
