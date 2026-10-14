"""Open-Meteo ensemble forecast client -- raw capture only.

Keyless. Serves per-member hourly forecasts from ensemble models (e.g.
``ecmwf_ifs025``, ``icon_seamless_eps``). Each member appears as its own
series, named ``<variable>_member<NN>``, plus no separate mean. The spread
across members is the uncertainty signal this adapter exists to capture.

``ensemble_spread`` is a pure helper over one hourly variable: it returns
the per-hour mean and standard deviation across members. It takes the raw
payload and does no point-in-time or venue logic.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

BASE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


class OpenMeteoEnsembleClient:
    def __init__(self, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=30)

    def hourly(
        self,
        lat: float,
        lon: float,
        *,
        variables: tuple[str, ...] = ("temperature_2m", "wind_speed_10m"),
        model: str = "ecmwf_ifs025",
        forecast_days: int = 3,
    ) -> dict[str, Any]:
        response = self.client.get(
            self.base_url,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": ",".join(variables),
                "models": model,
                "forecast_days": forecast_days,
                "timezone": "UTC",
            },
        )
        response.raise_for_status()
        return response.json()


def ensemble_spread(payload: dict[str, Any], variable: str) -> list[dict[str, float | None]]:
    """Per-hour mean and standard deviation across ensemble members for one variable.

    Returns one dict per hour with keys ``time``, ``mean``, ``stdev``, and
    ``members``. Hours where no member has a value yield ``None``.
    """
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    member_keys = [key for key in hourly if key.startswith(f"{variable}_member")]
    rows: list[dict[str, float | None]] = []
    for index, timestamp in enumerate(times):
        values = [
            hourly[key][index]
            for key in member_keys
            if index < len(hourly[key]) and hourly[key][index] is not None
        ]
        if not values:
            rows.append({"time": timestamp, "mean": None, "stdev": None, "members": 0})
            continue
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        rows.append({"time": timestamp, "mean": mean, "stdev": math.sqrt(variance), "members": len(values)})
    return rows
