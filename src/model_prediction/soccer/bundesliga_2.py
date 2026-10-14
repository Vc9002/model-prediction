"""German 2. Bundesliga per-league model config.

Parameters fit from historical match history:
  baseline=1.4657, home_advantage=1.2538, dc_rho=0.0092
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

BUNDESLIGA_2_CONFIG = LeagueSoccerConfig(
    league_code="BUNDESLIGA_2",
    model_version="soccer-bundesliga_2-poisson-dc-v1",
    baseline=1.4657,
    home_advantage=1.2538,
    dc_rho=0.0092,
)
