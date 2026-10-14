"""Portuguese Primeira Liga per-league model config.

Numbers fit from scripts/soccer_league_split_fit.py against
data/processed/soccer/games.jsonl, TRAIN+VALIDATION window only:
  baseline=1.3355, home_advantage=1.1780, dc_rho=-0.10
  (n=614 train+val games, 157 validation games, val log-loss 0.976915).
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

PRIMEIRA_LIGA_CONFIG = LeagueSoccerConfig(
    league_code="PRIMEIRA_LIGA",
    model_version="soccer-primeira_liga-poisson-dc-v1",
    baseline=1.3355,
    home_advantage=1.1780,
    dc_rho=-0.10,
)
