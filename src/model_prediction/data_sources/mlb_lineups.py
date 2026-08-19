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

  * Observation timestamps are stamped AFTER the HTTP response returns,
    never before. Stamping before a slow call claims we knew the lineup
    earlier than we did, which is the leaking direction (see CLAUDE.md's
    KBO/NPB timestamp-ordering incident, where exactly this ordering
    silently zeroed every real pick).
  * `lineup_state` marks whether the capture is decision-grade. Only
    `pregame` rows may inform a pregame decision; `in_game` and `final`
    rows are recorded for completeness and audit, and consumers must
    filter them out rather than trusting the timestamp alone. A game that
    has started has a *confirmed* order, which is precisely the
    information a pregame decision is not allowed to have.
  * One row per distinct lineup, keyed by `content_hash`. A late scratch
    changes the hash and becomes its own row; nothing is overwritten. A
    re-observation of an unchanged lineup advances
    `last_observed_at_utc` and increments `capture_count` on the row it
    confirms.

A consumer selects the row with the greatest `first_observed_at_utc <=
decision_time` for that game where `lineup_state == "pregame"`, and reads
staleness from `last_observed_at_utc` (also `<= decision_time`) rather
than from the first sighting -- a lineup seen at 18:10 and reconfirmed at
20:10 is 20 minutes old at 20:30, not 2h20m.
"""

from __future__ import annotations

import fcntl
import hashlib
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


def lineup_content_hash(game_pk: int, lineup_state: str, away: dict[str, Any], home: dict[str, Any]) -> str:
    """Stable identity of WHAT was announced, independent of when we saw it.

    A late scratch changes this hash and therefore earns its own snapshot;
    a re-observation of the same nine does not.
    """
    payload = {
        "game_pk": game_pk,
        "lineup_state": lineup_state,
        "away": [e["player_id"] for e in away.get("batting_order", [])],
        "home": [e["player_id"] for e in home.get("batting_order", [])],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_lineup_snapshot(
    game: dict[str, Any], boxscore: dict[str, Any], *, observed_at_utc: str
) -> dict[str, Any]:
    """One capture of one game's batting orders.

    `observed_at_utc` is supplied by the caller rather than read here, so
    the caller can stamp it after the network round-trip completes.

    Both `first_observed_at_utc` and `last_observed_at_utc` start equal;
    `LineupStore.merge` advances the latter when this same content is seen
    again. Keeping both matters for staleness: a lineup first seen at 18:10
    and reconfirmed at 20:10 is 20 minutes old to a decision made at 20:30,
    not 2h20m -- collapsing them would make every consumer systematically
    overstate how stale its own input is.
    """
    detailed_state = ((game.get("status") or {}).get("detailedState")) or ""
    teams = boxscore.get("teams") or {}
    away = _side_lineup(teams.get("away") or {})
    home = _side_lineup(teams.get("home") or {})
    complete = away["size"] == EXPECTED_LINEUP_SIZE and home["size"] == EXPECTED_LINEUP_SIZE
    game_pk = int(game["gamePk"])
    lineup_state = classify_lineup_state(detailed_state)
    return {
        "schema_version": LINEUP_SCHEMA_VERSION,
        "game_pk": game_pk,
        "game_start_utc": game.get("gameDate"),
        "content_hash": lineup_content_hash(game_pk, lineup_state, away, home),
        "first_observed_at_utc": observed_at_utc,
        "last_observed_at_utc": observed_at_utc,
        "capture_count": 1,
        "detailed_state": detailed_state,
        "lineup_state": lineup_state,
        "lineup_complete": complete,
        "away_team": ((game.get("teams") or {}).get("away") or {}).get("team", {}).get("name"),
        "home_team": ((game.get("teams") or {}).get("home") or {}).get("team", {}).get("name"),
        "away": away,
        "home": home,
    }


def _worth_observing(snapshot: dict[str, Any]) -> bool:
    """A lineup with nothing in it is not an observation.

    Before MLB posts an order, the boxscore returns empty batting orders.
    Recording those would add a row per game per hour that says only "not
    announced yet", which is not information the archive needs to carry.
    """
    return snapshot["away"]["size"] > 0 or snapshot["home"]["size"] > 0


def capture_date(
    game_date: str,
    *,
    client: MLBLineupClient | None = None,
    stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Capture the batting orders of games that have not started yet.

    Schedule-aware by design, so an hourly run costs one request when
    there is nothing to do. Games are filtered by status BEFORE any
    boxscore is fetched: a started game's order is already recoverable
    from its final boxscore forever, so re-recording it buys nothing and
    would add a row per game per hour. Only the pregame window is
    irreplaceable, and only it is collected.

    A per-game failure degrades to a skip rather than losing the whole
    date -- a single unavailable boxscore must not cost us the other
    fourteen games, since none of them can be re-captured later.
    """
    api = client or MLBLineupClient()
    scheduled = api.schedule(game_date)
    unstarted = [
        game
        for game in scheduled
        if classify_lineup_state(((game.get("status") or {}).get("detailedState")) or "") == "pregame"
    ]
    snapshots: list[dict[str, Any]] = []
    unavailable = 0
    for game in unstarted:
        try:
            boxscore = api.boxscore(int(game["gamePk"]))
        except (RuntimeError, KeyError, ValueError):
            unavailable += 1
            continue
        # Stamped AFTER the response: see the module docstring's PIT note.
        observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        snapshot = build_lineup_snapshot(game, boxscore, observed_at_utc=observed_at)
        if _worth_observing(snapshot):
            snapshots.append(snapshot)
    if stats is not None:
        # The denominator for capture rate has to be recorded at capture
        # time: the schedule is mutable, and "how many games were still
        # capturable when we looked" cannot be reconstructed afterwards.
        stats.update(
            {
                "scheduled_games": len(scheduled),
                "eligible_pregame_games": len(unstarted),
                "games_with_lineup_posted": len(snapshots),
                "boxscore_unavailable": unavailable,
            }
        )
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
    stats: dict[str, Any] = {}
    snapshots = capture_date(game_date, client=client, stats=stats)
    store = LineupStore(archive)
    result = store.merge(snapshots)
    decision_grade = [s for s in snapshots if s["lineup_state"] == "pregame" and s["lineup_complete"]]
    eligible = int(stats.get("eligible_pregame_games") or 0)
    return {
        "date": game_date,
        "games_seen": len(snapshots),
        "rows_written": result["written"],
        "rows_confirmed": result["confirmed"],
        "decision_grade": len(decision_grade),
        # Capture rate against the games that were still capturable when we
        # looked -- the number that reveals systematic missingness by start
        # time, which is invisible in a raw row count.
        "decision_grade_capture_rate": (round(len(decision_grade) / eligible, 4) if eligible else None),
        **stats,
        "archive": str(archive),
    }


class LineupStore:
    """JSONL archive of lineup captures, one row per distinct lineup.

    One row per distinct CONTENT, not per capture. Including the
    observation timestamp in the identity made every row unique by
    construction, so nothing ever deduped -- found immediately on live data
    (three runs of an unchanged slate wrote 45 rows instead of 15; ~360
    duplicate rows a day at hourly cadence).

    But a re-observation is not worthless, so it is folded into the
    existing row rather than dropped: `last_observed_at_utc` advances and
    `capture_count` increments. A lineup first seen at 18:10 and
    reconfirmed at 20:10 is 20 minutes old to a decision made at 20:30 --
    keeping only the first sighting would make every consumer overstate
    its own staleness by two hours, and would lose the evidence that the
    lineup was still standing later.

    A changed lineup (late scratch) has a different `content_hash` and so
    becomes its own new row; nothing is ever overwritten.
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
    def _identity(row: dict[str, Any]) -> str:
        cached = row.get("content_hash")
        if cached:
            return str(cached)
        # Pre-content_hash rows (schema v1 archives) hash on demand so a
        # migration is never required just to read an old file.
        return lineup_content_hash(
            int(row.get("game_pk") or 0),
            str(row.get("lineup_state") or ""),
            row.get("away") or {},
            row.get("home") or {},
        )

    def merge(self, snapshots: Iterable[dict[str, Any]]) -> dict[str, int]:
        """Add new lineups; fold re-observations into the row they confirm.

        Returns {"written": n, "confirmed": n}. The whole file is rewritten
        under an exclusive lock because a re-observation mutates an
        existing row -- the daily pipeline and the hourly collector can
        both call this, and a torn read-modify-write would lose lineups
        that cannot be re-captured.
        """
        snapshots = list(snapshots)
        if not snapshots:
            return {"written": 0, "confirmed": 0}

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("w") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                rows = self.rows()
                by_hash = {self._identity(row): row for row in rows}
                written = confirmed = 0
                for snapshot in snapshots:
                    key = self._identity(snapshot)
                    current = by_hash.get(key)
                    if current is None:
                        rows.append(snapshot)
                        by_hash[key] = snapshot
                        written += 1
                        continue
                    seen_at = snapshot.get("last_observed_at_utc") or snapshot.get(
                        "first_observed_at_utc", ""
                    )
                    # max(): captures can land out of order (a retry, or the
                    # daily job racing the hourly one), and last_observed
                    # must never move backwards.
                    if seen_at > str(current.get("last_observed_at_utc") or ""):
                        current["last_observed_at_utc"] = seen_at
                    current["capture_count"] = int(current.get("capture_count") or 1) + 1
                    confirmed += 1

                temporary = self.path.with_suffix(self.path.suffix + ".tmp")
                with temporary.open("w") as handle:
                    for row in rows:
                        handle.write(json.dumps(row, sort_keys=True) + "\n")
                temporary.replace(self.path)
                return {"written": written, "confirmed": confirmed}
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
