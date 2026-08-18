"""Thin-league fallback config: hierarchical shrinkage toward a global
soccer prior (docs/RESEARCH_BACKLOG.md P2: "Thin leagues use hierarchical
shrinkage toward a global soccer prior (theta = w*theta_league +
(1-w)*theta_global, w from sample size)").

The global prior itself is measured from ALL soccer history (the same
all-leagues pool the incumbent soccer-poisson-dc-v1 uses at call time):
  baseline=1.4562 (global mean goals/team), home_advantage=1.1865
  (global home/away goal ratio), dc_rho=-0.10 (the incumbent's value).
With shrinkage_prior_games=30, team strengths for a league with n<30
games per team are pulled hard toward neutral; leagues that cross ~30
team-games increasingly trust their own numbers. This covers both
genuinely thin leagues (the ~5-game South American competitions in
config/model.yaml's SOCCER.leagues) and the mid-size unnamed leagues
(CHAMPIONSHIP/EREDIVISIE/PRIMEIRA_LIGA/LIGUE_1 have 750-1400 games each
and deserve their own per-league fits -- flagged in registry.py as the
natural next pass, not silently lumped into this fallback forever).
"""

from __future__ import annotations

from .league_model import LeagueSoccerConfig

OTHER_CONFIG = LeagueSoccerConfig(
    league_code="",
    model_version="soccer-other-poisson-dc-v1",
    baseline=1.4562,
    home_advantage=1.1865,
    dc_rho=-0.10,
    shrinkage_prior_games=30.0,
    global_baseline=1.4562,
)
