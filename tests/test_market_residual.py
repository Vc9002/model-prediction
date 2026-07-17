import random

from model_prediction.models.market_residual import (
    MarketResidualModel,
    ResidualTrainingRow,
)


def synthetic_rows(count: int) -> list[ResidualTrainingRow]:
    rng = random.Random(7)
    rows = []
    for _ in range(count):
        market = rng.uniform(0.2, 0.8)
        # True probability tracks the market more than the (noisy) model.
        model = min(0.95, max(0.05, market + rng.uniform(-0.15, 0.15)))
        outcome = 1 if rng.random() < market else 0
        rows.append(
            ResidualTrainingRow(
                settled_at_utc="2026-07-10T00:00:00Z",
                model_probability=model,
                market_probability=market,
                outcome=outcome,
            )
        )
    return rows


def test_small_sample_falls_back_to_identity() -> None:
    model = MarketResidualModel.train(synthetic_rows(20))
    assert model.is_identity
    assert model.calibrated_probability(0.61, 0.55) == 0.61
    assert "identity-fallback" in model.version


def test_training_learns_and_outputs_valid_probabilities() -> None:
    model = MarketResidualModel.train(synthetic_rows(500))
    assert not model.is_identity
    for model_p, market_p in ((0.3, 0.5), (0.6, 0.55), (0.8, 0.4)):
        calibrated = model.calibrated_probability(model_p, market_p)
        assert 0 < calibrated < 1


def test_artifact_round_trip_with_hash(tmp_path) -> None:
    model = MarketResidualModel.train(synthetic_rows(300))
    path = tmp_path / "residual.json"
    model.save(path)
    loaded = MarketResidualModel.load(path)
    assert loaded.coefficients == model.coefficients
    # Tampering must be detected.
    tampered = path.read_text().replace("sample_size", "sample_sizE")
    path.write_text(tampered)
    try:
        MarketResidualModel.load(path)
        raised = False
    except (ValueError, KeyError):
        raised = True
    assert raised
