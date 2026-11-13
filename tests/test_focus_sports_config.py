"""Pin the production scope decided 2026-11-13: six sports forecast daily, the rest are off.

The flag lives in config/model.yaml (``daily_forecast_enabled``) and every daily
forecast task, learned or not, consults ``is_sport_daily_enabled`` before running.
MLB and WNBA are in scope but currently off-season, so they are asserted separately.
"""

from model_prediction.config import is_sport_daily_enabled, load_config

IN_SCOPE_IN_SEASON = ("NBA", "NCAAF", "NFL", "NCAAB")
IN_SCOPE_OFF_SEASON = ("MLB", "WNBA")
OUT_OF_SCOPE = (
    "SOCCER",
    "TENNIS",
    "LOL",
    "CS2",
    "DOTA2",
    "VALORANT",
    "RAINBOW_SIX",
    "KBO",
    "NPB",
    "HOCKEY",
    "DARTS",
    "EUROLEAGUE",
    "NBL",
)


def test_in_scope_sports_are_daily_enabled():
    config = load_config()
    for sport in IN_SCOPE_IN_SEASON:
        assert is_sport_daily_enabled(config, sport), sport


def test_in_scope_off_season_sports_are_only_gated_by_season():
    config = load_config()
    for sport in IN_SCOPE_OFF_SEASON:
        sport_cfg = config["models"][sport]
        assert sport_cfg.get("enabled") is True, sport
        assert sport_cfg.get("season_active") is False, sport


def test_out_of_scope_sports_are_daily_disabled():
    config = load_config()
    for sport in OUT_OF_SCOPE:
        assert not is_sport_daily_enabled(config, sport), sport
        # Code and artifacts stay; only the daily forecast is switched off.
        assert config["models"][sport].get("enabled") is True, sport
