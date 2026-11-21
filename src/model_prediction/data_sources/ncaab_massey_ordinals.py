"""Kaggle "March Machine Learning Mania" Massey ordinals: external NCAAB ratings as of a date.

Source files (men's), from the Kaggle competition data
(``kaggle competitions download -c march-machine-learning-mania-2026``, saved under
``data/research/ncaab_external/kaggle_mm/raw/`` with a manifest by
``scripts/fetch_kaggle_march_mania.py``):

- ``MMasseyOrdinals.csv``: Season, RankingDayNum, SystemName, TeamID, OrdinalRank
- ``MSeasons.csv``: Season, DayZero (MM/DD/YYYY), ...
- ``MTeamSpellings.csv``: TeamNameSpelling, TeamID;  ``MTeams.csv``: TeamID, TeamName, ...

HISTORICAL ONLY. Kaggle publishes this once a year (the 2026 edition is dated 2026-03-16 and stops
at the end of the 2025-26 regular season). It cannot serve the live 2026-27 season; a live
model needs a live source (e.g. cbbdata, ``ncaab_cbbdata.py``). Status 2026-11-20: written against
the documented schema; the download was refused until the operator accepts the competition
rules, so it has not been run on the real files yet. ``_read_csv`` fails closed on unexpected
headers.

Point-in-time contract: a ranking's date is ``DayZero + RankingDayNum``. Kaggle documents that a
ranking may be used for games ON that day; this module is one day stricter and serves only rankings
dated STRICTLY before the decision date (date-only granularity, so a same-day ranking can never
leak), and declines with ``NO_CALL_*`` when the newest usable ranking is older than
``max_staleness_days`` (e.g. last March's final ranking for a November game).

Team identity: Kaggle TeamIDs map to ESPN ids through ``ncaab_team_crosswalk`` (exact aliases,
explicit overrides, injective, fail closed).
"""

from __future__ import annotations

import csv
import statistics
from bisect import bisect_left
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .ncaab_team_crosswalk import CrosswalkResult, EspnTeam, TeamCrosswalk

KAGGLE_COMPETITION = "march-machine-learning-mania-2026"
KAGGLE_RULES_URL = f"https://www.kaggle.com/competitions/{KAGGLE_COMPETITION}/rules"

# Candidate consensus systems (long-running computer rankings). Not yet checked against the real
# file: run ``MasseyOrdinals.system_coverage()`` and drop any system missing in a season before use.
CONSENSUS_SYSTEMS: tuple[str, ...] = ("POM", "SAG", "MAS", "MOR", "WLK", "DOL", "COL", "RTH")
DEFAULT_MAX_STALENESS_DAYS = 14  # most systems publish weekly or faster in-season
DEFAULT_MIN_SYSTEMS = 4

NO_CALL_NO_RANKING = "NO_CALL_MASSEY_NO_RANKING_BEFORE_DATE"
NO_CALL_STALE = "NO_CALL_MASSEY_RANKING_STALE"
NO_CALL_TOO_FEW_SYSTEMS = "NO_CALL_MASSEY_TOO_FEW_SYSTEMS"

_ORDINAL_HEADER = ["Season", "RankingDayNum", "SystemName", "TeamID", "OrdinalRank"]


def _check_header(path: Path, header: list[str], required: Iterable[str]) -> None:
    missing = [c for c in required if c not in header]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}; header {header}")


def _parse_day_zero(text: str) -> date:
    text = text.strip()
    if "/" in text:
        month, day, year = (int(part) for part in text.split("/"))
        return date(year, month, day)
    return date.fromisoformat(text[:10])


def load_day_zero(seasons_csv: str | Path) -> dict[int, date]:
    path = Path(seasons_csv)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _check_header(path, list(reader.fieldnames or []), ["Season", "DayZero"])
        return {int(row["Season"]): _parse_day_zero(row["DayZero"]) for row in reader}


@dataclass(frozen=True)
class OrdinalLookup:
    rank: int | None
    ranking_date: date | None
    reason: str


@dataclass(frozen=True)
class ConsensusLookup:
    median_rank: float | None
    systems_used: tuple[str, ...]
    reason: str


class MasseyOrdinals:
    """Rankings indexed by (system, TeamID) as date-sorted series."""

    def __init__(self, series: Mapping[tuple[str, int], list[tuple[date, int]]]) -> None:
        self._series = {key: sorted(values) for key, values in series.items()}
        self._dates = {key: [d for d, _ in values] for key, values in self._series.items()}

    @classmethod
    def from_csv(
        cls,
        ordinals_csv: str | Path,
        seasons_csv: str | Path,
        systems: Iterable[str] | None = CONSENSUS_SYSTEMS,
        seasons: Iterable[int] | None = None,
    ) -> MasseyOrdinals:
        """Stream the ~130 MB file, keeping only the requested systems and seasons."""
        day_zero = load_day_zero(seasons_csv)
        keep_systems = set(systems) if systems is not None else None
        keep_seasons = set(seasons) if seasons is not None else None
        path = Path(ordinals_csv)
        series: dict[tuple[str, int], list[tuple[date, int]]] = {}
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            _check_header(path, header, _ORDINAL_HEADER)
            idx = [header.index(c) for c in _ORDINAL_HEADER]
            for row in reader:
                season = int(row[idx[0]])
                system = row[idx[2]].strip()
                if keep_seasons is not None and season not in keep_seasons:
                    continue
                if keep_systems is not None and system not in keep_systems:
                    continue
                if season not in day_zero:
                    raise ValueError(f"season {season} has no DayZero in {Path(seasons_csv).name}")
                ranked_on = day_zero[season] + timedelta(days=int(row[idx[1]]))
                series.setdefault((system, int(row[idx[3]])), []).append((ranked_on, int(row[idx[4]])))
        return cls(series)

    def systems(self) -> set[str]:
        return {system for system, _ in self._series}

    def system_coverage(self, day_zero: Mapping[int, date]) -> dict[int, dict[str, int]]:
        """Season -> system -> number of teams ranked at least once that season."""
        starts = sorted(day_zero.items())
        out: dict[int, dict[str, set[int]]] = {}
        for (system, team), values in self._series.items():
            for ranked_on, _ in values:
                season = max((s for s, dz in starts if dz <= ranked_on), default=None)
                if season is not None:
                    out.setdefault(season, {}).setdefault(system, set()).add(team)
        return {s: {sys: len(teams) for sys, teams in sorted(v.items())} for s, v in sorted(out.items())}

    def rank_as_of(
        self, team_id: int, system: str, as_of: date, max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS
    ) -> OrdinalLookup:
        key = (system, int(team_id))
        dates = self._dates.get(key)
        if not dates:
            return OrdinalLookup(None, None, NO_CALL_NO_RANKING)
        pos = bisect_left(dates, as_of) - 1  # last ranking dated strictly before as_of
        if pos < 0:
            return OrdinalLookup(None, None, NO_CALL_NO_RANKING)
        ranked_on, rank = self._series[key][pos]
        if (as_of - ranked_on).days > max_staleness_days:
            return OrdinalLookup(None, ranked_on, NO_CALL_STALE)
        return OrdinalLookup(rank, ranked_on, "OK")

    def consensus_as_of(
        self,
        team_id: int,
        as_of: date,
        systems: Iterable[str] = CONSENSUS_SYSTEMS,
        min_systems: int = DEFAULT_MIN_SYSTEMS,
        max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS,
    ) -> ConsensusLookup:
        """Median rank over the fixed system list, from systems with a fresh ranking before ``as_of``."""
        used: list[tuple[str, int]] = []
        for system in systems:
            hit = self.rank_as_of(team_id, system, as_of, max_staleness_days)
            if hit.rank is not None:
                used.append((system, hit.rank))
        if len(used) < min_systems:
            return ConsensusLookup(None, tuple(s for s, _ in used), NO_CALL_TOO_FEW_SYSTEMS)
        return ConsensusLookup(float(statistics.median(r for _, r in used)), tuple(s for s, _ in used), "OK")


def load_kaggle_team_spellings(
    spellings_csv: str | Path, teams_csv: str | Path | None = None
) -> dict[str, list[str]]:
    """TeamID -> every known spelling (MTeamSpellings plus MTeams.TeamName when given)."""
    out: dict[str, list[str]] = {}
    path = Path(spellings_csv)
    with path.open(encoding="utf-8", newline="", errors="strict") as handle:
        reader = csv.DictReader(handle)
        _check_header(path, list(reader.fieldnames or []), ["TeamNameSpelling", "TeamID"])
        for row in reader:
            out.setdefault(str(int(row["TeamID"])), []).append(row["TeamNameSpelling"])
    if teams_csv is not None:
        tpath = Path(teams_csv)
        with tpath.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            _check_header(tpath, list(reader.fieldnames or []), ["TeamID", "TeamName"])
            for row in reader:
                out.setdefault(str(int(row["TeamID"])), []).append(row["TeamName"])
    return out


def build_kaggle_crosswalk(
    spellings: Mapping[str, Iterable[str]],
    espn_teams: Iterable[EspnTeam],
    overrides: Mapping[str, str] | None = None,
) -> dict[str, CrosswalkResult]:
    """Kaggle TeamID -> ESPN team id (or a NO_CALL reason). Injective by construction."""
    return TeamCrosswalk(espn_teams, overrides).build_mapping(spellings)


def manifest_entry(path: Path) -> dict[str, object]:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
        "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
    }
