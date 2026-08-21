"""Per-league champion resolution (research-tier only).

Maps a competition code to its per-league Poisson-Dixon-Coles config and
model. This is the read side of docs/RESEARCH_BACKLOG.md P2's proposed
`competition` dimension for champion identity -- but it is deliberately
NOT wired into production_registry.py or soccer_forward.py yet. The
backlog itself says that dimension is a "MODEL phase infrastructure
extension, not during consolidation"; this registry exists so the
per-league candidates can be backtested side-by-side with the global
incumbent (scripts/soccer_league_split_eval.py) without touching the
serving path.

Known follow-ups, recorded here rather than silently papered over:
  - CHAMPIONSHIP / EREDIVISIE / PRIMEIRA_LIGA / LIGUE_1 have 750-1400
    games each in the local cache and deserve their own per-league fits;
    they currently resolve to the thin-league fallback.
  - The genuinely thin South American competitions (<=6 games each in
    the local cache) are un-fittable at ANY per-league level and stay on
    the shrinkage fallback by design.
"""

from __future__ import annotations

from dataclasses import replace

from .bundesliga import BUNDESLIGA_CONFIG
from .epl import EPL_CONFIG
from .la_liga import LA_LIGA_CONFIG
from .league_model import LeagueSoccerConfig, LeagueSoccerModel
from .mls import MLS_CONFIG
from .other import OTHER_CONFIG
from .serie_a import SERIE_A_CONFIG
from .ucl import UCL_CONFIG

_NAMED_CONFIGS: dict[str, LeagueSoccerConfig] = {
    config.league_code: config
    for config in (EPL_CONFIG, LA_LIGA_CONFIG, BUNDESLIGA_CONFIG, SERIE_A_CONFIG, MLS_CONFIG, UCL_CONFIG)
}


def resolve(league_code: str) -> LeagueSoccerConfig:
    """Per-league config for a competition code.

    Named leagues get their independently fitted config; anything else
    falls back to the hierarchical-shrinkage thin-league config with the
    league code stamped in so its history filter works.
    """
    code = str(league_code).upper()
    config = _NAMED_CONFIGS.get(code)
    if config is not None:
        return config
    return replace(OTHER_CONFIG, league_code=code)


def model_for(league_code: str) -> LeagueSoccerModel:
    return LeagueSoccerModel(resolve(league_code))


def named_league_codes() -> tuple[str, ...]:
    return tuple(sorted(_NAMED_CONFIGS))
