from model_prediction.sports.mlb.moneyline import MLBStructuralV10Model, MonotonicMLBClassifier


def test_mlb_moneyline_imports():
    assert MLBStructuralV10Model is not None
    assert MonotonicMLBClassifier is not None
