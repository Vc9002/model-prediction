"""MLB park-adjusted run environment.

Empirical park run factors computed directly from this project's own real
historical game data (``data/historical/mlb_games_all.jsonl``, 2024-02-22 to
2026-07-25, 7,803 completed games) rather than an externally published
static table -- confirmed during a real backtest investigation (2026-07-29)
that the prior static table was meaningfully stale for several parks, most
strikingly the Athletics: 0.98 (their old Oakland Coliseum figure) versus a
real 1.153 at their current park across 162 real games there. Regenerate by
re-running the same per-team average-total-runs-vs-league-average
computation, credibility-shrunk toward 1.0 by games-played (50-game prior)
so a thin sample doesn't swing as hard as a well-established one. Unknown
parks return neutral with an explicit status.
"""

from __future__ import annotations

from typing import Any

PARK_FACTORS_VERSION = "2026-07-29-empirical"

# Home team display name -> run factor, from real games (see module docstring
# for the exact computation and source window).
PARK_RUN_FACTORS: dict[str, float] = {
    "Colorado Rockies": 1.193,
    "Athletics": 1.153,
    "Arizona Diamondbacks": 1.097,
    "Washington Nationals": 1.042,
    "Minnesota Twins": 1.037,
    "Cincinnati Reds": 1.036,
    "Baltimore Orioles": 1.030,
    "Los Angeles Dodgers": 1.026,
    "Toronto Blue Jays": 1.019,
    "Philadelphia Phillies": 1.018,
    "Boston Red Sox": 1.017,
    "New York Yankees": 1.012,
    "Los Angeles Angels": 1.010,
    "Miami Marlins": 1.001,
    "Milwaukee Brewers": 0.997,
    "Pittsburgh Pirates": 0.988,
    "Chicago Cubs": 0.986,
    "San Francisco Giants": 0.980,
    "New York Mets": 0.978,
    "Kansas City Royals": 0.972,
    "Cleveland Guardians": 0.966,
    "Tampa Bay Rays": 0.965,
    "Detroit Tigers": 0.963,
    "Houston Astros": 0.956,
    "St. Louis Cardinals": 0.946,
    "San Diego Padres": 0.945,
    "Chicago White Sox": 0.937,
    "Seattle Mariners": 0.936,
    "Atlanta Braves": 0.929,
    "Texas Rangers": 0.912,
}


def park_factor(home_team: str) -> dict[str, Any]:
    factor = PARK_RUN_FACTORS.get(home_team)
    if factor is None:
        return {"park_factor": 1.0, "status": "unavailable_from_source", "version": PARK_FACTORS_VERSION}
    return {"park_factor": factor, "status": "available", "version": PARK_FACTORS_VERSION}
