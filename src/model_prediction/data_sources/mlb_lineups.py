"""Prospective MLB lineup capture (batting order, pregame).

Why this module exists: `features/lineup_strength.py` has always declared
`lineup_status: "unavailable_from_source"` because nothing captured who is
actually playing tonight. Completed-game boxscores carry the batting order
retroactively, but a decision made before first pitch cannot use them.

Lineups are the one MLB input that CANNOT be backfilled. Historical
boxscores tell you who ended up playing; they can never tell you what was
*announced* at 4pm for a 7pm game, nor when it was announced. So the
training set for any lineup-aware model starts accumulating the day this
capture first runs, and every day it does not run is permanently lost.
That is the entire argument for landing this before the features that
consume it.

Point-in-time contract:

  * `observed_at_utc` is stamped AFTER the HTTP response returns, never
    before. Stamping before a slow call claims we knew the lineup earlier
    than we did, which is the leaking direction (see CLAUDE.md's KBO/NPB
    timestamp-ordering incident, where exactly this ordering silently
    zeroed every real pick).
  * `lineup_state` marks whether the capture is decision-grade. Only
    `pregame` rows may inform a pregame decision; `in_game` and `final`
    rows are recorded for completeness and audit, and consumers must
    filter them out rather than trusting the timestamp alone. A game that
    has started has a *confirmed* order, which is precisely the
    information a pregame decision is not allowed to have.
  * Lineups change (late scratches). Every capture is appended as its own
    row; nothing is overwritten. A consumer picks the latest row with
    `observed_at_utc <= decision_time` and `lineup_state == "pregame"`.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATS_API_BASE = "https://statsapi.mlb.com/api"
LINEUP_SCHEMA_VERSION = "mlb-lineup-v1"

# Statuses in which MLB has posted a lineup but the game has not started.
# `Scheduled` is included because the order is sometimes posted hours out;
# a Scheduled game with a full nine is a real, usable projected lineup.
PREGAME_STATUSES = frozenset({"Scheduled", "Pre-Game", "Warmup", "Delayed Start", "Delayed"})
IN_GAME_STATUSES = frozenset({"In Progress", "Manager Challenge", "Delayed: Rain", "Suspended"})
FINAL_STATUSES = frozenset({"Final", "Game Over", "Completed Early"})

EXPECTED_LINEUP_SIZE = 9


class MLBLineupClient:
    """Read-only batting-order adapter over the official Stats API."""

    def __init__(self, base_url: str = STATS_API_BASE, timeout: float = 12) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def schedule(self, game_date: str) -> list[dict[str, Any]]:
        payload = self._get_json(f"{self.base_url}/v1/schedule", {"sportId": 1, "date": game_date})
        return [game for day in payload.get("dates", []) for game in day.get("games", [])]

    def boxscore(self, game_pk: int | str) -> dict[str, Any]:
        return self._get_json(f"{self.base_url}/v1/game/{game_pk}/boxscore")

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # Same curl-based transport as mlb_statsapi.py -- keeping one shape
        # for both means one place to reason about retries and timeouts.
        last_error: Exception | None = None
        query = urllib.parse.urlencode(params or {})
        request_url = f"{url}?{query}" if query else url
        for attempt in range(4):
            try:
                completed = subprocess.run(
                    [
                        "curl",
                        "-fsSL",
                        "--compressed",
                        "--max-time",
                        str(int(self.timeout)),
                        "--connect-timeout",
                        "5",
                        "--retry",
                        "1",
                        request_url,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return json.loads(completed.stdout)
            except (subprocess.CalledProcessError, ValueError) as error:
                last_error = error
                if attempt < 3:
                    time.sleep(0.4 * (2**attempt))
        raise RuntimeError(f"MLB Stats API request failed: {url}: {last_error}")


def classify_lineup_state(detailed_state: str) -> str:
    if detailed_state in PREGAME_STATUSES:
        return "pregame"
    if detailed_state in FINAL_STATUSES:
        return "final"
    if detailed_state in IN_GAME_STATUSES:
        return "in_game"
    # An unrecognized status must never be optimistically treated as
    # pregame -- that is the direction that would leak a confirmed lineup
    # into a pregame decision.
    return "unknown"


def _side_lineup(team_block: dict[str, Any]) -> dict[str, Any]:
    order = [int(pid) for pid in team_block.get("battingOrder", [])]
    players = team_block.get("players", {})
    entries = []
    for slot, player_id in enumerate(order, start=1):
        person = players.get(f"ID{player_id}", {})
        entries.append(
            {
                "slot": slot,
                "player_id": player_id,
                "player_name": (person.get("person") or {}).get("fullName"),
                "position": (person.get("position") or {}).get("abbreviation"),
            }
        )
    return {"size": len(entries), "batting_order": entries}


def build_lineup_snapshot(
    game: dict[str, Any], boxscore: dict[str, Any], *, observed_at_utc: str
) -> dict[str, Any]:
    """One capture of one game's batting orders.

    `observed_at_utc` is supplied by the caller rather than read here, so
    the caller can stamp it after the network round-trip completes.
    """
    detailed_state = ((game.get("status") or {}).get("detailedState")) or ""
    teams = boxscore.get("teams") or {}
    away = _side_lineup(teams.get("away") or {})
    home = _side_lineup(teams.get("home") or {})
    complete = away["size"] == EXPECTED_LINEUP_SIZE and home["size"] == EXPECTED_LINEUP_SIZE
    return {
        "schema_version": LINEUP_SCHEMA_VERSION,
        "game_pk": int(game["gamePk"]),
        "game_start_utc": game.get("gameDate"),
        "observed_at_utc": observed_at_utc,
        "detailed_state": detailed_state,
        "lineup_state": classify_lineup_state(detailed_state),
        "lineup_complete": complete,
        "away_team": ((game.get("teams") or {}).get("away") or {}).get("team", {}).get("name"),
        "home_team": ((game.get("teams") or {}).get("home") or {}).get("team", {}).get("name"),
        "away": away,
        "home": home,
    }


def capture_date(game_date: str, *, client: MLBLineupClient | None = None) -> list[dict[str, Any]]:
    """Capture every game's batting order for one date.

    Games are captured regardless of state so the archive records what was
    knowable when; `lineup_state` is what decides usability, not omission.
    A per-game failure degrades to a skip rather than losing the whole
    date -- a single unavailable boxscore must not cost us the other
    fourteen games, since none of them can be re-captured later.
    """
    api = client or MLBLineupClient()
    snapshots: list[dict[str, Any]] = []
    for game in api.schedule(game_date):
        try:
            boxscore = api.boxscore(int(game["gamePk"]))
        except (RuntimeError, KeyError, ValueError):
            continue
        # Stamped AFTER the response: see the module docstring's PIT note.
        observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        snapshots.append(build_lineup_snapshot(game, boxscore, observed_at_utc=observed_at))
    return snapshots


DEFAULT_LINEUP_ARCHIVE = "data/point_in_time/mlb_lineups.jsonl"


def capture_and_store(
    game_date: str,
    *,
    archive: str | Path = DEFAULT_LINEUP_ARCHIVE,
    client: MLBLineupClient | None = None,
) -> dict[str, Any]:
    """Capture one date and append it, returning a summary for the caller.

    Capture RATE is the whole game here. A once-daily run catches only the
    games still pregame at that moment -- the first live run, at 23:40Z,
    found 10 of 15 games already started and salvaged only 5 decision-grade
    lineups. Running this hourly through the afternoon is what turns it
    into full slate coverage, and there is no way to make up the
    difference afterwards.
    """
    snapshots = capture_date(game_date, client=client)
    store = LineupStore(archive)
    written = store.merge(snapshots)
    decision_grade = [s for s in snapshots if s["lineup_state"] == "pregame" and s["lineup_complete"]]
    return {
        "date": game_date,
        "games_seen": len(snapshots),
        "rows_written": written,
        "decision_grade": len(decision_grade),
        "archive": str(archive),
    }


class LineupStore:
    """Append-only JSONL archive of lineup captures.

    Append-only on purpose: a lineup that changes between captures is real
    signal (a late scratch), not a correction to overwrite.

    Dedupe is on CONTENT and deliberately EXCLUDES `observed_at_utc`. That
    field is different on every capture by construction, so including it
    made the identity unique every time and the store never deduped at all
    -- found immediately on live data (three runs of an unchanged slate
    wrote 45 rows instead of 15; at the intended hourly cadence that is
    ~360 duplicate rows per day). A row therefore records the FIRST time a
    given lineup was seen, and a re-capture of an unchanged lineup is a
    no-op.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        with self.path.open() as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    @staticmethod
    def _identity(row: dict[str, Any]) -> tuple:
        def order(side: str) -> tuple:
            return tuple(e["player_id"] for e in row.get(side, {}).get("batting_order", []))

        return (
            row.get("game_pk"),
            row.get("lineup_state"),
            order("away"),
            order("home"),
        )

    def merge(self, snapshots: Iterable[dict[str, Any]]) -> int:
        existing = {self._identity(row) for row in self.rows()}
        fresh = [s for s in snapshots if self._identity(s) not in existing]
        if not fresh:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            for row in fresh:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        return len(fresh)
