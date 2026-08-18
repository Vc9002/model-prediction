"""Serie A per-league model config.

Numbers frozen from scripts/soccer_league_split_fit.py (2026-08-18, run
against data/processed/soccer/games.jsonl, TRAIN+VALIDATION window only --
the locked holdout was never used for fitting):
  baseline=1.2738, home_advantage=1.1130 (both measured), dc_rho=-0.05
  (grid-searched on the validation split only, 3-way log-loss, n=778
  train+val games, 220 validation games, best val log-loss 1.033410).
Do NOT hand-edit these numbers without re-running that fit and recording
the change here -- this module is the audit record.
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

SERIE_A_CONFIG = LeagueSoccerConfig(
    league_code="SERIE_A",
    model_version="soccer-serie-a-poisson-dc-v1",
    baseline=1.2738,
    home_advantage=1.1130,
    dc_rho=-0.05,
)
