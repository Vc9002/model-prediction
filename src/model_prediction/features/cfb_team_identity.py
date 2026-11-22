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
        # An ESPN id the history knows wins over the name: the slate says "FIU Panthers" (a team-table name, so a
        # canonical key of its own) while FIU's history sits under "Florida International Panthers" / ID:2229.
        if team_id is not None and team_id in self._id_key:
            return self._id_key[team_id]
        hit = self._cache.get(name)
        if hit is not None:
            return hit
        key = self._resolver.base_key(name)
        if key.startswith("NAME:"):
            key = self._norm_key.get(_norm(name), key)
        self._cache[name] = key
        return key

    def row_keys(self, row: Any) -> tuple[str, str]:
        """(home key, away key) of a history row."""
        return (
            self.key(_get_val(row, "home_team") or "", _team_id(row, "home")),
            self.key(_get_val(row, "away_team") or "", _team_id(row, "away")),
        )


# ------------------------------------------------------------------ per-season division class
FBS = "FBS"
FCS = "FCS"
UNKNOWN = "UNKNOWN"
_FBS_FEED = 80
_FCS_FEED = 81


def _feed_groups(row: Any) -> tuple[int, ...]:
    groups = _get_val(row, "feed_groups")
    if not groups:
        return ()
    try:
        return tuple(sorted({int(x) for x in groups}))
    except (TypeError, ValueError):
        return ()


def _resolver_class(key: str) -> str | None:
    """FBS / FCS from the static team table, for a canonical key only (``ID:`` / ``NAME:`` keys are not in it)."""
    if key.startswith(("ID:", "NAME:")):
        return None
    from ..data_sources.cfb_data import resolve_team

    team = resolve_team(key)
    if team is None:
        return None
    return FCS if team.tier == "FCS" else FBS


class CFBTeamClasses:
    """FBS / FCS label per (team key, season), from the ESPN scoreboard feed that listed each game.

    ``scoreboard_ncaaf_divisions`` records the feed(s) listing each event: 80 only = FBS-FBS, 81 only = a
    lower-division game, 80 + 81 = FBS-FCS. Division membership is a schedule fact, but the labels are still
    built only from rows before the decision time. Legacy rows carry no feed groups and are NOT read as FBS:
    208 of them are FBS-FCS or FCS-FCS games.

    Order: this season's feed evidence, then last season's, then the static team table's tier, else
    ``UNKNOWN``. The static table alone labelled 1,703 of 1,782 FCS team-seasons FBS (every FCS team missing
    from it fell back to G6), so ``is_fbs_vs_fcs`` and the Main ``block_fcs`` gate failed open.
    """

    def __init__(self, labels: dict[tuple[str, int], str]) -> None:
        self._labels = labels

    @classmethod
    def from_keyed_rows(cls, keyed_rows: Sequence[tuple[Any, str, str]]) -> CFBTeamClasses:
        labels: dict[tuple[str, int], str] = {}
        pairs: list[tuple[str, str, int]] = []
        for row, home, away in keyed_rows:
            season = row_season(row)
            if season is None:
                continue
            groups = _feed_groups(row)
            if groups == (_FCS_FEED,):
                labels[(home, season)] = FCS
                labels[(away, season)] = FCS
            elif groups == (_FBS_FEED,):
                # A lower-division listing anywhere in the season wins: fail closed on a conflict.
                labels.setdefault((home, season), FBS)
                labels.setdefault((away, season), FBS)
            elif groups == (_FBS_FEED, _FCS_FEED):
                pairs.append((home, away, season))
        # An FBS-FCS game has exactly one FBS side: a labelled side labels the other.
        changed = True
        while changed:
            changed = False
            for home, away, season in pairs:
                lh, la = labels.get((home, season)), labels.get((away, season))
                if lh is not None and la is None:
                    labels[(away, season)] = FCS if lh == FBS else FBS
                    changed = True
                elif la is not None and lh is None:
                    labels[(home, season)] = FCS if la == FBS else FBS
                    changed = True
        for home, away, season in pairs:
            if (home, season) in labels or (away, season) in labels:
                continue
            th, ta = _resolver_class(home), _resolver_class(away)
            if th == FBS and ta != FBS:
                labels[(home, season)], labels[(away, season)] = FBS, FCS
            elif ta == FBS and th != FBS:
                labels[(away, season)], labels[(home, season)] = FBS, FCS
        return cls(labels)

    def classify(self, key: str, season: int) -> tuple[str, str]:
        """(FBS | FCS | UNKNOWN, source). Callers treat UNKNOWN as not-FBS."""
        hit = self._labels.get((key, season))
        if hit is not None:
            return hit, "feed"
        hit = self._labels.get((key, season - 1))
        if hit is not None:
            return hit, "previous_season_feed"
        resolved = _resolver_class(key)
        if resolved is not None:
            return resolved, "team_table"
        return UNKNOWN, "no_class_evidence"
