from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .domain import League


class EntityResolutionError(ValueError):
    pass


def normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


@dataclass(frozen=True)
class SourceTeamAlias:
    source: str
    source_team_id: str | None
    source_name: str
    normalized_alias: str
    valid_from: str | None = None
    valid_to: str | None = None


@dataclass(frozen=True)
class CanonicalTeam:
    canonical_team_id: str
    league: League
    canonical_name: str
    abbreviation: str
    active: bool
    valid_from: str | None
    valid_to: str | None
    aliases: tuple[SourceTeamAlias, ...]


class EntityRegistry:
    def __init__(self, teams: Iterable[CanonicalTeam], version: str) -> None:
        self.version = version
        self._teams = tuple(teams)
        self._by_id = {team.canonical_team_id: team for team in self._teams}
        self._aliases: dict[tuple[League, str], list[tuple[CanonicalTeam, str | None, str | None]]] = {}
        for team in self._teams:
            candidates = {team.canonical_team_id, team.canonical_name, team.abbreviation}
            for candidate in candidates:
                self._aliases.setdefault((team.league, normalize_alias(candidate)), []).append(
                    (team, team.valid_from, team.valid_to)
                )
            for alias in team.aliases:
                self._aliases.setdefault((team.league, alias.normalized_alias), []).append(
                    (team, alias.valid_from or team.valid_from, alias.valid_to or team.valid_to)
                )

    @classmethod
    def from_json(cls, path: str | Path) -> EntityRegistry:
        with Path(path).open(encoding="utf-8") as handle:
            payload = json.load(handle)
        teams = []
        for item in payload["teams"]:
            aliases = tuple(
                SourceTeamAlias(
                    source=alias.get("source", "common"),
                    source_team_id=alias.get("source_team_id"),
                    source_name=alias["source_name"],
                    normalized_alias=normalize_alias(alias["source_name"]),
                    valid_from=alias.get("valid_from"),
                    valid_to=alias.get("valid_to"),
                )
                for alias in item.get("aliases", [])
            )
            teams.append(
                CanonicalTeam(
                    canonical_team_id=item["canonical_team_id"],
                    league=League(item["league"]),
                    canonical_name=item["canonical_name"],
                    abbreviation=item["abbreviation"],
                    active=item.get("active", True),
                    valid_from=item.get("valid_from"),
                    valid_to=item.get("valid_to"),
                    aliases=aliases,
                )
            )
        return cls(teams, str(payload["schema_version"]))

    def resolve(self, league: League | str, value: str, at_utc: str | None = None) -> CanonicalTeam:
        resolved_league = League(league)
        key = (resolved_league, normalize_alias(value))
        candidates = self._aliases.get(key, [])
        if at_utc is not None:
            candidates = [
                candidate for candidate in candidates if self._valid_at(candidate[1], candidate[2], at_utc)
            ]
        matches_by_id = {candidate[0].canonical_team_id: candidate[0] for candidate in candidates}
        matches = list(matches_by_id.values())
        if not matches:
            raise EntityResolutionError(f"unknown {resolved_league.value} team or alias: {value!r}")
        if len(matches) > 1:
            names = ", ".join(team.canonical_name for team in matches)
            raise EntityResolutionError(f"ambiguous {resolved_league.value} team alias {value!r}: {names}")
        return matches[0]

    @staticmethod
    def _valid_at(valid_from: str | None, valid_to: str | None, at_utc: str) -> bool:
        at = EntityRegistry._parse_boundary(at_utc)
        start = EntityRegistry._parse_boundary(valid_from) if valid_from else None
        end = EntityRegistry._parse_boundary(valid_to, end_of_day=True) if valid_to else None
        return (start is None or at >= start) and (end is None or at <= end)

    @staticmethod
    def _parse_boundary(value: str, end_of_day: bool = False) -> datetime:
        if "T" not in value:
            suffix = "T23:59:59.999999+00:00" if end_of_day else "T00:00:00+00:00"
            return datetime.fromisoformat(value + suffix)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def by_id(self, canonical_team_id: str) -> CanonicalTeam:
        try:
            return self._by_id[canonical_team_id]
        except KeyError as error:
            raise EntityResolutionError(f"unknown canonical team id: {canonical_team_id!r}") from error

    def teams(self, league: League | None = None) -> tuple[CanonicalTeam, ...]:
        if league is None:
            return self._teams
        return tuple(team for team in self._teams if team.league is league)
