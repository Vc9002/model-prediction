"""Raw-first MLB v3 backfill orchestration; no models and no v2 evidence."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from model_prediction.rebuild.providers.base import ProviderStatus
from model_prediction.rebuild.providers.mlb_stats import MLBStatsProvider
from model_prediction.rebuild.providers.statcast import MAX_STATCAST_DAYS, StatcastProvider

from .audit import audit_mlb_v3
from .normalize import normalize_game_feed, normalize_schedule, normalize_statcast, normalize_transactions
from .store import MLBV3NormalizedStore


class MLBV3Foundation:
    def __init__(
        self,
        normalized_root: str | Path,
        *,
        mlb_stats: MLBStatsProvider | None = None,
        statcast: StatcastProvider | None = None,
    ) -> None:
        self.store = MLBV3NormalizedStore(normalized_root)
        self.mlb_stats = mlb_stats
        self.statcast = statcast

    def backfill_mlb_stats(
        self,
        start: date,
        end: date,
        *,
        tables: Iterable[str] = ("schedule",),
        force: bool = False,
    ) -> dict[str, Any]:
        if self.mlb_stats is None:
            raise RuntimeError("MLB Stats provider is not configured")
        requested = tuple(tables)
        unsupported = sorted(set(requested) - {"schedule", "game_feed", "transactions"})
        if unsupported:
            raise ValueError(f"unsupported MLB Stats tables: {unsupported}")
        report: dict[str, Any] = {
            "sport": "mlb",
            "version": "v3",
            "provider": "mlb_stats",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "row_counts": {},
            "errors": {},
        }
        schedule_result = self.mlb_stats.schedule(start, end, force=force)
        games = pl.DataFrame()
        if schedule_result.status is ProviderStatus.AVAILABLE and schedule_result.metadata is not None:
            if schedule_result.frame is not None and not schedule_result.frame.is_empty():
                games = normalize_schedule(schedule_result.frame, schedule_result.metadata)
                for season_data in games.partition_by("season", as_dict=False):
                    season = int(season_data["season"].item(0))
                    self.store.write("games", season, season_data)
                report["row_counts"]["games"] = games.height
        else:
            report["errors"]["schedule"] = schedule_result.reason or schedule_result.status.value

        if "game_feed" in requested:
            if games.is_empty():
                report["errors"]["game_feed"] = "NO_GAMES"
            else:
                feed_counts = {"probable_pitchers": 0, "lineups": 0, "rosters": 0}
                season_by_game = dict(games.select("game_pk", "season").iter_rows())
                for game_pk in games["game_pk"].unique().to_list():
                    result = self.mlb_stats.game_feed(int(game_pk), force=force)
                    if result.status is not ProviderStatus.AVAILABLE or result.frame is None or result.metadata is None:
                        report["errors"][f"game_feed:{game_pk}"] = result.reason or result.status.value
                        continue
                    outputs = normalize_game_feed(result.frame, result.metadata)
                    for table, frame in outputs.items():
                        if not frame.is_empty():
                            self.store.write(table, int(season_by_game[int(game_pk)]), frame)
                            feed_counts[table] += frame.height
                report["row_counts"].update(feed_counts)

        if "transactions" in requested:
            result = self.mlb_stats.transactions(start, end, force=force)
            if result.status is ProviderStatus.AVAILABLE and result.frame is not None and result.metadata is not None:
                transactions = normalize_transactions(result.frame, result.metadata)
                if not transactions.is_empty():
                    # MLB transaction endpoints do not provide a reliable season
                    # field, so partition by requested start year and retain exact
                    # transaction/effective dates inside the records.
                    self.store.write("transactions", start.year, transactions)
                report["row_counts"]["transactions"] = transactions.height
            else:
                report["errors"]["transactions"] = result.reason or result.status.value
        report["status"] = "DEGRADED" if report["errors"] else "AVAILABLE"
        return report

    def backfill_statcast(self, start: date, end: date, *, force: bool = False) -> dict[str, Any]:
        if self.statcast is None:
            raise RuntimeError("Statcast provider is not configured")
        current = start
        rows = 0
        errors: dict[str, str] = {}
        while current <= end:
            chunk_end = min(end, current + timedelta(days=MAX_STATCAST_DAYS - 1))
            result = self.statcast.pitches(current, chunk_end, force=force)
            key = f"{current.isoformat()}:{chunk_end.isoformat()}"
            if result.status is not ProviderStatus.AVAILABLE or result.frame is None or result.metadata is None:
                errors[key] = result.reason or result.status.value
            elif not result.frame.is_empty():
                normalized = normalize_statcast(result.frame, result.metadata)
                for season_data in normalized.with_columns(
                    pl.col("game_date").str.slice(0, 4).cast(pl.Int64).alias("_season")
                ).partition_by("_season", as_dict=False):
                    season = int(season_data["_season"].item(0))
                    self.store.write("statcast_pitches", season, season_data.drop("_season"))
                rows += normalized.height
            current = chunk_end + timedelta(days=1)
        return {
            "sport": "mlb",
            "version": "v3",
            "provider": "statcast",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "row_counts": {"statcast_pitches": rows},
            "errors": errors,
            "status": "DEGRADED" if errors else "AVAILABLE",
        }

    def audit(self, season: int) -> dict[str, Any]:
        return audit_mlb_v3(self.store, season)
