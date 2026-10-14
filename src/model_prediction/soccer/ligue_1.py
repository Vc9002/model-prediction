"""French Ligue 1 per-league model config.

Numbers fit from scripts/soccer_league_split_fit.py against
data/processed/soccer/games.jsonl, TRAIN+VALIDATION window only:
  baseline=1.4644, home_advantage=1.1867, dc_rho=0.0
  (n=604 train+val games, 163 validation games, val log-loss 0.983146).
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

LIGUE_1_CONFIG = LeagueSoccerConfig(
    league_code="LIGUE_1",
    model_version="soccer-ligue_1-poisson-dc-v1",
    baseline=1.4644,
    home_advantage=1.1867,
    dc_rho=0.0,
)
