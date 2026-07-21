"""Feature registry, on-disk caching, and point-in-time integrity.

Every feature snapshot is computed from locally cached game records only
(``data/processed/{sport}/games.jsonl``). Nothing here makes a network call.

Point-in-time rule: a feature computed "as of" date T may read only games whose
start timestamp is strictly before T's cutoff. ``games_before`` is the single
chokepoint that enforces this; every feature module must go through it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..domain import EASTERN, parse_utc


FeatureFunction = Callable[["FeatureContext"], dict[str, Any]]

_REGISTRY: dict[str, FeatureFunction] = {}


def register_feature(name: str) -> Callable[[FeatureFunction], FeatureFunction]:
    """Register a feature snapshot builder under a stable name."""

    def decorator(function: FeatureFunction) -> FeatureFunction:
        if name in _REGISTRY:
            raise ValueError(f"duplicate feature registration: {name}")
        _REGISTRY[name] = function
        return function

    return decorator


def registered_features() -> dict[str, FeatureFunction]:
    return dict(_REGISTRY)


@dataclass(frozen=True)
class GameRecord:
    """Normalized completed game used by every feature module."""

    event_id: str
    event_start_utc: str
    league: str
    away_team: str
    home_team: str
    away_score: int
    home_score: int
    season_type: str = "unknown"
    season_year: int | None = None
    competition_type: str = "unknown"

    @property
    def start(self) -> datetime:
        return parse_utc(self.event_start_utc)

    @property
    def total(self) -> int:
        return self.away_score + self.home_score

    @property
    def margin(self) -> int:
        """Home margin of victory (negative when the away team won)."""
        return self.home_score - self.away_score


@dataclass(frozen=True)
class FeatureContext:
    """Inputs available to a feature builder for one (sport, as_of_date) pair."""

    sport: str
    as_of_date: str
    games: tuple[GameRecord, ...]
    data_root: Path


class FeatureStore:
    """Reads processed games, enforces point-in-time cutoffs, caches snapshots."""

    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root)
        self._event_metadata_cache: dict[str, dict[str, dict[str, Any]]] = {}

    # ---------------------------------------------------------------- games

    def processed_path(self, sport: str) -> Path:
        return self.data_root / "processed" / sport.lower() / "games.jsonl"

    def load_games(self, sport: str) -> list[GameRecord]:
        path = self.processed_path(sport)
        if not path.exists():
            return []
        games: list[GameRecord] = []
        metadata = self._event_metadata(sport)
        seen: set[str] = set()
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                key = str(raw.get("event_id"))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    event_start_utc = str(raw["event_start_utc"])
                    event_start = parse_utc(event_start_utc)
                    event_metadata = metadata.get(key, {})
                    season_type = str(
                        raw.get("season_type") or event_metadata.get("season_type") or "unknown"
                    )
                    # Exhibition and All-Star results are a different data-generating
                    # process. They must not train MLB regular/postseason forecasts.
                    if sport.lower() == "mlb" and season_type in {"preseason", "all-star"}:
                        continue
                    # ESPN's historical MLB rows frequently omit season_type.
                    # The project governance rule therefore limits the MLB
                    # modeling population to April 1 through October 31.
                    if sport.lower() == "mlb" and not _is_mlb_modeling_date(
                        event_start.astimezone(EASTERN).date()
                    ):
                        continue
                    games.append(
                        GameRecord(
                            event_id=key,
                            event_start_utc=event_start_utc,
                            league=str(raw.get("league", sport.upper())),
                            away_team=str(raw["away_team"]),
                            home_team=str(raw["home_team"]),
                            away_score=int(raw["away_score"]),
                            home_score=int(raw["home_score"]),
                            season_type=season_type,
                            season_year=_optional_int(
                                raw.get("season_year") or event_metadata.get("season_year")
                            ),
                            competition_type=str(
                                raw.get("competition_type")
                                or event_metadata.get("competition_type")
                                or "unknown"
                            ),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        games.sort(key=lambda game: game.start)
        return games

    def _event_metadata(self, sport: str) -> dict[str, dict[str, Any]]:
        """Recover season classification from immutable raw scoreboards.

        Older normalized rows predate the season fields. Reading their cached raw
        payloads keeps the append-only processed store intact while preventing
        Spring Training and exhibition leakage in current backtests.
        """
        key = sport.lower()
        if key in self._event_metadata_cache:
            return self._event_metadata_cache[key]
        metadata: dict[str, dict[str, Any]] = {}
        raw_root = self.data_root / "raw" / key
        if raw_root.exists():
            for path in raw_root.glob("*/scores_*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for event in payload.get("events", []):
                    event_id = str(event.get("id", ""))
                    if not event_id:
                        continue
                    competition = (event.get("competitions") or [{}])[0]
                    season = event.get("season") or {}
                    metadata[event_id] = {
                        "season_type": str(season.get("slug", "unknown")),
                        "season_year": season.get("year"),
                        "competition_type": str(
                            (competition.get("type") or {}).get("abbreviation", "unknown")
                        ),
                    }
        self._event_metadata_cache[key] = metadata
        return metadata

    def games_before(self, sport: str, as_of_date: str) -> list[GameRecord]:
        """Every cached completed game that started strictly before the as-of date.

        This is the point-in-time chokepoint: the cutoff is midnight US-Eastern
        at the START of ``as_of_date`` (the project's canonical calendar — see
        domain.EASTERN), so no same-ET-day or future information can leak into
        a snapshot computed for that date. Yesterday's late ET-evening games
        are included; under the previous UTC cutoff they were dropped.
        """
        cutoff = datetime.combine(date.fromisoformat(as_of_date), time.min, tzinfo=EASTERN)
        return [game for game in self.load_games(sport) if game.start < cutoff]

    # ------------------------------------------------------------- snapshots

    def snapshot_path(self, sport: str, as_of_date: str, name: str) -> Path:
        return self.data_root / "features" / sport.lower() / as_of_date / f"{name}.json"

    def compute(self, sport: str, as_of_date: str, name: str, refresh: bool = False) -> dict[str, Any]:
        """Compute (or load cached) one named feature snapshot."""
        function = _REGISTRY.get(name)
        if function is None:
            raise KeyError(f"unknown feature: {name}; registered: {sorted(_REGISTRY)}")
        path = self.snapshot_path(sport, as_of_date, name)
        if path.exists() and not refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        context = FeatureContext(
            sport=sport.lower(),
            as_of_date=as_of_date,
            games=tuple(self.games_before(sport, as_of_date)),
            data_root=self.data_root,
        )
        payload = function(context)
        snapshot = {
            "feature": name,
            "sport": sport.lower(),
            "as_of_date": as_of_date,
            "input_games": len(context.games),
            "payload": payload,
        }
        snapshot["computation_hash"] = computation_hash(snapshot)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return snapshot

    def compute_all(self, sport: str, as_of_date: str, names: Iterable[str] | None = None) -> dict[str, Any]:
        selected = list(names) if names is not None else sorted(_REGISTRY)
        return {name: self.compute(sport, as_of_date, name) for name in selected}


def computation_hash(snapshot: dict[str, Any]) -> str:
    canonical = {key: value for key, value in snapshot.items() if key != "computation_hash"}
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_mlb_modeling_date(game_date: date) -> bool:
    """Project-defined MLB modeling window: April 1 through October 31."""
    return date(game_date.year, 4, 1) <= game_date <= date(game_date.year, 10, 31)
