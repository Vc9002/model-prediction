from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcntl
import yaml

from .audit import AuditLog
from .domain import League
from .entities import EntityResolutionError
from .entities import CanonicalTeam, EntityRegistry


@dataclass(frozen=True)
class BanEntry:
    league: League
    canonical_team_id: str
    canonical_name: str
    configured_input: str
    reason: str = "manual_governance"
    review_after: str | None = None


class TeamBanList:
    def __init__(
        self,
        config_path: str | Path,
        registry: EntityRegistry,
        audit_log: AuditLog,
    ) -> None:
        self.config_path = Path(config_path)
        self.lock_path = self.config_path.with_suffix(self.config_path.suffix + ".lock")
        self.registry = registry
        self.audit_log = audit_log

    def _load(self) -> dict[str, Any]:
        with self.config_path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def _entries(self, config: dict[str, Any]) -> list[BanEntry]:
        section = config.get("team_ban_list", {})
        if not section.get("enabled", True):
            return []
        entries = []
        for league_value, values in section.get("teams", {}).items():
            league = League(league_value)
            for value in values or []:
                input_value = (
                    value.get("input", value.get("canonical_team_id")) if isinstance(value, dict) else value
                )
                try:
                    team = self.registry.resolve(league, str(input_value))
                except EntityResolutionError:
                    self.audit_log.append(
                        "ban_entry_unresolved",
                        str(input_value),
                        {"league": league.value, "input_alias": str(input_value)},
                    )
                    continue
                reason = (
                    value.get("reason", "manual_governance")
                    if isinstance(value, dict)
                    else "manual_governance"
                )
                review_after = value.get("review_after") if isinstance(value, dict) else None
                entries.append(
                    BanEntry(
                        league,
                        team.canonical_team_id,
                        team.canonical_name,
                        str(input_value),
                        reason,
                        review_after,
                    )
                )
        return entries

    def list(self) -> list[BanEntry]:
        return self._entries(self._load())

    def check(self, league: League | str, team_input: str) -> tuple[CanonicalTeam, bool]:
        team = self.registry.resolve(League(league), team_input)
        banned_ids = {entry.canonical_team_id for entry in self.list()}
        return team, team.canonical_team_id in banned_ids

    def add(
        self,
        league: League | str,
        team_input: str,
        reason: str = "manual_governance",
        review_after: str | None = None,
    ) -> tuple[BanEntry, bool]:
        return self._mutate(League(league), team_input, add=True, reason=reason, review_after=review_after)

    def remove(self, league: League | str, team_input: str) -> tuple[BanEntry, bool]:
        return self._mutate(League(league), team_input, add=False)

    def _mutate(
        self,
        league: League,
        team_input: str,
        add: bool,
        reason: str = "manual_governance",
        review_after: str | None = None,
    ) -> tuple[BanEntry, bool]:
        team = self.registry.resolve(league, team_input)
        entry = BanEntry(
            league, team.canonical_team_id, team.canonical_name, team_input, reason, review_after
        )
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                config = self._load()
                section = config.setdefault("team_ban_list", {"enabled": True, "teams": {}})
                allowed_reasons = set(section.get("allowed_reasons", ["manual_governance"]))
                if reason not in allowed_reasons:
                    raise ValueError(f"ban reason must be one of {sorted(allowed_reasons)}")
                teams = section.setdefault("teams", {})
                values = list(teams.setdefault(league.value, []))
                resolved_ids = []
                existing_value: Any = None
                for value in values:
                    raw = (
                        value.get("input", value.get("canonical_team_id"))
                        if isinstance(value, dict)
                        else value
                    )
                    resolved_id = self.registry.resolve(league, str(raw)).canonical_team_id
                    resolved_ids.append(resolved_id)
                    if resolved_id == team.canonical_team_id:
                        existing_value = value
                previous = team.canonical_team_id in resolved_ids
                if not add and isinstance(existing_value, dict):
                    reason = existing_value.get("reason", reason)
                    review_after = existing_value.get("review_after", review_after)
                    entry = BanEntry(
                        league,
                        team.canonical_team_id,
                        team.canonical_name,
                        team_input,
                        reason,
                        review_after,
                    )
                if add and not previous:
                    values.append(
                        {
                            "canonical_team_id": team.canonical_team_id,
                            "input": team_input,
                            "reason": reason,
                            "review_after": review_after,
                        }
                    )
                elif not add and previous:
                    values = [
                        value
                        for value in values
                        if self.registry.resolve(
                            league,
                            str(
                                value.get("input", value.get("canonical_team_id"))
                                if isinstance(value, dict)
                                else value
                            ),
                        ).canonical_team_id
                        != team.canonical_team_id
                    ]
                teams[league.value] = values
                resulting = add or (previous and add)
                changed = previous != resulting
                if changed:
                    self._atomic_write(config)
                    self.audit_log.append(
                        "ban_team_added" if add else "ban_team_removed",
                        team.canonical_team_id,
                        {
                            "action": "add" if add else "remove",
                            "league": league.value,
                            "canonical_team_id": team.canonical_team_id,
                            "canonical_team_name": team.canonical_name,
                            "input_alias": team_input,
                            "previous_state": previous,
                            "resulting_state": add,
                            "reason": reason,
                            "review_after": review_after,
                            "configuration_schema_version": str(config.get("schema_version", "legacy")),
                        },
                    )
                return entry, changed
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _atomic_write(self, config: dict[str, Any]) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix="model-", suffix=".yaml", dir=self.config_path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                yaml.safe_dump(config, handle, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.config_path)
        except Exception:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise
