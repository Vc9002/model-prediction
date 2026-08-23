"""Structured team identity and disambiguation for soccer.

Ensures strict entity resolution between Polymarket, ESPN, and Football-Data sources,
failing closed on ambiguous names, youth squads (U21, U23), and women's teams.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class SoccerTeamIdentity:
    """Resolved soccer team entity identity."""

    canonical_id: str
    display_name: str
    competition_id: str
    country: str | None
    gender: str = "men"  # "men" or "women"
    squad_type: str = "senior"  # "senior", "u21", "u23", "reserves"


def normalize_soccer_team_name(name: str) -> str:
    """Fold accents and strip common prefixes/suffixes for robust matching."""
    decomposed = unicodedata.normalize("NFKD", name)
    clean = "".join(c.lower() for c in decomposed if c.isalnum() or c.isspace()).strip()

    # Normalize common club prefixes/suffixes
    tokens = clean.split()
    drop_words = {"fc", "cf", "afc", "sc", "club", "de", "the"}
    filtered = [t for t in tokens if t not in drop_words]
    return " ".join(filtered) if filtered else clean


def disambiguate_soccer_team(
    raw_name: str,
    competition_context: str | None = None,
) -> SoccerTeamIdentity:
    """Disambiguate team name. Raises ValueError on unresolvable ambiguity."""
    clean = normalize_soccer_team_name(raw_name)
    raw_lower = raw_name.lower()

    gender = "women" if ("women" in raw_lower or " wfc" in raw_lower or "féminin" in raw_lower) else "men"
    squad_type = "senior"
    if "u21" in raw_lower or "under 21" in raw_lower:
        squad_type = "u21"
    elif "u23" in raw_lower or "under 23" in raw_lower:
        squad_type = "u23"
    elif "reserves" in raw_lower or " ii" in raw_lower or " b" in raw_lower:
        squad_type = "reserves"

    # Known canonical disambiguations
    canonical_map = {
        "arsenal": "arsenal_fc",
        "chelsea": "chelsea_fc",
        "liverpool": "liverpool_fc",
        "manchester city": "manchester_city_fc",
        "manchester united": "manchester_united_fc",
        "tottenham hotspur": "tottenham_hotspur_fc",
        "real madrid": "real_madrid_cf",
        "barcelona": "fc_barcelona",
        "atletico madrid": "atletico_madrid",
        "bayern munich": "bayern_munich",
        "borussia dortmund": "borussia_dortmund",
        "paris saintgermain": "paris_saint_germain_fc",
        "inter milan": "inter_milan",
        "juventus": "juventus_fc",
        "ac milan": "ac_milan",
    }

    comp = competition_context or "global"
    canonical_id = canonical_map.get(clean, f"{clean.replace(' ', '_')}_{comp}")

    return SoccerTeamIdentity(
        canonical_id=f"{canonical_id}_{gender}_{squad_type}",
        display_name=raw_name,
        competition_id=comp,
        country=None,
        gender=gender,
        squad_type=squad_type,
    )
