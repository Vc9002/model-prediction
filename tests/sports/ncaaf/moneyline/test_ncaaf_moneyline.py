from model_prediction.sports.ncaaf.moneyline import CFBStructuralV2Model, CollegeFootballModel


def test_ncaaf_moneyline_imports():
    assert CollegeFootballModel is not None
    assert CFBStructuralV2Model is not None
