"""Fail-closed NCAAB team crosswalk from an external source's team identity to ESPN team ids.

ESPN ids are the identity the NCAAB pipeline uses (``scoreboard.jsonl``, ``ncaab_games_all.jsonl``).
External rating sources (Kaggle Massey ordinals, Torvik via cbbdata) name teams their own way
("Connecticut", "Miami FL", "St John's"), and this repo has already shipped a real collision bug
(UConn vs Central Connecticut, UMass vs UMass Lowell, 2026-11-18). So matching here is deliberately
dumb and strict:

- **Exact normalised alias match only.** ESPN aliases are each team's ``location``,
  ``displayName``, ``shortDisplayName`` and ``nickname``; abbreviations are excluded ("MIA" style
  codes collide). No substring, prefix, mascot-stripping or fuzzy matching.
- **Every spelling of a source team must agree.** A source team with several spellings is matched
  only if all spellings that hit anything hit the same ESPN id; otherwise it is ambiguous.
- **Injective.** If two source teams resolve to one ESPN id, both are declined.
- **Overrides are explicit** (normalised source spelling -> ESPN id) and win over aliases.

Anything else returns ``espn_team_id=None`` with a ``NO_CALL_*`` reason, never a guess.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

MATCHED_ALIAS = "MATCHED_ALIAS"
MATCHED_OVERRIDE = "MATCHED_OVERRIDE"
NO_CALL_UNMATCHED = "NO_CALL_CROSSWALK_UNMATCHED"
NO_CALL_AMBIGUOUS = "NO_CALL_CROSSWALK_AMBIGUOUS"
NO_CALL_COLLISION = "NO_CALL_CROSSWALK_COLLISION"  # two source teams claim one ESPN id

# Verified against ESPN's team list (data/research/ncaab_external/espn_teams/teams.json, 2026-11-20).
# Only spellings exact alias matching cannot reach, or that external sources commonly use for the
# known collision traps. Keys are normalised (see ``normalize_team_name``).
DEFAULT_OVERRIDES: dict[str, str] = {
    "connecticut": "41",  # UConn; ESPN never uses the long form
    "uconn": "41",
    "central connecticut": "2115",
    "central conn": "2115",
    "central connecticut st": "2115",
    "massachusetts": "113",
    "umass": "113",
    "umass lowell": "2349",
    "massachusetts lowell": "2349",
    "mass lowell": "2349",
    "miami fl": "2390",
    "miami florida": "2390",
    "miami oh": "193",
    "miami ohio": "193",
    "southern california": "30",
    "usc": "30",
    "st johns": "2599",
    "st johns ny": "2599",
    "saint johns": "2599",
    "st marys ca": "2608",
    "saint marys ca": "2608",
    "saint marys": "2608",
    "loyola chicago": "2350",
    "loyola il": "2350",
    "loyola illinois": "2350",
    "loyola md": "2352",
    "loyola maryland": "2352",
    "loyola marymount": "2351",
    "lmu": "2351",
}

_PUNCT = re.compile(r"[.'’`()\-/,]")
_SPACES = re.compile(r"\s+")


def normalize_team_name(name: str) -> str:
    """Lowercase, '&' -> 'and', drop punctuation, collapse spaces. 'St.' is NOT expanded:
    it means Saint in "St. John's" and State in "Iowa St.", so expanding it would invent matches."""
    text = str(name).lower().replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


@dataclass(frozen=True)
class EspnTeam:
    team_id: str
    display_name: str
    location: str
    short_display_name: str
    nickname: str

    def aliases(self) -> set[str]:
        names = (self.display_name, self.location, self.short_display_name, self.nickname)
        return {normalize_team_name(n) for n in names if n}


@dataclass(frozen=True)
class CrosswalkResult:
    espn_team_id: str | None
    reason: str
    detail: str = ""


def load_espn_teams(path: str | Path) -> list[EspnTeam]:
    """Read the cached ESPN team list (``{"teams": [{id, location, displayName, ...}]}``)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        EspnTeam(
            team_id=str(row["id"]),
            display_name=str(row.get("displayName") or ""),
            location=str(row.get("location") or ""),
            short_display_name=str(row.get("shortDisplayName") or ""),
            nickname=str(row.get("nickname") or ""),
        )
        for row in payload["teams"]
    ]


class TeamCrosswalk:
    """Resolves source spellings to one ESPN team id or declines with a reason."""

    def __init__(self, espn_teams: Iterable[EspnTeam], overrides: Mapping[str, str] | None = None) -> None:
        self.teams = {team.team_id: team for team in espn_teams}
        self.overrides = {
            normalize_team_name(k): str(v)
            for k, v in (DEFAULT_OVERRIDES if overrides is None else overrides).items()
        }
        unknown = sorted({v for v in self.overrides.values() if v not in self.teams})
        if unknown:
            raise ValueError(f"crosswalk overrides point at unknown ESPN ids: {unknown}")
        self._alias_index: dict[str, set[str]] = {}
        for team in self.teams.values():
            for alias in team.aliases():
                self._alias_index.setdefault(alias, set()).add(team.team_id)

    def candidates(self, spelling: str) -> set[str]:
        key = normalize_team_name(spelling)
        if key in self.overrides:
            return {self.overrides[key]}
        return set(self._alias_index.get(key, set()))

    def resolve(self, spellings: Iterable[str]) -> CrosswalkResult:
        """One source team (all its known spellings) -> one ESPN id, or a NO_CALL reason."""
        hits: set[str] = set()
        via_override = False
        names = [s for s in spellings if s and str(s).strip()]
        for spelling in names:
            found = self.candidates(spelling)
            if normalize_team_name(spelling) in self.overrides:
                via_override = True
            hits |= found
        if not hits:
            return CrosswalkResult(None, NO_CALL_UNMATCHED, "; ".join(sorted(set(names)))[:200])
        if len(hits) > 1:
            return CrosswalkResult(None, NO_CALL_AMBIGUOUS, ",".join(sorted(hits)))
        return CrosswalkResult(next(iter(hits)), MATCHED_OVERRIDE if via_override else MATCHED_ALIAS)

    def build_mapping(self, source_teams: Mapping[str, Iterable[str]]) -> dict[str, CrosswalkResult]:
        """Resolve every source team, then decline every ESPN id claimed by more than one source id."""
        results = {str(source_id): self.resolve(spellings) for source_id, spellings in source_teams.items()}
        claims: dict[str, list[str]] = {}
        for source_id, result in results.items():
            if result.espn_team_id is not None:
                claims.setdefault(result.espn_team_id, []).append(source_id)
        for espn_id, source_ids in claims.items():
            if len(source_ids) > 1:
                for source_id in source_ids:
                    results[source_id] = CrosswalkResult(
                        None, NO_CALL_COLLISION, f"espn {espn_id} claimed by {sorted(source_ids)}"
                    )
        return results
