"""Dutch Eredivisie per-league model config.

Numbers fit from scripts/soccer_league_split_fit.py against
data/processed/soccer/games.jsonl, TRAIN+VALIDATION window only:
  baseline=1.5575, home_advantage=1.2746, dc_rho=-0.15
  (n=617 train+val games, 164 validation games, val log-loss 0.952505).
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

EREDIVISIE_CONFIG = LeagueSoccerConfig(
    league_code="EREDIVISIE",
    model_version="soccer-eredivisie-poisson-dc-v1",
    baseline=1.5575,
    home_advantage=1.2746,
    dc_rho=-0.15,
)
