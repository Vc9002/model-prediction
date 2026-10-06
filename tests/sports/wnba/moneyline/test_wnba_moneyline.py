from model_prediction.sports.wnba.moneyline import WNBAPossessionEngine, WNBAStructuralV3Engine


def test_wnba_moneyline_imports():
    assert WNBAStructuralV3Engine is not None
    assert WNBAPossessionEngine is not None
