"""Tennis raw-first, point-in-time data foundation.

Built directly against the real TennisMyLife (historical/ongoing matches)
and ESPN (live scoreboard) providers -- the archived `origin/rebuild/
tennis-v1` branch predates their existence and is a fail-closed policy stub
acknowledging the dead Sackmann source, not real ingestion code, so this
package is new authorship rather than a curated port (see
`docs/rebuild/OPERATIONS.md`)."""

from .audit import audit_tennis_data
from .foundation import TennisFoundation
from .normalize import normalize_espn_scoreboard, normalize_tennismylife_matches
from .pit import eligible_matches_as_of, eligible_prior_matches_for_player
from .store import TennisNormalizedStore

__all__ = [
    "TennisFoundation",
    "TennisNormalizedStore",
    "audit_tennis_data",
    "eligible_matches_as_of",
    "eligible_prior_matches_for_player",
    "normalize_espn_scoreboard",
    "normalize_tennismylife_matches",
]
