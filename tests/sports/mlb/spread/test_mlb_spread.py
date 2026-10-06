from model_prediction.sports.mlb.spread import MeasuredEdgeMarginModel, MLBStructuralRunlineV4Model


def test_mlb_spread_imports():
    assert MeasuredEdgeMarginModel is not None
    assert MLBStructuralRunlineV4Model is not None
