"""Point-in-time NCAAF team identity for the structural (v2-v5) feature extractor.

The history stores ESPN display names, and ESPN renames teams mid-season ("App State Mountaineers" /
"Appalachian State Mountaineers", "Hawai'i" / "Hawaii", "San José" / "San Jose" in 2026). Matching rows by the
exact display name split one team into two partial histories. Many daily-ingested 2026 rows carry no ESPN team
id, so the id alone cannot merge them either.

The name -> key rule is v6's ``TeamResolver`` (resolve_team canonical name, else a shared ESPN team id, else
``ID:<id>``, else ``NAME:<normalised name>``), reused rather than re-implemented; this module adds one step for
the id-less rows (a ``NAME:`` key whose normalised name matches a keyed history name takes that key) and the
serving-time lookup by the slate event's ESPN team id.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..domain import parse_utc
from .cfb_features_v6 import TeamResolver, _norm


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def row_start(row: Any) -> datetime | None:
    val = _get_val(row, "event_start_utc") or _get_val(row, "start")
    if val is None:
        return None
    return parse_utc(val) if isinstance(val, str) else val


def row_season(row: Any) -> int | None:
    """Fall season of a history row (January / February bowl games belong to the previous season)."""
    dt = row_start(row)
    sy = _get_val(row, "season_year")
    if dt and dt.month in (1, 2):
        if sy is not None and int(sy) == dt.year:
            return dt.year - 1
        if sy is not None:
            return int(sy)
        return dt.year - 1
    if sy is not None:
        return int(sy)
    if dt:
        return dt.year
    return None


def _team_id(row: Any, side: str) -> str | None:
    tid = _get_val(row, f"{side}_team_id")
    return str(tid) if tid not in (None, "") else None


@dataclass(frozen=True)
class _ResolverRow:
    """The attributes ``TeamResolver.from_games`` reads, so v6's resolver runs on structural history rows."""

    home: str
    away: str
    home_id: str | None
    away_id: str | None
    season: int
    game_class: str | None = None


class CFBTeamIdentity:
    """Display name -> canonical team key, built only from history rows before the decision time."""

    def __init__(self, resolver: TeamResolver, id_key: dict[str, str], norm_key: dict[str, str]) -> None:
        self._resolver = resolver
        self._id_key = id_key
        self._norm_key = norm_key
        self._cache: dict[str, str] = {}

    @classmethod
    def from_rows(cls, rows: Sequence[Any]) -> CFBTeamIdentity:
        adapted = []
        for r in rows:
            home, away = _get_val(r, "home_team") or "", _get_val(r, "away_team") or ""
            if not home or not away:
                continue
            adapted.append(
                _ResolverRow(home, away, _team_id(r, "home"), _team_id(r, "away"), row_season(r) or 0)
            )
        resolver = TeamResolver.from_games(adapted)  # type: ignore[arg-type]
        id_key: dict[str, str] = {}
        norm_key: dict[str, str] = {}
        for g in adapted:
            for name, tid in ((g.home, g.home_id), (g.away, g.away_id)):
                key = resolver.base_key(name)
                if tid is not None:
                    id_key.setdefault(tid, key)
                if not key.startswith("NAME:"):
                    norm_key.setdefault(_norm(name), key)
        return cls(resolver, id_key, norm_key)

    def key(self, name: str, team_id: str | None = None) -> str:
        if team_id is None:
            hit = self._cache.get(name)
            if hit is not None:
                return hit
        key = self._resolver.base_key(name)
        if key.startswith("NAME:"):
            # A slate name the history never used: the event's ESPN id is the identity when the history has it.
            if team_id is not None and team_id in self._id_key:
                key = self._id_key[team_id]
            else:
                key = self._norm_key.get(_norm(name), key)
        if team_id is None:
            self._cache[name] = key
        return key

    def row_keys(self, row: Any) -> tuple[str, str]:
        """(home key, away key) of a history row."""
        return (
            self.key(_get_val(row, "home_team") or "", _team_id(row, "home")),
            self.key(_get_val(row, "away_team") or "", _team_id(row, "away")),
        )
