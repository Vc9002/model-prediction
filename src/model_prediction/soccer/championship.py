"""English Championship per-league model config.

Numbers fit from scripts/soccer_league_split_fit.py against
data/processed/soccer/games.jsonl, TRAIN+VALIDATION window only:
  baseline=1.2672, home_advantage=1.3116, dc_rho=0.0
  (n=1121 train+val games, 311 validation games, val log-loss 1.112670).
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

CHAMPIONSHIP_CONFIG = LeagueSoccerConfig(
    league_code="CHAMPIONSHIP",
    model_version="soccer-championship-poisson-dc-v1",
    baseline=1.2672,
    home_advantage=1.3116,
    dc_rho=0.0,
)
