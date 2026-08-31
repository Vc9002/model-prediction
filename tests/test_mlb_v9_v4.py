import json
from pathlib import Path

import numpy as np
import polars as pl

from model_prediction.features.mlb_v9_v4 import audit_v9_features
from model_prediction.models.mlb_v9_nested_evaluator import (
    NestedEvaluationResult,
    evaluate_nested_walk_forward,
)


def test_v4_feature_audit_detects_collinearity():
    np.random.seed(42)
    n = 100
    x1 = np.random.normal(0, 1, n)
    x2 = np.random.normal(0, 1, n)
    x3 = x1 * 2.0  # perfectly collinear

    mat = np.column_stack([x1, x2, x3])
    rep = audit_v9_features(mat, ["x1", "x2", "x3"])
    assert rep.passed_audit is False
    assert len(rep.high_correlation_pairs) > 0


def test_v4_table_and_manifest_integrity():
    tbl_path = Path("outputs/research/mlb_v9/tables/mlb_v9_feature_table_v4.parquet")
    man_path = Path("outputs/research/mlb_v9/manifests/mlb_v9_feature_table_v4.json")

    assert tbl_path.exists()
    assert man_path.exists()

    df = pl.read_parquet(tbl_path)
    assert len(df) >= 6000

    manifest = json.loads(man_path.read_text(encoding="utf-8"))
    assert manifest["dataset_name"] == "mlb_v9_feature_table_v4"
    assert manifest["statistical_audit"]["passed"] is True
    assert manifest["statistical_audit"]["condition_number"] < 10.0
    assert manifest["statistical_audit"]["max_vif"] < 10.0


def test_nested_walk_forward_evaluator_runs_and_preregisters_mde():
    np.random.seed(42)
    n = 300
    X = np.random.normal(0, 1, (n, 5))
    # True relationship with first feature
    y = np.array([1 if np.random.rand() < (1.0 / (1.0 + np.exp(-0.8 * X[i, 0]))) else 0 for i in range(n)])
    baseline_probs = np.full(n, 0.50)
    dates = [f"2026-05-{i % 30 + 1:02d}" for i in range(n)]

    res = evaluate_nested_walk_forward(
        X=X,
        y=y,
        baseline_probs=baseline_probs,
        dates=dates,
        n_outer_folds=3,
        n_inner_folds=2,
    )
    assert isinstance(res, NestedEvaluationResult)
    assert res.sample_size > 0
    assert res.oof_log_loss_challenger < 0.693
    assert res.delta_log_loss < 0.0
    assert res.mde_threshold > 0.0
    assert isinstance(res.meets_mde_gate, bool)
