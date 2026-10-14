from model_prediction.sports.wnba.total import WNBAStructuralForecast, WNBAStructuralV3Engine


def test_wnba_total_imports():
    assert WNBAStructuralForecast is not None
    assert WNBAStructuralV3Engine is not None
