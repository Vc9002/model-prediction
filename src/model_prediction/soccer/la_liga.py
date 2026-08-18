"""La Liga per-league model config.

Numbers frozen from scripts/soccer_league_split_fit.py (2026-08-18, run
against data/processed/soccer/games.jsonl, TRAIN+VALIDATION window only --
the locked holdout was never used for fitting):
  baseline=1.3064, home_advantage=1.2754 (both measured), dc_rho=-0.05
  (grid-searched on the validation split only, 3-way log-loss, n=762
  train+val games, 185 validation games, best val log-loss 0.987733).
Do NOT hand-edit these numbers without re-running that fit and recording
the change here -- this module is the audit record.
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

LA_LIGA_CONFIG = LeagueSoccerConfig(
    league_code="LA_LIGA",
    model_version="soccer-la-liga-poisson-dc-v1",
    baseline=1.3064,
    home_advantage=1.2754,
    dc_rho=-0.05,
)
