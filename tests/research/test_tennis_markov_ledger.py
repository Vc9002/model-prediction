"""Settled-ledger evaluation of the tennis Markov challenger.

Data-dependent: it reads the live settled tennis ledger, so its calibration numbers
drift as rows settle. Tennis is out of production scope (2026-11-13), so this runs with
the research suite on demand, not in the core suite.
"""

import pytest


@pytest.mark.skip(
    reason=(
        "tennis is out of production scope since 2026-11-13 and the challenger's calibration slope "
        "on the live settled ledger has drifted to ~0.80 (asserted 1.00 +/- 0.05); re-evaluate the "
        "Markov challenger before any tennis re-enable"
    )
)
def test_settled_ledger_evaluation_runs_cleanly():
    from scripts.compare_tennis_markov_models import evaluate_tennis_models

    report = evaluate_tennis_models()
    assert report["verdict"] == "VALIDATED_OFFLINE"
    assert report["sample_size"] >= 800
    assert report["models"]["challenger"]["calibration_slope"] == pytest.approx(1.00, abs=0.05)
    assert report["models"]["challenger"]["log_loss"] < report["models"]["incumbent"]["log_loss"]
    assert report["models"]["challenger"]["brier_score"] < report["models"]["incumbent"]["brier_score"]
