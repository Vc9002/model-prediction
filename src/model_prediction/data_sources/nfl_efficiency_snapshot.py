"""Capture nflverse weekly statistics with an immutable observation timestamp.

These captures are prospective source vintages. A later download must never be
used to reconstruct what a past forecast knew.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime
from pathlib import Path

import httpx
import polars as pl

from ..domain import parse_utc, utc_now

RELEASE_ROOT = "https://github.com/nflverse/nflverse-data/releases/download"
PLAYER_URL = f"{RELEASE_ROOT}/stats_player/stats_player_week_{{season}}.parquet"
SCHEDULE_URL = f"{RELEASE_ROOT}/schedules/games.parquet"
DEPTH_URL = f"{RELEASE_ROOT}/depth_charts/depth_charts_{{season}}.parquet"
TEAM_COLUMNS = ("attempts", "carries", "passing_epa", "rushing_epa")
QB_COLUMNS = ("attempts", "sacks_suffered", "passing_epa", "passing_cpoe")


def _complete_games(schedule: pl.DataFrame, observed: datetime) -> pl.DataFrame:
    """Use published final scores and a prior local game date as a safety bound."""
    required = {"game_id", "gameday", "home_score", "away_score", "season"}
    if not required <= set(schedule.columns):
        raise ValueError(f"NFL schedule missing {sorted(required - set(schedule.columns))}")
    return schedule.filter(
        pl.col("home_score").is_not_null()
        & pl.col("away_score").is_not_null()
        & (pl.col("gameday").cast(pl.Utf8) < observed.date().isoformat())
    ).select("game_id", "gameday", "season")


def build_efficiency_frames(
    players: pl.DataFrame, schedule: pl.DataFrame, observed: datetime
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build the exact minimal schema consumed by the NFL structural engine."""
    needed = {
        "game_id",
        "team",
        "opponent_team",
        "player_display_name",
        "position",
        *TEAM_COLUMNS,
        *QB_COLUMNS,
    }
    if not needed <= set(players.columns):
        raise ValueError(f"NFL player statistics missing {sorted(needed - set(players.columns))}")
    games = _complete_games(schedule, observed)
    joined = players.join(games, on="game_id", how="inner", suffix="_schedule")
    if joined.is_empty():
        raise ValueError("NFL statistics have no completed games before capture")
    team = (
        joined.group_by("game_id", "gameday", "team", "opponent_team")
        .agg([pl.col(col).fill_null(0).sum().alias(col) for col in TEAM_COLUMNS])
        .sort("gameday", "game_id", "team")
    )
    qb = (
        joined.filter(pl.col("position") == "QB")
        .select("game_id", "gameday", "team", "player_display_name", *QB_COLUMNS)
        .sort("gameday", "game_id", "player_display_name")
    )
    if qb.is_empty() or team.is_empty():
        raise ValueError("NFL capture lacks team or quarterback rows")
    return team, qb


def _qb1_rows(depth: pl.DataFrame, observed: datetime) -> pl.DataFrame:
    """Latest QB1 depth-chart entry per team known at capture time."""
    required = {"dt", "team", "player_name", "pos_abb", "pos_rank"}
    if not required <= set(depth.columns):
        raise ValueError(f"NFL depth chart missing {sorted(required - set(depth.columns))}")
    cutoff = observed.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        depth.filter((pl.col("pos_abb") == "QB") & (pl.col("pos_rank") == 1) & (pl.col("dt") <= cutoff))
        .sort("dt", descending=True)
        .unique(subset="team", keep="first")
    )


def projected_qbs(depth: pl.DataFrame, observed: datetime) -> dict[str, str]:
    """Latest published QB1 per team, labelled projected rather than confirmed."""
    rows = _qb1_rows(depth, observed)
    return dict(zip(rows["team"].to_list(), rows["player_name"].to_list()))


def capture_nfl_efficiency(
    data_root: str | Path,
    *,
    observed_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> dict[str, object]:
    """Atomically archive raw releases, derived inputs, and their hashes."""
    observed = parse_utc((observed_at or utc_now()).isoformat())
    owns_client = client is None
    http_client = client or httpx.Client(follow_redirects=True, timeout=45)
    try:
        season = observed.year
        raw: dict[str, bytes] = {}
        for label, url in (
            ("players", PLAYER_URL.format(season=season)),
            ("schedule", SCHEDULE_URL),
            ("depth", DEPTH_URL.format(season=season)),
        ):
            response = http_client.get(url)
            response.raise_for_status()
            raw[label] = response.content
        players = pl.read_parquet(io.BytesIO(raw["players"]))
        schedule = pl.read_parquet(io.BytesIO(raw["schedule"]))
        depth = pl.read_parquet(io.BytesIO(raw["depth"]))
        team, qb = build_efficiency_frames(players, schedule, observed)
        starters = projected_qbs(depth, observed)
        qb1_rows = _qb1_rows(depth, observed)
        starter_updates = dict(zip(qb1_rows["team"].to_list(), qb1_rows["dt"].to_list()))
        root = Path(data_root) / "processed" / "nfl" / "efficiency_snapshots"
        stamp = observed.strftime("%Y%m%dT%H%M%S%fZ")
        final = root / stamp
        tmp = root / f".{stamp}.partial"
        if final.exists() or tmp.exists():
            raise FileExistsError(final)
        tmp.mkdir(parents=True)
        try:
            for label, payload in raw.items():
                (tmp / f"{label}.parquet").write_bytes(payload)
            team.write_parquet(tmp / "team.parquet")
            qb.write_parquet(tmp / "qb.parquet")
            files = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp.glob("*.parquet")
            }
            manifest = {
                "schema_version": "1",
                "observed_at_utc": observed.isoformat(),
                "source_urls": {
                    "players": PLAYER_URL.format(season=season),
                    "schedule": SCHEDULE_URL,
                    "depth": DEPTH_URL.format(season=season),
                },
                "sha256": files,
                "team_rows": team.height,
                "qb_rows": qb.height,
                "latest_gameday": team.select(pl.col("gameday").max()).item(),
                "projected_qbs": starters,
                "projected_qb_updated_utc": starter_updates,
            }
            manifest["manifest_sha256"] = hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            (tmp / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(tmp, final)
        except Exception:
            for path in tmp.glob("*"):
                path.unlink()
            tmp.rmdir()
            raise
        return {"status": "ok", "snapshot_path": str(final), **manifest}
    finally:
        if owns_client:
            http_client.close()


def load_efficiency_snapshot(
    data_root: str | Path, observed_at: datetime
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, object]] | None:
    """Choose only an observed, hash-valid capture; never use a future release."""
    root = Path(data_root) / "processed" / "nfl" / "efficiency_snapshots"
    if not root.exists():
        return None
    candidates = []
    for path in root.glob("*/manifest.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            expected = manifest.pop("manifest_sha256", None)
            if (
                not expected
                or hashlib.sha256(
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                != expected
            ):
                continue
            manifest["manifest_sha256"] = expected
            obs = parse_utc(str(manifest["observed_at_utc"]))
            if obs <= observed_at:
                candidates.append((obs, path, manifest))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for _, path, manifest in sorted(candidates, reverse=True):
        folder = path.parent
        hashes = manifest.get("sha256")
        if not isinstance(hashes, dict):
            continue
        try:
            if any(
                hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest
                for name, digest in hashes.items()
            ):
                continue
            team = pl.read_parquet(folder / "team.parquet")
            qb = pl.read_parquet(folder / "qb.parquet")
            if team.is_empty() or qb.is_empty():
                continue
            return team, qb, {**manifest, "snapshot_path": str(folder)}
        except (OSError, pl.exceptions.PolarsError):
            continue
    return None
