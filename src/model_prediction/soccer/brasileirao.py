"""Brazilian Serie A (Brasileirao) per-league model config.

Parameters fit from historical match history:
  baseline=1.0729, home_advantage=2.4333, dc_rho=0.1000
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

BRASILEIRAO_CONFIG = LeagueSoccerConfig(
    league_code="BRASILEIRAO",
    model_version="soccer-brasileirao-poisson-dc-v1",
    baseline=1.0729,
    home_advantage=2.4333,
    dc_rho=0.1000,
)
