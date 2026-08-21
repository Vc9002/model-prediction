"""UCL per-league model config.

Numbers frozen from scripts/soccer_league_split_fit.py (2026-08-18, run
against data/processed/soccer/games.jsonl, TRAIN+VALIDATION window only --
the locked holdout was never used for fitting):
  baseline=1.6323, home_advantage=1.3060 (both measured), dc_rho=0.0
  (grid-searched on the validation split only, 3-way log-loss, n=344
  train+val games, 127 validation games, best val log-loss 0.986366).
Do NOT hand-edit these numbers without re-running that fit and recording
the change here -- this module is the audit record.
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

UCL_CONFIG = LeagueSoccerConfig(
    league_code="UCL",
    model_version="soccer-ucl-poisson-dc-v1",
    baseline=1.6323,
    home_advantage=1.3060,
    dc_rho=0.0,
)
