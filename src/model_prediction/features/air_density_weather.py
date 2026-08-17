"""Per-game air-density run factor from observed game-time temperature (shadow).

Extends features/air_density.py's published ball-flight physics (Bahill,
Baldwin, Ramberg 2009; SABR "High Altitude Offense" 2014) with the ONE
per-game driver this project holds historical data for: game-time
temperature (data/mlb_statsapi/game_snapshots.jsonl ``weather.temperature_f``;
pressure and humidity are not captured anywhere locally).

Design follows the double-counting finding recorded in the experiment
registry (mlb-v9-air-density-double-counting-finding): park ALTITUDE is
deliberately NOT included here. The empirical PARK_RUN_FACTORS table is fit
from real observed scoring at each venue and already absorbs each park's
average environment; a second static per-park constant would double-count
it. This module instead models the GAME-LEVEL deviation -- today's
temperature versus that park's own month-of-season norm -- which is the
component the empirical park factor cannot see.

The physics: at constant pressure, air density scales as 1/T (ideal gas
law), and a 10% density drop adds ~4% to fly-ball distance
(features/air_density.py::fly_ball_distance_factor). Translating distance
into expected runs is the MODEL's job (walk-forward), not this module's --
it returns only the distance factor; the elasticity is fitted empirically
by scripts/mlb_v9_air_density_backtest.py.

INERT: wired into no production model. Fail-closed: indoor games
("Dome"/"Roof Closed" -- climate-controlled air), missing temperature,
unknown venue, or no norm available all return a neutral 1.0 factor with
an explicit status, never a guess.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .air_density import air_density, fly_ball_distance_factor

DEFAULT_SNAPSHOT_PATH = Path("data/mlb_statsapi/game_snapshots.jsonl")

_NORMS_CACHE: dict[Path, dict[tuple[str, int], float]] = {}

INDOOR_CONDITIONS = {"dome", "roof closed"}


def _fahrenheit_to_celsius(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0


def park_monthly_temperature_norms(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[tuple[str, int], float]:
    """(venue_name, month 1-12) -> mean observed game-time temperature (F).

    Computed from the snapshot file itself -- no external climate table --
    so the norm is exactly "this park's own typical conditions for this
    month of season," the right baseline for a deviation signal.
    """
    path = Path(snapshot_path)
    if path in _NORMS_CACHE:
        return _NORMS_CACHE[path]
    sums: dict[tuple[str, int], list[float]] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue
                venue = snap.get("venue_name")
                weather = snap.get("weather") or {}
                temp = weather.get("temperature_f")
                if not venue or temp is None:
                    continue
                try:
                    month = datetime.fromisoformat(str(snap.get("game_start_utc", ""))).month
                except ValueError:
                    continue
                sums.setdefault((venue, month), []).append(float(temp))
    norms: dict[tuple[str, int], float] = {}
    for key, values in sums.items():
        norms[key] = sum(values) / len(values)
    _NORMS_CACHE[path] = norms
    return norms


def air_density_distance_factor(
    venue_name: str,
    game_start_utc: str,
    temperature_f: float,
    condition: str,
    *,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    """Distance factor for a game's temperature deviation from park norm.

    Returns {"factor": float, "status": str}. Neutral 1.0 when indoor or
    when any input needed to compute a trustworthy deviation is missing.
    """
    if (condition or "").strip().lower() in INDOOR_CONDITIONS:
        return {"factor": 1.0, "status": "indoor_climate_controlled"}
    if not venue_name or temperature_f is None:
        return {"factor": 1.0, "status": "unavailable_from_source"}
    try:
        month = datetime.fromisoformat(str(game_start_utc)).month
    except ValueError:
        return {"factor": 1.0, "status": "unavailable_from_source"}
    norms = park_monthly_temperature_norms(snapshot_path)
    norm = norms.get((venue_name, month))
    if norm is None:
        return {"factor": 1.0, "status": "no_park_month_norm"}
    # Density at the game's temperature vs at the park's norm, standard
    # sea-level pressure and zero humidity held on both sides (pressure and
    # humidity are not observed per game; they cancel in the ratio except
    # for their temperature interaction, which is second-order).
    game_density = air_density(_fahrenheit_to_celsius(float(temperature_f)), 101325.0, 0.0)
    norm_density = air_density(_fahrenheit_to_celsius(norm), 101325.0, 0.0)
    # Hotter than norm -> lighter air -> factor > 1 (ball carries further).
    # fly_ball_distance_factor takes rho/std, so pass rho_game/rho_norm.
    deviation_ratio = game_density.density_ratio / norm_density.density_ratio
    return {
        "factor": round(fly_ball_distance_factor(deviation_ratio), 6),
        "status": "available",
    }
