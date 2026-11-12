"""Per-game NCAAB team statistics and AP ranks from the archived ESPN scoreboards.

The daily ingest writes ``data/raw/ncaab/<ET date>/scores_ncaab.json`` and rewrites it once the
day's games are final, so every completed game carries each team's box aggregates (field-goal,
three-point and free-throw attempts/makes, rebounds, assists, points) and the team's AP
``curatedRank`` as of that game. Offensive rebounds and turnovers are NOT in the scoreboard
feed, so possessions cannot be reconstructed exactly; ``shot_attempts`` (FGA + 0.44 * FTA) is
the tempo proxy this module offers instead.

Point-in-time contract: this module is keyed by ESPN event id and never filters by time itself.
Callers pass the games they are allowed to see (``FeatureStore.games_before`` output) and look up
only those ids, so a stats row can never be newer than the history it is attached to. Ranks are
"the rank the team carried into that game", so "last known rank before the cutoff" is the rank
attached to the team's most recent game in the caller's history.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..features.base import GameRecord

FREE_THROW_POSSESSION_WEIGHT = 0.44  # standard share of free-throw attempts that end a possession

_STAT_FIELDS = {
    "fieldGoalsAttempted": "fga",
    "fieldGoalsMade": "fgm",
    "threePointFieldGoalsAttempted": "tpa",
    "threePointFieldGoalsMade": "tpm",
    "freeThrowsAttempted": "fta",
    "freeThrowsMade": "ftm",
    "rebounds": "reb",
    "assists": "ast",
}


@dataclass(frozen=True)
class TeamGameStats:
    team: str
    points: int
    fga: int
    fgm: int
    tpa: int
    tpm: int
    fta: int
    ftm: int
    reb: int
    ast: int
    rank: int | None  # AP rank carried into the game, None when unranked

    @property
    def shot_attempts(self) -> float:
        """Possession proxy: field-goal attempts plus the possession-ending share of free throws."""
        return self.fga + FREE_THROW_POSSESSION_WEIGHT * self.fta


@dataclass(frozen=True)
class GameStats:
    event_id: str
    home: TeamGameStats
    away: TeamGameStats
    conference_game: bool
    neutral_site: bool

    @property
    def game_attempts(self) -> float:
        """Average of both teams' shot attempts: the game's tempo in possessions-per-team terms."""
        return (self.home.shot_attempts + self.away.shot_attempts) / 2.0


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _parse_competitor(comp: dict[str, Any]) -> TeamGameStats | None:
    team = (comp.get("team") or {}).get("displayName")
    if not team:
        return None
    stats = {s.get("name"): s.get("displayValue") for s in comp.get("statistics") or []}
    values: dict[str, int] = {}
    for espn_name, field in _STAT_FIELDS.items():
        v = _int(stats.get(espn_name))
        if v is None:
            return None
        values[field] = v
    points = _int(comp.get("score"))
    if points is None:
        return None
    rank = _int((comp.get("curatedRank") or {}).get("current"))
    return TeamGameStats(
        team=str(team),
        points=points,
        rank=rank if rank is not None and 1 <= rank <= 25 else None,
        **values,
    )


class NCAABScoreboardStats:
    """Loads every archived scoreboard once and serves per-event stats by ESPN event id."""

    def __init__(self, raw_root: Path | str | None = None) -> None:
        self.raw_root = (
            Path(raw_root) if raw_root else Path(__file__).resolve().parents[3] / "data" / "raw" / "ncaab"
        )
        self._by_event: dict[str, GameStats] | None = None

    def _load(self) -> dict[str, GameStats]:
        if self._by_event is not None:
            return self._by_event
        out: dict[str, GameStats] = {}
        for path in sorted(self.raw_root.glob("*/scores_ncaab.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for event in payload.get("events", []):
                comp = (event.get("competitions") or [{}])[0]
                if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
                    continue
                sides: dict[str, TeamGameStats] = {}
                for c in comp.get("competitors", []):
                    parsed = _parse_competitor(c)
                    if parsed is not None and c.get("homeAway") in ("home", "away"):
                        sides[str(c["homeAway"])] = parsed
                if "home" not in sides or "away" not in sides:
                    continue
                event_id = str(event.get("id"))
                # Later files re-emit the same final game; the content is identical, keep the first.
                out.setdefault(
                    event_id,
                    GameStats(
                        event_id=event_id,
                        home=sides["home"],
                        away=sides["away"],
                        conference_game=bool(comp.get("conferenceCompetition", False)),
                        neutral_site=bool(comp.get("neutralSite", False)),
                    ),
                )
        self._by_event = out
        return out

    def get(self, event_id: str) -> GameStats | None:
        return self._load().get(str(event_id))

    def for_history(self, history: Iterable[GameRecord]) -> list[tuple[GameRecord, GameStats]]:
        """Stats for exactly the games the caller may see, in the caller's order."""
        table = self._load()
        out: list[tuple[GameRecord, GameStats]] = []
        for game in history:
            stats = table.get(str(game.event_id))
            if stats is not None:
                out.append((game, stats))
        return out

    def last_known_ranks(self, history: Iterable[GameRecord]) -> dict[str, int]:
        """Each team's AP rank carried into its most recent game within ``history`` (ranked only)."""
        ranks: dict[str, int] = {}
        latest: dict[str, Any] = {}
        for game, stats in self.for_history(history):
            for side in (stats.home, stats.away):
                if side.team not in latest or game.start > latest[side.team]:
                    latest[side.team] = game.start
                    if side.rank is None:
                        ranks.pop(side.team, None)
                    else:
                        ranks[side.team] = side.rank
        return ranks

    def coverage(self, history: Iterable[GameRecord]) -> float:
        games = list(history)
        if not games:
            return 0.0
        return len(self.for_history(games)) / len(games)
