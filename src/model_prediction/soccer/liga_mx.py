"""Mexican Liga MX per-league model config.

Parameters fit from historical match history:
  baseline=1.7717, home_advantage=1.0633, dc_rho=0.1000
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

LIGA_MX_CONFIG = LeagueSoccerConfig(
    league_code="LIGA_MX",
    model_version="soccer-liga_mx-poisson-dc-v1",
    baseline=1.7717,
    home_advantage=1.0633,
    dc_rho=0.1000,
)
