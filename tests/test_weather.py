from __future__ import annotations

from model_prediction.features.weather import live_weather, weather_profile


def test_weather_profile_accepts_direct_espn_weather_and_applies_wind() -> None:
    result = weather_profile(
        "Boston Red Sox",
        {"temperature": 80, "windSpeed": 20, "humidity": 55},
    )

    assert result["temperature_f"] == 80
    assert result["wind_mph"] == 20
    assert result["weather_run_factor"] > 1.01


def test_live_weather_uses_forecast_hour_nearest_game_start(monkeypatch) -> None:
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "hourly": {
                    "time": [
                        "2026-07-26T10:00",
                        "2026-07-26T11:00",
                        "2026-07-26T12:00",
                    ],
                    "temperature_2m": [60, 70, 90],
                    "wind_speed_10m": [1, 2, 20],
                    "relative_humidity_2m": [30, 40, 50],
                }
            }

    monkeypatch.setattr(
        "model_prediction.features.weather.httpx.get",
        lambda *args, **kwargs: Response(),
    )
    # live_weather caches the raw hourly payload per team (see
    # prefetch_live_weather) -- reset it so this test doesn't see a payload
    # left behind by another test that already fetched this same team.
    monkeypatch.setattr("model_prediction.features.weather._FORECAST_CACHE", {})

    result = live_weather("Boston Red Sox", "2026-07-26T11:45:00Z")

    assert result["temperature_f"] == 90
    assert result["wind_mph"] == 20
    assert result["humidity_pct"] == 50
