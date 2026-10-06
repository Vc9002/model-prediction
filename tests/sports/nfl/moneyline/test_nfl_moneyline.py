from model_prediction.sports.nfl.moneyline import NFLCalibrator, NFLStructuralV5Engine


def test_nfl_moneyline_imports():
    assert NFLStructuralV5Engine is not None
    assert NFLCalibrator is not None
