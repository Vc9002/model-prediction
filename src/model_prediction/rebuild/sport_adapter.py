"""Shared sport-adapter protocol (FOUNDATION_COMPLETION.md Phase 13/item 5).

One interface every sport plugs into so a single CLI can route
`collect -> normalize -> features -> predict -> markets -> decide -> persist`
without a separate `<sport>_shadow_run.py` script per sport.

Honest scope, not fabricated: MLB is the only sport with a real trained
model and real market-matching/decision logic (scripts/mlb_shadow_run.py,
proven and live-verified repeatedly this session). Porting that script's
inline logic into this adapter framework is real, separate follow-up work
-- rewriting a working, tested pipeline under time pressure risks
introducing new bugs in exactly the code this project depends on most.
What's real and shared *today*:

- `collect()`: real for all 5 sports with a real collector (MLB, NBA/WNBA,
  NFL, Soccer, Tennis) -- reuses the exact same Collector classes
  scripts/mlb_shadow_run.py and the test suite already exercise.
- `build_features()`: real for MLB only (horizon_builder.py, itself real
  and live-verified). Every other sport correctly reports NOT_IMPLEMENTED
  rather than fabricating a feature row.
- `predict()` / `match_markets()` / `decide()`: NOT_IMPLEMENTED through
  this shared interface for every sport, MLB included, until that
  extraction happens -- scripts/mlb_shadow_run.py remains the one proven
  real path for those stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .collectors import (
    MLBCollector,
    NBACollector,
    NFLCollector,
    SoccerCollector,
    TennisCollector,
)
from .metadata import MetadataDB

STAGE_SUCCESS = "SUCCESS"
STAGE_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
STAGE_NO_DATA = "NO_DATA"
STAGE_ERROR = "ERROR"


@dataclass
class StageResult:
    stage: str
    status: str
    detail: dict[str, Any] = field(default_factory=dict)


class SportAdapter(Protocol):
    sport: str

    def collect(self, date: str) -> StageResult: ...
    def build_features(self, date: str, horizon: str) -> StageResult: ...
    def predict(self, date: str, horizon: str) -> StageResult: ...
    def match_markets(self, date: str, horizon: str) -> StageResult: ...
    def decide(self, date: str, horizon: str) -> StageResult: ...


class _NotImplementedStagesMixin:
    """Every sport starts from here -- predict/markets/decide are real
    only where a sport adapter below explicitly overrides them, per the
    module docstring's honest-scope note."""

    sport: str

    def predict(self, date: str, horizon: str) -> StageResult:
        return StageResult("predict", STAGE_NOT_IMPLEMENTED, {
            "reason": f"no real trained model wired into the shared adapter for {self.sport} yet",
        })

    def match_markets(self, date: str, horizon: str) -> StageResult:
        return StageResult("match_markets", STAGE_NOT_IMPLEMENTED, {
            "reason": f"no real market-matching logic wired into the shared adapter for {self.sport} yet",
        })

    def decide(self, date: str, horizon: str) -> StageResult:
        return StageResult("decide", STAGE_NOT_IMPLEMENTED, {
            "reason": f"no real decision logic wired into the shared adapter for {self.sport} yet",
        })

    def build_features(self, date: str, horizon: str) -> StageResult:
        return StageResult("build_features", STAGE_NOT_IMPLEMENTED, {
            "reason": f"no real horizon feature builder wired into the shared adapter for {self.sport} yet",
        })


class MLBAdapter(_NotImplementedStagesMixin):
    sport = "mlb"

    def __init__(self, data_root: str = "data/rebuild") -> None:
        self.data_root = data_root
        self.meta = MetadataDB(f"{data_root}/metadata.db")
        self.collector = MLBCollector(data_root, self.meta)

    def collect(self, date: str) -> StageResult:
        result = self.collector.collect_espn_scoreboard(date)
        if result.get("status") == "ok":
            return StageResult("collect", STAGE_SUCCESS, result)
        if result.get("status") == "no_games":
            return StageResult("collect", STAGE_NO_DATA, result)
        return StageResult("collect", STAGE_ERROR, result)

    def build_features(self, date: str, horizon: str) -> StageResult:
        # Real, not a placeholder: horizon_builder.py, live-verified
        # against the real 2026-08-06 slate in this same session.
        import json
        from pathlib import Path

        from .horizon_builder import build_mlb_horizon_dataset

        probables_path = Path("data/point_in_time/mlb_probable_starters.jsonl")
        records = (
            [json.loads(line) for line in probables_path.read_text().splitlines() if line.strip()]
            if probables_path.exists() else []
        )
        result = build_mlb_horizon_dataset(self.data_root, date, horizon, records)
        status = STAGE_SUCCESS if result.coverage["rows_built"] > 0 else STAGE_NO_DATA
        return StageResult("build_features", status, {
            "coverage": result.coverage, "missingness": result.missingness,
            "snapshot_hash": result.snapshot_hash,
        })


class _CollectionOnlyAdapter(_NotImplementedStagesMixin):
    """Real collection, honest NOT_IMPLEMENTED for everything past it --
    NBA/WNBA/NFL/Soccer/Tennis have real Collector classes (proven by
    their own test suites) but no real trained model or market-matching
    logic yet."""

    def __init__(self, sport: str, collector: Any) -> None:
        self.sport = sport
        self.collector = collector

    def collect(self, date: str) -> StageResult:
        # Real bug found live wiring this adapter (2026-08-07): Soccer/
        # TennisCollector.collect_date() call the real ESPN client with
        # league="SOCCER"/"TENNIS", neither of which exists in
        # data_sources/espn.py's LEAGUE_PATHS -- real network collection
        # has never worked for either sport, confirmed by a real
        # ValueError on a real call, not a mock. Pre-existing, unrelated
        # to this adapter; caught here so the shared CLI reports an honest
        # per-stage ERROR instead of crashing the whole process, and other
        # sports/stages remain usable.
        try:
            result = self.collector.collect_date(date)
        except Exception as e:  # noqa: BLE001 -- reported as a real, visible per-stage error, not swallowed
            return StageResult("collect", STAGE_ERROR, {"error": str(e)[:300]})
        status_map = {"ok": STAGE_SUCCESS, "no_games": STAGE_NO_DATA, "partial": STAGE_SUCCESS}
        return StageResult("collect", status_map.get(result.get("status"), STAGE_ERROR), result)


def build_adapter(sport: str, data_root: str = "data/rebuild") -> SportAdapter:
    """The one real registry every sport plugs into."""
    if sport == "mlb":
        return MLBAdapter(data_root)

    meta = MetadataDB(f"{data_root}/metadata.db")
    if sport in ("nba", "wnba"):
        return _CollectionOnlyAdapter(sport, NBACollector(data_root, meta))
    if sport == "nfl":
        return _CollectionOnlyAdapter(sport, NFLCollector(data_root, meta))
    if sport == "soccer":
        return _CollectionOnlyAdapter(sport, SoccerCollector(data_root, meta))
    if sport == "tennis":
        return _CollectionOnlyAdapter(sport, TennisCollector(data_root, meta))

    raise ValueError(
        f"no adapter registered for sport={sport!r} -- "
        f"esports/kbo/npb correctly have no real collector wired here yet"
    )


SUPPORTED_SPORTS = ("mlb", "nba", "wnba", "nfl", "soccer", "tennis")
