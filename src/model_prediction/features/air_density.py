"""Closed-form air-density layer for MLB run-environment adjustment (shadow).

Implements the published ball-flight physics with its effect sizes
(Bahill, Baldwin, Ramberg 2009, Int. J. Sports Sci. Eng.; SABR "High
Altitude Offense" 2014; AMS Weather, Climate & Society 2013):

  - air density decomposes as altitude ~80% / temperature ~13% /
    pressure ~4% / humidity ~3% of density variance;
  - a 10% density drop adds ~4% to fly-ball distance;
  - empirical anchors: +2.8 runs/game at 5,000 ft elevation; league
    scoring swings 8.95 -> 10.08 runs/game cold -> warm (22,215 games).

This module is INERT: wired into no model, and it fails closed — the
per-venue elevation table it needs does not exist yet (building one is
data-acquisition work; see docs/DISTRIBUTION_MIGRATION_PLAN.md §2.1),
so callers must supply elevation explicitly. The run-scaling itself is
deliberately NOT included: translating density to expected runs is the
model's job (walk-forward), not this module's — only the physics and
the published anchors are asserted here.
"""

from __future__ import annotations

from dataclasses import dataclass

R_DRY = 287.05  # J/(kg*K), specific gas constant for dry air
G_MOLAR_RATIO = 0.62198  # water-vapor to dry-air molar mass ratio
STANDARD_PRESSURE_PA = 101325.0
STANDARD_TEMP_K = 288.15  # 15 C, ISA sea level
STD_DENSITY = STANDARD_PRESSURE_PA / (R_DRY * STANDARD_TEMP_K)  # ~1.225 kg/m^3
ALTITUDE_SCALE_M = 8435.0  # barometric scale height (standard atmosphere)
DISTANCE_EXPONENT = 0.4  # 10% density drop -> ~4% fly-ball distance gain


@dataclass(frozen=True)
class AirDensityResult:
    density_kg_m3: float
    density_ratio: float  # rho / rho_std, 1.0 = sea-level standard
    virtual_temp_k: float


def _saturation_vapor_pressure_pa(temp_c: float) -> float:
    """Magnus-form saturation vapor pressure over liquid water, Pa."""
    return 610.94 * (10.0 ** ((7.625 * temp_c) / (temp_c + 243.04)))


def air_density(
    temp_c: float,
    pressure_pa: float,
    relative_humidity: float,
) -> AirDensityResult:
    """Air density from temperature, pressure, relative humidity (0-100).

    Pure physics, no state, no lookups. Relative humidity enters via the
    virtual-temperature correction (moist air is lighter per mole).
    """
    es = _saturation_vapor_pressure_pa(temp_c)
    # mixing ratio r = eps * e/(p - e); e = RH * es
    e = (relative_humidity / 100.0) * es
    mixing_ratio = G_MOLAR_RATIO * e / (pressure_pa - e)
    virtual_temp_k = (temp_c + 273.15) * (1.0 + 0.61 * mixing_ratio)
    density = pressure_pa / (R_DRY * virtual_temp_k)
    return AirDensityResult(
        density_kg_m3=density,
        density_ratio=density / STD_DENSITY,
        virtual_temp_k=virtual_temp_k,
    )


def pressure_from_altitude_pa(altitude_m: float) -> float:
    """Barometric pressure estimate from elevation (standard atmosphere)."""
    return STANDARD_PRESSURE_PA * (2.718281828459045 ** (-altitude_m / ALTITUDE_SCALE_M))


def fly_ball_distance_factor(density_ratio: float) -> float:
    """Multiplier on fly-ball distance vs standard sea-level conditions.

    Published calibration: a 10% density drop adds ~4% to fly-ball
    distance, i.e. distance ~ (rho_std / rho)^0.4.
    """
    return (1.0 / density_ratio) ** DISTANCE_EXPONENT


def run_environment_anchors() -> dict[str, float]:
    """Published empirical anchors, for validation and documentation only.

    +2.8 runs/game at 5,000 ft (SABR); 8.95 -> 10.08 runs/game cold ->
    warm (AMS). The module asserts these as constants, never as a
    translation layer — the run translation belongs to the model.
    """
    return {
        "altitude_5000ft_runs_per_game_delta": 2.8,
        "cold_runs_per_game": 8.95,
        "warm_runs_per_game": 10.08,
    }
