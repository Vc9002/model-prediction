from model_prediction.dashboard.backtests import _team_matches as dashboard_team_matches
from model_prediction.data_sources.polymarket_execute import _team_name_matches as execute_team_matches
from model_prediction.learned_forward import _team_matches as forward_team_matches


def test_team_matches_rejects_directional_prefixes():
    # Directional prefix should not match state school
    for fn in (dashboard_team_matches, forward_team_matches, execute_team_matches):
        assert not fn("Western Kentucky Hilltoppers", "Kentucky")
        assert not fn("Middle Tennessee Blue Raiders", "Tennessee")
        assert not fn("North Texas Mean Green", "Texas")
        assert not fn("West Virginia Mountaineers", "Virginia")
        assert not fn("South Florida Bulls", "Florida")
        assert not fn("Northern Illinois Huskies", "Illinois")


def test_team_matches_rejects_institutional_suffixes():
    # Suffix like State, Tech, A&M should not match flagship university
    for fn in (dashboard_team_matches, forward_team_matches, execute_team_matches):
        assert not fn("Penn State Nittany Lions", "Penn")
        assert not fn("Michigan State Spartans", "Michigan")
        assert not fn("Ohio State Buckeyes", "Ohio")
        assert not fn("Texas Tech Red Raiders", "Texas")
        assert not fn("Texas A&M Aggies", "Texas")
        assert not fn("Florida State Seminoles", "Florida")


def test_team_matches_accepts_valid_equivalents():
    # Valid city, mascot, or exact alias matches
    for fn in (dashboard_team_matches, forward_team_matches, execute_team_matches):
        assert fn("Denver Nuggets", "Denver")
        assert fn("Seattle Storm", "Storm")
        assert fn("Kentucky Wildcats", "Kentucky")
        assert fn("Tennessee Volunteers", "Tennessee")
        assert fn("Penn Quakers", "Penn")
        assert fn("Penn State Nittany Lions", "Penn State")
        assert fn("Texas Longhorns", "Texas")
