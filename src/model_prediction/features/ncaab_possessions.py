"""Per-game NCAAB possessions from the captured team box-score components.

``data_sources/ncaab_boxscores.py`` captures, once per final game, each team's field-goal
attempts, free-throw attempts, offensive rebounds and turnovers (the raw components; it refuses to
infer a formula). This module applies the standard estimate

    possessions = FGA - OREB + TOV + 0.44 * FTA

per team and averages the two sides for the game's tempo. The scoreboard feed used by
``ncaab_scoreboard_stats`` lacks OREB and TOV, so its ``shot_attempts`` proxy over-counts every
offensive rebound and under-counts every turnover; this is the exact version.

Point-in-time contract: keyed by ESPN event id, no time filtering of its own. Callers pass the
history they may see and only those ids are served. Each file carries ``observed_at_utc``
(capture time); backfilled seasons were captured long after the games, so these are terminal
box scores and not vintaged snapshots -- fine for history features, never for a live decision
about a game that has not finished.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .base import GameRecord

FREE_THROW_POSSESSION_WEIGHT = 0.44


@dataclass(frozen=True)
class TeamPossessions:
    team: str
    points: int
    fga: int
    fta: int
    oreb: int
    tov: int

    @property
    def possessions(self) -> float:
        return self.fga - self.oreb + self.tov + FREE_THROW_POSSESSION_WEIGHT * self.fta


@dataclass(frozen=True)
class GamePossessions:
    event_id: str
    home: TeamPossessions
    away: TeamPossessions
    observed_at_utc: str

    @property
    def game_possessions(self) -> float:
        """Average of the two sides' estimates: possessions per team in this game."""
        return (self.home.possessions + self.away.possessions) / 2.0

    def efficiency(self, side: str) -> float:
        """Points per 100 possessions for ``side`` ('home' or 'away')."""
        team = self.home if side == "home" else self.away
        poss = self.game_possessions
        return 100.0 * team.points / poss if poss > 0 else 0.0


def _team(payload: dict) -> TeamPossessions | None:
    try:
        return TeamPossessions(
            team=str(payload["team_name"]),
            points=int(payload["points"]),
            fga=int(payload["field_goal_attempts"]),
            fta=int(payload["free_throw_attempts"]),
            oreb=int(payload["offensive_rebounds"]),
            tov=int(payload["turnovers"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


class NCAABPossessionStore:
    """Loads every captured box-score file once and serves possessions by ESPN event id."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = (
            Path(root)
            if root
            else Path(__file__).resolve().parents[3] / "data" / "features" / "ncaab" / "boxscores"
        )
        self._by_event: dict[str, GamePossessions] | None = None

    def _load(self) -> dict[str, GamePossessions]:
        if self._by_event is not None:
            return self._by_event
        out: dict[str, GamePossessions] = {}
        if self.root.exists():
            for path in sorted(self.root.glob("*/*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if (
                    payload.get("schema") != "ncaab-boxscore-components-v1"
                    or payload.get("status") != "completed"
                ):
                    continue
                home = _team(payload.get("home") or {})
                away = _team(payload.get("away") or {})
                if home is None or away is None:
                    continue
                if min(home.possessions, away.possessions) <= 0:
                    continue
                event_id = str(payload.get("event_id") or path.stem)
                out.setdefault(
                    event_id,
                    GamePossessions(
                        event_id=event_id,
                        home=home,
                        away=away,
                        observed_at_utc=str(payload.get("observed_at_utc", "")),
                    ),
                )
        self._by_event = out
        return out

    def get(self, event_id: str) -> GamePossessions | None:
        return self._load().get(str(event_id))

    def for_history(self, history: Iterable[GameRecord]) -> list[tuple[GameRecord, GamePossessions]]:
        """Possessions for exactly the games the caller may see, in the caller's order."""
        table = self._load()
        out: list[tuple[GameRecord, GamePossessions]] = []
        for game in history:
            record = table.get(str(game.event_id))
            if record is not None:
                out.append((game, record))
        return out

    def coverage(self, history: Iterable[GameRecord]) -> float:
        games = list(history)
        return len(self.for_history(games)) / len(games) if games else 0.0
