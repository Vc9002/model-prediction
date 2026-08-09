"""Soccer raw-source, normalized-contract, and point-in-time foundation."""

from .audit import audit_soccer_data
from .normalize import normalize_soccer_matches
from .pit import eligible_matches_as_of, prior_team_matches_as_of
from .rights import assert_economic_use_allowed, assert_research_shadow_allowed
from .store import SoccerNormalizedStore

__all__ = [
    "SoccerNormalizedStore",
    "assert_economic_use_allowed",
    "assert_research_shadow_allowed",
    "audit_soccer_data",
    "eligible_matches_as_of",
    "normalize_soccer_matches",
    "prior_team_matches_as_of",
]
