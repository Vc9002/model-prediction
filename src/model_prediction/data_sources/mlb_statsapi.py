from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
import time
import urllib.parse
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

STATS_API_BASE = "https://statsapi.mlb.com/api"
SNAPSHOT_SCHEMA_VERSION = "mlb-statsapi-game-v1"
_BATTING_FIELDS = (
    "plateAppearances",
    "atBats",
    "hits",
    "doubles",
    "triples",
    "homeRuns",
    "baseOnBalls",
    "intentionalWalks",
    "hitByPitch",
    "strikeOuts",
    "sacFlies",
    "totalBases",
    "runs",
    "rbi",
)
_PITCHING_FIELDS = (
    "inningsPitched",
    "earnedRuns",
    "runs",
    "hits",
    "homeRuns",
    "baseOnBalls",
    "hitByPitch",
    "strikeOuts",
    "battersFaced",
    "numberOfPitches",
    "strikes",
)


@dataclass(frozen=True)
class SnapshotCollectionResult:
    scheduled: int
    written: int
    skipped: tuple[dict[str, str], ...]
    output_path: str


class MLBStatsAPIClient:
    """Read-only official MLB schedule/game-feed adapter."""

    def __init__(self, base_url: str = STATS_API_BASE, timeout: float = 12) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def schedule(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        games: dict[int, dict[str, Any]] = {}
        chunks: list[tuple[date, date]] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + timedelta(days=6))
            chunks.append((cursor, chunk_end))
            cursor = chunk_end + timedelta(days=1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            payloads = pool.map(
                lambda chunk: self._get_json(
                    f"{self.base_url}/v1/schedule",
                    {
                        "sportId": 1,
                        "startDate": chunk[0].isoformat(),
                        "endDate": chunk[1].isoformat(),
                    },
                ),
                chunks,
            )
            for payload in payloads:
                for day in payload.get("dates", []):
                    for game in day.get("games", []):
                        games[int(game["gamePk"])] = game
        return sorted(games.values(), key=lambda game: (game["gameDate"], game["gamePk"]))

    def live_feed(self, game_pk: int | str) -> dict[str, Any]:
        return self._get_json(f"{self.base_url}/v1.1/game/{game_pk}/feed/live")

    def snapshot(self, game: dict[str, Any], *, snapshot_type: str) -> dict[str, Any]:
        game_pk = int(game["gamePk"])
        payload = self.live_feed(game_pk)
        return compact_game_snapshot(payload, snapshot_type=snapshot_type)

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
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


def collect_game_snapshots(
    client: MLBStatsAPIClient,
    start_date: str,
    end_date: str,
    output_path: str | Path,
    *,
    snapshot_type: str = "historical_reconstruction",
    workers: int = 12,
    progress_every: int = 50,
    checkpoint_every: int = 100,
) -> SnapshotCollectionResult:
    if snapshot_type not in {"historical_reconstruction", "forward_pregame"}:
        raise ValueError("snapshot_type must be historical_reconstruction or forward_pregame")
    games = client.schedule(start_date, end_date)
    games = [game for game in games if game.get("gameType") == "R"]
    if snapshot_type == "historical_reconstruction":
        games = [game for game in games if game.get("status", {}).get("abstractGameState") == "Final"]
    else:
        now = datetime.now(UTC)
        games = [game for game in games if _parse_utc(game["gameDate"]) > now]

    store = MLBGameSnapshotStore(output_path)
    snapshots: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    written = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(client.snapshot, game, snapshot_type=snapshot_type): game for game in games
        }
        for index, future in enumerate(as_completed(futures), start=1):
            game = futures[future]
            try:
                snapshots.append(future.result())
            except (KeyError, RuntimeError, TypeError, ValueError) as error:
                skipped.append({"game_pk": str(game.get("gamePk", "unknown")), "reason": str(error)})
            if checkpoint_every and len(snapshots) >= checkpoint_every:
                store.merge(snapshots)
                written += len(snapshots)
                snapshots.clear()
            if progress_every and (index % progress_every == 0 or index == len(futures)):
                print(
                    f"MLB snapshot progress {index}/{len(futures)} "
                    f"ok={written + len(snapshots)} skipped={len(skipped)}",
                    flush=True,
                )

    if snapshots:
        store.merge(snapshots)
        written += len(snapshots)
    return SnapshotCollectionResult(
        scheduled=len(games),
        written=written,
        skipped=tuple(skipped),
        output_path=str(Path(output_path)),
    )


class MLBGameSnapshotStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        return rows

    def merge(self, snapshots: Iterable[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                existing = {int(row["game_pk"]): row for row in self.rows()}
                for snapshot in snapshots:
                    existing[int(snapshot["game_pk"])] = snapshot
                ordered = sorted(existing.values(), key=lambda row: (row["game_start_utc"], row["game_pk"]))
                temporary = self.path.with_suffix(self.path.suffix + ".tmp")
                with temporary.open("w", encoding="utf-8") as handle:
                    for row in ordered:
                        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                temporary.replace(self.path)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def compact_game_snapshot(payload: dict[str, Any], *, snapshot_type: str) -> dict[str, Any]:
    game_data = payload["gameData"]
    live_data = payload["liveData"]
    boxscore = live_data.get("boxscore", {})
    linescore = live_data.get("linescore", {})
    players = game_data.get("players", {})
    teams = game_data["teams"]
    probable = game_data.get("probablePitchers", {})
    raw_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    sides: dict[str, Any] = {}
    for side in ("away", "home"):
        team_box = boxscore.get("teams", {}).get(side, {})
        batting_order = [int(player_id) for player_id in team_box.get("battingOrder", [])]
        pitcher_order = [int(player_id) for player_id in team_box.get("pitchers", [])]
        compact_players = []
        for key, player_box in team_box.get("players", {}).items():
            player_id = int(str(key).removeprefix("ID"))
            identity = players.get(f"ID{player_id}", player_box)
            person = identity.get("person", player_box.get("person", {}))
            stats = player_box.get("stats", {})
            batting = _select_stats(stats.get("batting", {}), _BATTING_FIELDS)
            pitching = _select_stats(stats.get("pitching", {}), _PITCHING_FIELDS)
            if not batting and not pitching and player_id not in batting_order and player_id not in pitcher_order:
                continue
            compact_players.append(
                {
                    "player_id": player_id,
                    "name": person.get("fullName") or person.get("displayName") or "Unknown",
                    "bat_side": _code(identity.get("batSide")),
                    "pitch_hand": _code(identity.get("pitchHand")),
                    "batting_order": batting_order.index(player_id) + 1 if player_id in batting_order else None,
                    "pitching_order": pitcher_order.index(player_id) + 1 if player_id in pitcher_order else None,
                    "batting": batting,
                    "pitching": pitching,
                }
            )
        sides[side] = {
            "team_id": int(teams[side]["id"]),
            "team_name": teams[side]["name"],
            "probable_pitcher_id": _integer(probable.get(side, {}).get("id")),
            "probable_pitcher_name": probable.get(side, {}).get("fullName"),
            "batting_order": batting_order,
            "pitcher_order": pitcher_order,
            "players": sorted(compact_players, key=lambda row: row["player_id"]),
            "runs": _integer(linescore.get("teams", {}).get(side, {}).get("runs")),
        }

    # First-inning YRFI from linescore innings
    innings = linescore.get("innings", [])
    first_inning = innings[0] if innings else {}
    first_inning_runs_away = _integer(first_inning.get("away", {}).get("runs", 0))
    first_inning_runs_home = _integer(first_inning.get("home", {}).get("runs", 0))
    yrfi = 1 if (first_inning_runs_away or 0) + (first_inning_runs_home or 0) > 0 else 0

    weather = game_data.get("weather", {})
    officials = [
        {
            "name": item.get("official", {}).get("fullName"),
            "type": item.get("officialType"),
        }
        for item in boxscore.get("officials", [])
    ]
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_type": snapshot_type,
        "observed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": "MLB Stats API",
        "source_url": f"{STATS_API_BASE}/v1.1/game/{payload['gamePk']}/feed/live",
        "source_payload_hash": raw_hash,
        "game_pk": int(payload["gamePk"]),
        "game_start_utc": game_data["datetime"]["dateTime"],
        "status": game_data.get("status", {}).get("detailedState"),
        "venue_id": _integer(game_data.get("venue", {}).get("id")),
        "venue_name": game_data.get("venue", {}).get("name"),
        "weather": {
            "condition": weather.get("condition"),
            "temperature_f": _number(weather.get("temp")),
            "wind": weather.get("wind"),
        },
        "officials": officials,
        "away": sides["away"],
        "home": sides["home"],
        "first_inning_runs_away": first_inning_runs_away or 0,
        "first_inning_runs_home": first_inning_runs_home or 0,
        "yrfi": yrfi,
    }


def _select_stats(raw: dict[str, Any], fields: tuple[str, ...]) -> dict[str, float | str]:
    output: dict[str, float | str] = {}
    for field in fields:
        value = raw.get(field)
        if value in (None, "", "-.--", ".---"):
            continue
        if field == "inningsPitched":
            output[field] = str(value)
        else:
            parsed = _number(value)
            if parsed is not None:
                output[field] = parsed
    return output


def _code(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("code") or value.get("description")
    return str(value) if value else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)
