from model_prediction.sports.ncaaf.total import (
    CFBDistributionType,
    CFBJointDistributionEngine,
    CFBJointMarketProbabilities,
)


def test_ncaaf_total_imports():
    assert CFBDistributionType is not None
    assert CFBJointDistributionEngine is not None
    assert CFBJointMarketProbabilities is not None
