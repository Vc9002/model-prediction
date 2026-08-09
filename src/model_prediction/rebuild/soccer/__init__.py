"""Soccer raw-source, normalized-contract, and point-in-time foundation."""

from .normalize import normalize_soccer_matches
from .pit import eligible_matches_as_of, prior_team_matches_as_of
from .store import SoccerNormalizedStore

__all__ = [
    "SoccerNormalizedStore",
    "eligible_matches_as_of",
    "normalize_soccer_matches",
    "prior_team_matches_as_of",
]
