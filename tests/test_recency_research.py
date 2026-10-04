import json
from dataclasses import asdict

import numpy as np
import pytest

from model_prediction.research_forward import expected_model_id
from model_prediction.research_generation import partition, predict_bundle, train_candidate
from scripts import train_recency_research as runner
from tests.test_research_generation import sample_rows


def test_training_weights_have_exact_half_life_and_constant_total_weight():
    dates = ["2025-01-01", "2025-06-30"]
    weights = runner.training_weights(dates, 180)
    assert weights[1] / weights[0] == pytest.approx(2)
    assert weights.mean() == pytest.approx(1)
    with pytest.raises(ValueError, match="invalid_recency"):
        runner.training_weights(dates, 0)


@pytest.mark.parametrize("market", ["moneyline", "total"])
def test_recency_fit_uses_only_training_rows_and_replays_control(tmp_path, monkeypatch, market):
    data = sample_rows()
    generation = tmp_path / "reference"
    registration = train_candidate(
        data, "NBA", market, "inc", generation / f"models/nba-{market}", ("x", "weekday")
    )
    (generation / "features").mkdir()
    (generation / "features/nba.jsonl").write_text("".join(json.dumps(asdict(r)) + "\n" for r in data))
    output = tmp_path / "recency"
    masks = partition(data)
    actual_fit = runner.fit_weighted
    calls = []

    def checked_fit(model, x, y, weights):
        assert np.array_equal(x, np.asarray([data[i].features for i in masks["train"]]))
        assert len(y) == len(weights) == len(masks["train"])
        calls.append(len(y))
        return actual_fit(model, x, y, weights)

    monkeypatch.setattr(runner, "fit_weighted", checked_fit)
    report = runner.train_one(generation, output, registration)
    assert len(calls) == 2
    assert report["reference_reproduction"] == "PASS" and report["reference_max_absolute_error"] == 0
    assert report["serialization_parity"] == "PASS" and report["economics"] is None
    assert not report["promote"]
    artifact = output / f"models/nba-{market}/model.joblib"
    recipe = json.loads(artifact.with_name("recipe.json").read_text())
    assert recipe["model_id"] == expected_model_id(recipe, "NBA", market)
    p = predict_bundle(artifact, np.asarray([data[-1].features]), line=11.5 if market == "total" else None)
    assert p.sum() == pytest.approx(1)
    if report["verdict"] == "EXPLORATORY_GAIN_VS_RESEARCH_REFERENCE":
        assert report["reference_minus_candidate_loss"]["lower_95"] > report["minimum_effect"]
    saved = generation / f"models/nba-{market}/evaluation_predictions.jsonl"
    values = runner.rows(saved)
    values[0]["prediction"] = [0.5, 0.5] if market == "moneyline" else -999
    saved.write_text("".join(json.dumps(r) + "\n" for r in values))
    with pytest.raises(ValueError, match="reference_reproduction_failed"):
        runner.train_one(generation, tmp_path / "bad", registration)
    assert len(calls) == 2  # Gate blocks new fitting, not merely the final verdict.
