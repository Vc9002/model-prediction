import random

from model_prediction.calibration import (
    IdentityCalibrator,
    IsotonicCalibrator,
    TemperatureCalibrator,
    TrainablePlattCalibrator,
)


def overconfident_sample(count: int) -> tuple[list[float], list[int]]:
    rng = random.Random(11)
    probabilities, outcomes = [], []
    for _ in range(count):
        p = rng.uniform(0.1, 0.9)
        # True hit rate is shrunk toward 0.5 relative to stated p (overconfident model).
        truth = 0.5 + 0.5 * (p - 0.5)
        probabilities.append(p)
        outcomes.append(1 if rng.random() < truth else 0)
    return probabilities, outcomes


def underconfident_sample(count: int) -> tuple[list[float], list[int]]:
    rng = random.Random(11)
    probabilities, outcomes = [], []
    for _ in range(count):
        p = rng.uniform(0.1, 0.9)
        # True hit rate is pushed away from 0.5 relative to stated p (underconfident).
        truth = min(0.99, max(0.01, 0.5 + 1.5 * (p - 0.5)))
        probabilities.append(p)
        outcomes.append(1 if rng.random() < truth else 0)
    return probabilities, outcomes


def test_platt_small_sample_returns_identity() -> None:
    calibrator = TrainablePlattCalibrator.fit([0.6] * 10, [1] * 10, "m-v1")
    assert isinstance(calibrator, IdentityCalibrator)


def test_platt_shrinks_overconfident_probabilities() -> None:
    probabilities, outcomes = overconfident_sample(2000)
    calibrator = TrainablePlattCalibrator.fit(probabilities, outcomes, "m-v1")
    assert isinstance(calibrator, TrainablePlattCalibrator)
    assert calibrator.transform(0.9) < 0.9
    assert calibrator.transform(0.1) > 0.1
    assert calibrator.transform(0.4) < calibrator.transform(0.6)


def test_isotonic_is_monotonic_and_bounded() -> None:
    probabilities, outcomes = overconfident_sample(2000)
    calibrator = IsotonicCalibrator.fit(probabilities, outcomes, "m-v1")
    assert isinstance(calibrator, IsotonicCalibrator)
    previous = 0.0
    for step in range(1, 20):
        value = calibrator.transform(step / 20)
        assert 0 <= value <= 1
        assert value >= previous - 1e-9
        previous = value


def test_temperature_small_sample_returns_identity() -> None:
    calibrator = TemperatureCalibrator.fit([0.6] * 10, [1] * 10, "m-v1")
    assert isinstance(calibrator, IdentityCalibrator)


def test_temperature_sharpens_underconfident_probabilities() -> None:
    probabilities, outcomes = underconfident_sample(2000)
    calibrator = TemperatureCalibrator.fit(probabilities, outcomes, "m-v1")
    assert isinstance(calibrator, TemperatureCalibrator)
    assert calibrator.temperature < 1.0
    assert calibrator.transform(0.7) > 0.7
    assert calibrator.transform(0.3) < 0.3
    assert calibrator.transform(0.4) < calibrator.transform(0.6)
    # Never leaves [0, 1] even at extreme inputs.
    assert 0 <= calibrator.transform(1e-6) <= 1
    assert 0 <= calibrator.transform(1 - 1e-6) <= 1


def test_temperature_softens_overconfident_probabilities() -> None:
    probabilities, outcomes = overconfident_sample(2000)
    calibrator = TemperatureCalibrator.fit(probabilities, outcomes, "m-v1")
    assert isinstance(calibrator, TemperatureCalibrator)
    assert calibrator.temperature > 1.0
    assert calibrator.transform(0.7) < 0.7
    assert calibrator.transform(0.3) > 0.3
