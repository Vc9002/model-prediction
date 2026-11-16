"""NCAAB v7 multi-season margin engine, SERVING side (quick 261115-v7s). Off by default.

Turns the research components (``ncaab_v7_multiseason``) plus the frozen F1 combiner into a per-game margin
mean and Student-t scale that ``NCAABSpreadModelV6`` can swap in for D1-vs-D1 games when
``models.NCAAB.margin_engine == "v7"``. Nothing here is registered, promoted or scheduled.

Why an archive: v7's gain needs three seasons of history (carryover) and per-season ESPN conference ids, but
the live history only holds 2025-26 onward. ``data/archive/ncaab_v7_prior_seasons.jsonl.gz`` (built by
``scripts/build_ncaab_v7_serving_archive.py``) freezes 2021-22..2024-25 games, their box/rank/neutral stats and
every season's conference map. Live games are merged on top by event id.

Point-in-time contract: the merged history is cut at midnight ET of the earliest upcoming game's day, the same
cut the research replay used, so no game on or after the target day (archived or live) can reach a component.
Conference membership for the game's own season is read from the map (public before tip-off), never a result.

Fail closed: a missing/corrupt archive, a team naming gap or any exception raises; the caller (v6) logs a
warning and keeps its own margin. A game that is not D1-vs-D1 in the as-of map gets no v7 row (v6 serves it);
the research non-D1 branch is deliberately not used for serving.

The combiner constants are the frozen config of ``outputs/ncaab/ncaab_v7_multiseason_frozen.json`` (pin 2, fit
on 2022-23 and 2023-24 only). The math is a few lines duplicated from ``scripts/ncaab_v7_multiseason.py``
because ``src`` must not import ``scripts``; ``tests/test_ncaab_v7_serving.py`` pins equality with that script's
``apply_config`` and with the frozen JSON, so drift fails a test.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import numpy as np

from ..domain import EASTERN, parse_utc
from ..features.base import GameRecord
from ..features.ncaab_scoreboard_stats import GameStats, NCAABScoreboardStats, TeamGameStats
from .basketball import UpcomingGame
from .ncaab_v7_multiseason import NCAABV7MultiSeasonModel

_logger = logging.getLogger(__name__)

ARCHIVE_PATH = Path(__file__).resolve().parents[3] / "data" / "archive" / "ncaab_v7_prior_seasons.jsonl.gz"

# Frozen config (pin ncaab-v7-multiseason-pin-2, fit seasons 2022-23 and 2023-24).
V7_VARIANT = "rg_c010_l02_k14"
V7_COEF = (0.9592933218909206, 0.1610938843028618)  # F1: [ridge coefficient, tail coefficient]
V7_SCALES = (11.271310244143418, 10.872746171852754, 10.711292254487482, 9.967310767878121)
BUCKET_CUTS = (1, 3, 8)  # current-season sample buckets 0 | 1-2 | 3-7 | 8+
TAIL_KNOT = 12.0  # points of |v6 as-of margin| beyond which the tail term shrinks blowouts


@dataclass(frozen=True)
class V7Margin:
    """v7 forecast for one D1-vs-D1 game: home margin mean and Student-t (df 8) scale."""

    mean: float
    scale: float
    ridge: float
    v6_asof_mean: float
    sample: int


def combine_f1(ridge: float, v6_mean: float, sample: int) -> tuple[float, float]:
    """Frozen F1 combiner: ``a*ridge + c*tail(v6)`` and the per-sample-bucket t-scale."""
    tail = float(np.sign(v6_mean) * max(abs(v6_mean) - TAIL_KNOT, 0.0))
    mean = V7_COEF[0] * ridge + V7_COEF[1] * tail
    bucket = int(np.searchsorted(np.array(BUCKET_CUTS), sample, side="right"))
    return float(mean), float(V7_SCALES[bucket])


@dataclass(frozen=True)
class Archive:
    games: tuple[GameRecord, ...]
    stats: dict[str, GameStats]
    conferences: dict[int, dict[str, str]]
    max_season_year: int


def _team_stats(row: Mapping[str, Any], side: str) -> TeamGameStats | None:
    vals = {
        "fga": row[f"{side}_fieldGoalsAttempted"],
        "fgm": row[f"{side}_fieldGoalsMade"],
        "tpa": row[f"{side}_threePointFieldGoalsAttempted"],
        "tpm": row[f"{side}_threePointFieldGoalsMade"],
        "fta": row[f"{side}_freeThrowsAttempted"],
        "ftm": row[f"{side}_freeThrowsMade"],
        "reb": row[f"{side}_rebounds"],
        "ast": row[f"{side}_assists"],
    }
    if any(v is None for v in vals.values()):
        return None
    rank = row.get(f"{side}_ap_rank")
    return TeamGameStats(
        team=str(row[f"{side}_team"]),
        points=int(row[f"{side}_score"]),
        rank=int(rank) if rank is not None and 1 <= int(rank) <= 25 else None,
        **{k: int(v) for k, v in vals.items()},
    )


def load_archive(path: Path | str | None = None) -> Archive:
    """Read and verify the frozen archive (games sha256 must match the meta line)."""
    path = ARCHIVE_PATH if path is None else path  # resolved at call time so tests can repoint it
    with gzip.open(path, "rb") as fh:
        raw = fh.read().decode("utf-8")
    head, _, body = raw.partition("\n")
    meta = json.loads(head)["_meta"]
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != meta["games_sha256"]:
        raise ValueError(f"{path}: games sha256 does not match the archive meta")
    games: list[GameRecord] = []
    stats: dict[str, GameStats] = {}
    for line in body.splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        games.append(
            GameRecord(
                event_id=str(r["event_id"]),
                event_start_utc=r["event_start_utc"],
                league="NCAAB",
                away_team=r["away_team"],
                home_team=r["home_team"],
                away_score=int(r["away_score"]),
                home_score=int(r["home_score"]),
                season_type=r["season_type"],
                season_year=int(r["season_year"]),
                competition_type=r["competition_type"],
            )
        )
        home, away = _team_stats(r, "home"), _team_stats(r, "away")
        if home is not None and away is not None:
            stats[str(r["event_id"])] = GameStats(
                event_id=str(r["event_id"]),
                home=home,
                away=away,
                conference_game=bool(r["conference_competition"]),
                neutral_site=bool(r["neutral"]),
            )
    if len(games) != int(meta["n_games"]):
        raise ValueError(f"{path}: {len(games)} games, meta says {meta['n_games']}")
    conferences = {
        int(s): {str(t): str(c) for t, c in m.items()} for s, m in meta["conferences_by_season_year"].items()
    }
    return Archive(tuple(games), stats, conferences, int(meta["max_archived_season_year"]))


class _ServingStats(NCAABScoreboardStats):
    """Live archived scoreboards plus the frozen archive's per-game stats (live wins on a shared id)."""

    def __init__(self, raw_root: Path | str | None, archived: Mapping[str, GameStats]) -> None:
        super().__init__(raw_root)
        self._archived = archived
        self._merged = False

    def _load(self) -> dict[str, GameStats]:
        table = super()._load()
        if not self._merged:
            for event_id, stats in self._archived.items():
                table.setdefault(event_id, stats)
            self._merged = True
        return table


def et_midnight(start_utc: str) -> datetime:
    """Midnight ET of the game's ET calendar day: the strict PIT cut (same-day games never count)."""
    day = parse_utc(start_utc).astimezone(EASTERN).date()
    return datetime.combine(day, dtime.min, tzinfo=EASTERN)


class NCAABV7Serving:
    """Merges live history with the frozen archive and serves v7 margins for D1-vs-D1 games."""

    def __init__(self, archive: Archive | None = None, live_stats_root: Path | str | None = None) -> None:
        self.archive = archive if archive is not None else load_archive()
        self._stats = _ServingStats(live_stats_root, self.archive.stats)
        self._model = NCAABV7MultiSeasonModel(self.archive.conferences, asof_map=True)
        # the research model builds its own stats reader; point every reader at the merged table
        self._model._stats = self._stats
        self._model._v6._stats = self._stats
        if getattr(self._model._v6_total, "_stats", None) is not None:
            self._model._v6_total._stats = self._stats

    def merged_history(self, live_history: Sequence[GameRecord], cutoff: datetime) -> list[GameRecord]:
        """Archive plus live games strictly before ``cutoff``, one record per event id."""
        seen = {g.event_id for g in self.archive.games}
        pool = [g for g in self.archive.games if g.start < cutoff]
        pool += [g for g in live_history if g.event_id not in seen and g.start < cutoff]
        pool.sort(key=lambda g: (g.start, g.event_id))
        return pool

    def margins(
        self, live_history: Sequence[GameRecord], upcoming: Sequence[UpcomingGame]
    ) -> dict[str, V7Margin]:
        """event id -> v7 margin for each D1-vs-D1 upcoming game (others are absent: v6 serves them)."""
        if not upcoming:
            return {}
        earliest = min(upcoming, key=lambda u: parse_utc(u.event_start_utc))
        history = self.merged_history(live_history, et_midnight(earliest.event_start_utc))
        if not history:
            raise ValueError("no history before the target day")
        # fresh rows: the research fit saw no market line, availability gap or rest override
        clean = [
            UpcomingGame(
                u.event_id,
                u.event_start_utc,
                u.away_team,
                u.home_team,
                league="NCAAB",
                neutral_site=bool(u.neutral_site),
            )
            for u in upcoming
        ]
        out: dict[str, V7Margin] = {}
        for row in self._model.components(history, clean):
            ridge = row.get(V7_VARIANT)
            if not (row["d1_home"] and row["d1_away"]) or ridge is None:
                continue
            mean, scale = combine_f1(float(ridge), float(row["v6_mean"]), int(row["sample"]))
            out[str(row["event_id"])] = V7Margin(
                mean=mean,
                scale=scale,
                ridge=float(ridge),
                v6_asof_mean=float(row["v6_mean"]),
                sample=int(row["sample"]),
            )
        return out


_SERVING: dict[str, NCAABV7Serving] = {}


def default_serving() -> NCAABV7Serving:
    """Process-wide instance (the archive load and model build are slow; v6 is rebuilt per slate)."""
    key = str(ARCHIVE_PATH)
    if key not in _SERVING:
        _SERVING[key] = NCAABV7Serving()
    return _SERVING[key]
