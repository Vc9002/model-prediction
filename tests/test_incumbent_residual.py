import numpy as np
import pytest

from model_prediction.incumbent_residual import date_split, fit_offset, predict_offset


def test_identity_reproduces_control_exactly():
    base = np.array([0.1, 0.6123456789, 0.9])
    assert np.array_equal(predict_offset({"kind": "identity"}, base, np.ones((3, 2))), base)


def test_regularization_shrinks_correction_and_constant_feature_is_safe():
    x = np.array([[1.0, i % 2] for i in range(100)])
    base = np.full(100, 0.5)
    y = x[:, 1]
    weak = fit_offset(base, x, y, 0.1)
    strong = fit_offset(base, x, y, 100.0)
    assert np.linalg.norm(strong["coefficients"]) < np.linalg.norm(weak["coefficients"])
    assert np.isfinite(predict_offset(weak, base, x)).all()
    assert (predict_offset(weak, base, x)[y == 1] > 0.5).all()


def test_splits_keep_complete_dates_and_strict_chronology():
    dates = [f"2026-08-{i:02d}" for i in range(1, 31) for _ in range(i % 3 + 1)]
    groups = date_split(dates)
    days = {name: {dates[i] for i in indices} for name, indices in groups.items()}
    assert [len(days[n]) for n in ("train", "select", "test")] == [18, 6, 6]
    assert max(days["train"]) < min(days["select"]) < max(days["select"]) < min(days["test"])
    assert sum(map(len, groups.values())) == len(dates)


@pytest.mark.parametrize("bad", [0.0, 1.0, np.nan])
def test_invalid_baseline_never_becomes_a_neutral_fallback(bad):
    with pytest.raises(ValueError):
        fit_offset(np.array([bad]), np.ones((1, 2)), np.array([1]), 1.0)
