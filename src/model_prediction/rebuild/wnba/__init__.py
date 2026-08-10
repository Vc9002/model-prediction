"""WNBA free-data foundation (data and PIT contracts only; no promoted model).

Deliberately excludes the source branch's features.py/horizon_builder.py/
baselines.py -- those are feature-engineering and model-baseline modules,
a different concern from backfill/audit data ingestion, and are left for a
separate, explicit decision (feature/model work belongs behind
model_lifecycle.py's rebuild-model seam, not folded silently into this
rebuild-data transplant)."""

from .audit import audit_wnba_season
from .foundation import WNBAFoundation
from .normalize import normalize_wnba_table
from .pit import eligible_prior_team_games

__all__ = [
    "WNBAFoundation",
    "audit_wnba_season",
    "eligible_prior_team_games",
    "normalize_wnba_table",
]
