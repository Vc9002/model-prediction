import json

import numpy as np
import pytest

from model_prediction.joint_score_research import fit, probabilities, score_samples


@pytest.fixture
def fitted():
    rng = np.random.default_rng(21)
    x = rng.normal(size=(180, 2))
    shared = rng.normal(scale=5, size=180)
    scores = np.rint(
        np.maximum(
            0,
            np.column_stack(
                (24 + 5 * x[:, 0] + shared, 22 + 4 * x[:, 1] + shared + rng.normal(scale=6, size=180))
            ),
        )
    )
    return fit(
        x[:100], scores[:100], x[100:140], scores[100:140], x[140:], scores[140:], ["home_form", "away_form"]
    ), x


def test_joint_distribution_is_coherent_across_markets_and_json_reload(fitted):
    model, x = fitted
    restored = json.loads(json.dumps(model))
    assert np.array_equal(score_samples(model, x[:5]), score_samples(restored, x[:5]))
    samples = score_samples(model, x[:5])
    assert samples.shape == (5, 40, 2)
    assert np.equal(samples, np.floor(samples)).all() and (samples >= 0).all()
    ml = probabilities(model, x[:5], "moneyline")
    zero = probabilities(model, x[:5], "spread", line=0)
    assert np.array_equal(ml[:, 1], zero[:, 2])
    assert np.array_equal(ml[:, 0], zero[:, 0])
    assert not zero[:, 1].any()
    for market, line in (("spread", -3), ("spread", -3.5), ("total", 45), ("total", 45.5)):
        p = probabilities(model, x[:5], market, line=line)
        assert np.allclose(p.sum(1), 1) and (p >= 0).all()
        if line % 1:
            assert not p[:, 1].any()
    low = probabilities(model, x[:5], "total", line=40.5)[:, 2]
    high = probabilities(model, x[:5], "total", line=50.5)[:, 2]
    assert (low >= high).all()


def test_residual_uncertainty_is_fitted_on_calibration_pairs(fitted):
    model, _ = fitted
    residuals = np.asarray(model["residual_pairs"])
    assert len(residuals) == 40
    assert np.array_equal(np.cov(residuals, rowvar=False), np.asarray(model["residual_covariance"]))
    assert model["residual_covariance"][0][1] > 0


def test_corrupted_artifact_missing_lines_and_bad_features_fail(fitted):
    model, x = fitted
    with pytest.raises(ValueError, match="hash"):
        score_samples(model | {"selected_alpha": -1}, x[:1])
    with pytest.raises(ValueError, match="features"):
        score_samples(model, [[np.nan, 1]])
    for line in (None, True, float("inf"), 3.2):
        with pytest.raises(ValueError, match="contract_line"):
            probabilities(model, x[:1], "spread", line=line)


def test_fit_rejects_missing_or_noninteger_score_labels():
    x = np.zeros((25, 2))
    y = np.ones((25, 2)) * 3.5
    with pytest.raises(ValueError, match="training_values"):
        fit(x, y, x, y, x, y, ["a", "b"])


def test_exported_bundle_uses_the_same_joint_inference(fitted, tmp_path):
    import joblib

    from model_prediction.research_generation import file_hash, predict_bundle

    model, x = fitted
    recipe = {
        "model_id": model["model_ids"]["moneyline"],
        "research_family": "ncaaf_joint_score_v2",
        "market": "moneyline",
        "features": model["feature_names"],
        "joint_score_artifact": model,
        "target": "moneyline",
        "class_labels": ["b_win", "a_win"],
    }
    path = tmp_path / "model.joblib"
    joblib.dump({"model": None, "recipe": recipe}, path)
    path.with_name("recipe.json").write_text(json.dumps(recipe | {"model_file_sha256": file_hash(path)}))
    assert np.array_equal(predict_bundle(path, x[:3]), probabilities(model, x[:3], "moneyline"))
    recipe["class_labels"] = ["a_win", "b_win"]
    joblib.dump({"model": None, "recipe": recipe}, path)
    path.with_name("recipe.json").write_text(json.dumps(recipe | {"model_file_sha256": file_hash(path)}))
    with pytest.raises(ValueError, match="outcome_space"):
        predict_bundle(path, x[:3])
