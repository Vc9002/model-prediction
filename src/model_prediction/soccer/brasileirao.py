"""Brazilian Serie A (Brasileirao) per-league model config.

Parameters measured from the 218 completed BRASILEIRAO games in
data/historical/soccer_games_all.jsonl (2026-07-16..2026-10-18):
  away goals/game=0.9312, home/away goal ratio=1.6158, dc_rho=-0.05

`baseline` is the AWAY goals mean, not the both-sides mean: models/soccer.py
does not divide the away rate for BRASILEIRAO (home = baseline*home_advantage,
away = baseline), so this reproduces the league's real home/away goal means.
Replaces the 2026-10 values (baseline=1.0729, home_advantage=2.4333, rho=0.1)
that implied ~2.6 home goals before team strength and priced home wins at
~70% vs 48% actual. Walk-forward (80-game warmup, 138 test games) moneyline
log-loss: 1.1418 -> 1.0152; rho grid {0.1, 0.0, -0.05} picked on the same split.
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

BRASILEIRAO_CONFIG = LeagueSoccerConfig(
    league_code="BRASILEIRAO",
    model_version="soccer-brasileirao-poisson-dc-v1",
    baseline=0.9312,
    home_advantage=1.6158,
    dc_rho=-0.0500,
)
