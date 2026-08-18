"""MLS per-league model config.

Numbers frozen from scripts/soccer_league_split_fit.py (2026-08-18, run
against data/processed/soccer/games.jsonl, TRAIN+VALIDATION window only --
the locked holdout was never used for fitting):
  baseline=1.5312, home_advantage=1.2325 (both measured), dc_rho=-0.05
  (grid-searched on the validation split only, 3-way log-loss, n=1041
  train+val games, 219 validation games, best val log-loss 1.040145).
Do NOT hand-edit these numbers without re-running that fit and recording
the change here -- this module is the audit record.
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

MLS_CONFIG = LeagueSoccerConfig(
    league_code="MLS",
    model_version="soccer-mls-poisson-dc-v1",
    baseline=1.5312,
    home_advantage=1.2325,
    dc_rho=-0.05,
)
