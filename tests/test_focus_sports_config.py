"""Pin the production scope decided 2026-11-13: six sports forecast daily, the rest are off.

The switch is ``daily_forecast_enabled`` in config/model.yaml and every daily forecast
task consults ``is_sport_daily_enabled`` before running. Season state (``season_active``)
is deliberately not asserted here: it flips at every season boundary and has nothing to
do with scope.
"""

from model_prediction.config import is_sport_daily_enabled, load_config

IN_SCOPE = ("NBA", "NCAAF", "NFL", "MLB", "WNBA", "NCAAB")
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


def test_in_scope_sports_are_not_switched_off():
    config = load_config()
    for sport in IN_SCOPE:
        sport_cfg = config["models"][sport]
        assert sport_cfg.get("enabled") is True, sport
        # An in-scope sport may be off only for season reasons (MLB/WNBA carry
        # daily_forecast_enabled: false together with season_active: false). Off while
        # in season is a scope regression; the season rollover itself never trips this.
        if sport_cfg.get("daily_forecast_enabled") is False:
            assert sport_cfg.get("season_active") is False, f"{sport} switched off while in season"


def test_out_of_scope_sports_are_daily_disabled():
    config = load_config()
    for sport in OUT_OF_SCOPE:
        assert not is_sport_daily_enabled(config, sport), sport
        # Code and artifacts stay; only the daily forecast is switched off.
        assert config["models"][sport].get("enabled") is True, sport
