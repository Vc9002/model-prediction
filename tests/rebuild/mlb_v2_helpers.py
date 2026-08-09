"""Synthetic fitted objects for MLB v2 artifact integrity tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl

from model_prediction.rebuild.mlb_features import MLB_DIFFERENTIAL_FEATURES, MLB_INTENSITY_FEATURES
from model_prediction.rebuild.mlb_v2_artifact import (
    FROZEN_DISTRIBUTION_METHOD,
    MLB_V2_BUNDLE_DIRNAME,
    XGB_DIRECT_FEATURES,
    FrozenMLBV2Anchor,
    _primary_content_hash,
    write_frozen_mlb_v2_bundle,
)
from model_prediction.rebuild.models import BootstrapMLBEnsemble, MLBTwoHeadModel, XGBoostTwoHeadModel
from model_prediction.rebuild.xgboost_stress import XGBoostChallenger

TEST_CODE_REVISION = "a" * 40
TEST_DATASET_HASH = "d" * 64
TEST_SOURCE_TREE_HASH = "e" * 64


def fitted_components(seed: int = 7):
    rng = np.random.default_rng(seed)
    n = 40
    data = {name: rng.normal(0, 1, n) for name in MLB_INTENSITY_FEATURES}
    data.update({name: rng.normal(0, 1, n) for name in MLB_DIFFERENTIAL_FEATURES})
    data["total_runs"] = rng.uniform(5, 12, n)
    data["home_margin"] = rng.normal(0, 2, n)
    frame = pl.DataFrame(data)

    primary = XGBoostTwoHeadModel(seed=42, method=FROZEN_DISTRIBUTION_METHOD)
    primary.fit(frame, MLB_INTENSITY_FEATURES, MLB_DIFFERENTIAL_FEATURES)
    bootstrap = BootstrapMLBEnsemble(n_bootstrap=2, seed=42, head_family="xgboost")
    bootstrap.fit(frame, MLB_INTENSITY_FEATURES, MLB_DIFFERENTIAL_FEATURES)
    baseline = MLBTwoHeadModel(seed=42)
    baseline.fit(frame, MLB_INTENSITY_FEATURES, MLB_DIFFERENTIAL_FEATURES)
    direct = XGBoostChallenger(seed=42, n_jobs=1)
    X = frame.select(XGB_DIRECT_FEATURES).to_numpy()
    y = (frame["home_margin"].to_numpy() > 0).astype(int)
    direct.fit(X[:30], y[:30], XGB_DIRECT_FEATURES, eval_set=(X[30:], y[30:]))
    row = {name: float(rng.normal()) for name in XGB_DIRECT_FEATURES}
    row["event_id"] = "future-401"
    return primary, bootstrap, baseline, direct, row


def build_test_bundle(challenger_root: Path, repo_root: Path):
    primary, bootstrap, baseline, direct, row = fitted_components()
    calibrator = {
        "model_name": "xgb_two_head_negative_binomial",
        "method": "temperature",
        "parameters": {"temperature": 1.25},
        "base_model_hash": _primary_content_hash(primary),
        "dataset_hash": TEST_DATASET_HASH,
        "n_training_oof": 2,
    }
    calibrator["calibrator_hash"] = hashlib.sha256(
        json.dumps(calibrator, sort_keys=True, default=str).encode()
    ).hexdigest()
    calibrator["oof_probs"] = [0.4, 0.6]
    calibrator["oof_labels"] = [0, 1]
    calibrator_path = challenger_root / "test-calibrator.json"
    challenger_root.mkdir(parents=True, exist_ok=True)
    calibrator_path.write_text(json.dumps(calibrator))
    target = challenger_root / MLB_V2_BUNDLE_DIRNAME
    write_frozen_mlb_v2_bundle(
        target,
        primary=primary,
        bootstrap=bootstrap,
        sklearn_baseline=baseline,
        xgb_direct=direct,
        calibrator_path=calibrator_path,
        dataset_hash=TEST_DATASET_HASH,
        training_cutoff_utc="2098-12-31T23:59:00+00:00",
        training_rows=40,
        code_revision=TEST_CODE_REVISION,
        dependency_manifest=repo_root / "pyproject.toml",
        source_tree_sha256=TEST_SOURCE_TREE_HASH,
    )
    return target, row


def anchor_for_test_bundle(target: Path) -> FrozenMLBV2Anchor:
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    return FrozenMLBV2Anchor(
        status="sealed",
        bundle_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        bundle_hash=manifest["bundle_hash"],
        primary_content_sha256=manifest["components"]["primary"]["learned_content_sha256"],
        primary_artifact_sha256=manifest["components"]["primary"]["artifact_sha256"],
        calibrator_artifact_sha256=manifest["components"]["calibrator"]["artifact_sha256"],
        calibrator_hash=manifest["components"]["calibrator"]["calibrator_hash"],
        source_tree_sha256=manifest["code_provenance"]["source_tree_sha256"],
    )
