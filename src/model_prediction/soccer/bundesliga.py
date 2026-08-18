"""Bundesliga per-league model config.

Numbers frozen from scripts/soccer_league_split_fit.py (2026-08-18, run
against data/processed/soccer/games.jsonl, TRAIN+VALIDATION window only --
the locked holdout was never used for fitting):
  baseline=1.5785, home_advantage=1.1369 (both measured), dc_rho=0.0
  (grid-searched on the validation split only, 3-way log-loss, n=618
  train+val games, 149 validation games, best val log-loss 0.959054).
Do NOT hand-edit these numbers without re-running that fit and recording
the change here -- this module is the audit record.
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

BUNDESLIGA_CONFIG = LeagueSoccerConfig(
    league_code="BUNDESLIGA",
    model_version="soccer-bundesliga-poisson-dc-v1",
    baseline=1.5785,
    home_advantage=1.1369,
    dc_rho=0.0,
)
