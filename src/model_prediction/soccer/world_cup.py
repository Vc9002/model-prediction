"""FIFA World Cup / International Tournament per-league model config.

Parameters fit from historical match history:
  baseline=1.4903, home_advantage=1.3798, dc_rho=-0.2449
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

WORLD_CUP_CONFIG = LeagueSoccerConfig(
    league_code="WORLD_CUP",
    model_version="soccer-world_cup-poisson-dc-v1",
    baseline=1.4903,
    home_advantage=1.3798,
    dc_rho=-0.2449,
)
