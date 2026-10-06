from model_prediction.sports.tennis.moneyline import TennisMarkovEngine, TennisV2Model


def test_tennis_moneyline_imports():
    assert TennisV2Model is not None
    assert TennisMarkovEngine is not None
