from model_prediction.sports.wnba.spread import WNBAStructuralForecast, WNBAStructuralV3Engine


def test_wnba_spread_imports():
    assert WNBAStructuralForecast is not None
    assert WNBAStructuralV3Engine is not None
