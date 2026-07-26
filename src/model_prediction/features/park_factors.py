"""MLB park-adjusted run environment.

Static three-year park run factors (1.00 = neutral). These are published
figures, not live data; the table is versioned here so changes are reviewable
in git. Unknown parks return neutral with an explicit status.
"""

from __future__ import annotations

from typing import Any

PARK_FACTORS_VERSION = "2025-three-year"

# Home team display name -> run factor (three-year rolling, both teams' runs).
PARK_RUN_FACTORS: dict[str, float] = {
    "Colorado Rockies": 1.12,
    "Cincinnati Reds": 1.05,
    "Boston Red Sox": 1.04,
    "Kansas City Royals": 1.03,
    "Arizona Diamondbacks": 1.02,
    "Texas Rangers": 1.02,
    "Atlanta Braves": 1.01,
    "Baltimore Orioles": 1.01,
    "Los Angeles Angels": 1.01,
    "Philadelphia Phillies": 1.01,
    "Chicago Cubs": 1.00,
    "Houston Astros": 1.00,
    "Minnesota Twins": 1.00,
    "Pittsburgh Pirates": 1.00,
    "Toronto Blue Jays": 1.00,
    "Washington Nationals": 1.00,
    "Chicago White Sox": 0.99,
    "Los Angeles Dodgers": 0.99,
    "Miami Marlins": 0.99,
    "New York Yankees": 0.99,
    "St. Louis Cardinals": 0.99,
    "Athletics": 0.98,
    "Detroit Tigers": 0.98,
    "Milwaukee Brewers": 0.98,
    "New York Mets": 0.97,
    "Cleveland Guardians": 0.97,
    "San Francisco Giants": 0.96,
    "Tampa Bay Rays": 0.96,
    "San Diego Padres": 0.95,
    "Seattle Mariners": 0.92,
}


def park_factor(home_team: str) -> dict[str, Any]:
    factor = PARK_RUN_FACTORS.get(home_team)
    if factor is None:
        return {"park_factor": 1.0, "status": "unavailable_from_source", "version": PARK_FACTORS_VERSION}
    return {"park_factor": factor, "status": "available", "version": PARK_FACTORS_VERSION}
