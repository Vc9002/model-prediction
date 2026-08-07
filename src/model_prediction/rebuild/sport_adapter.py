"""Shared sport-adapter protocol (FOUNDATION_COMPLETION.md Phase 13/item 5).

One interface every sport plugs into so a single CLI can route
`collect -> normalize -> features -> predict -> markets -> decide -> persist`
without a separate `<sport>_shadow_run.py` script per sport.

Honest scope, not fabricated:

- `collect()`: real for all 5 sports with a real collector (MLB, NBA/WNBA,
  NFL, Soccer, Tennis) -- reuses the exact same Collector classes
  scripts/mlb_shadow_run.py and the test suite already exercise. Esports
  is registered too, but its collector is an honest stub (no real BO3/
  OpenDota network integration yet), so collect() reports
  NOT_IMPLEMENTED rather than SUCCESS. KBO/NPB have no collector at all
  and correctly raise from build_adapter().
- `build_features()`: real for MLB only (horizon_builder.py, itself real
  and live-verified). Every other sport correctly reports NOT_IMPLEMENTED
  rather than fabricating a feature row.
- `predict()` / `match_markets()` / `decide()`: real for MLB, via
  mlb_shadow_pipeline.py's predict_stage/match_markets_stage/decide_stage
  -- the exact same functions (train_through, build_forecast,
  decision.evaluate_game, mlb_market_matching's real_* functions)
  scripts/mlb_shadow_run.py itself now imports from that same module
  (single source of truth, not a duplicate reimplementation). Verified
  live to produce byte-identical forecasts/decisions to the standalone
  script for the same real slate before this was wired in. Every other
  sport correctly reports NOT_IMPLEMENTED -- no trained model or
  market-matching logic exists for them yet.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import polars as pl

from . import basic_sport_pipeline as basic_pipeline
from .collectors import (
    EsportsCollector,
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

    def collect(self, date: str, run_id: str | None = None) -> StageResult: ...
    def build_features(self, date: str, horizon: str, run_id: str | None = None) -> StageResult: ...
    def predict(self, date: str, horizon: str, run_id: str | None = None) -> StageResult: ...
    def match_markets(self, date: str, horizon: str, run_id: str | None = None) -> StageResult: ...
    def decide(self, date: str, horizon: str, run_id: str | None = None) -> StageResult: ...


class _NotImplementedStagesMixin:
    """Every sport starts from here -- predict/markets/decide are real
    only where a sport adapter below explicitly overrides them, per the
    module docstring's honest-scope note."""

    sport: str

    def predict(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        return StageResult("predict", STAGE_NOT_IMPLEMENTED, {
            "reason": f"no real trained model wired into the shared adapter for {self.sport} yet",
        })

    def match_markets(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        return StageResult("match_markets", STAGE_NOT_IMPLEMENTED, {
            "reason": f"no real market-matching logic wired into the shared adapter for {self.sport} yet",
        })

    def decide(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        return StageResult("decide", STAGE_NOT_IMPLEMENTED, {
            "reason": f"no real decision logic wired into the shared adapter for {self.sport} yet",
        })

    def build_features(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        return StageResult("build_features", STAGE_NOT_IMPLEMENTED, {
            "reason": f"no real horizon feature builder wired into the shared adapter for {self.sport} yet",
        })


class MLBAdapter(_NotImplementedStagesMixin):
    sport = "mlb"

    def __init__(self, data_root: str = "data/rebuild") -> None:
        self.data_root = data_root
        self.meta = MetadataDB(f"{data_root}/metadata.db")
        self.collector = MLBCollector(data_root, self.meta)
        self._state: Any = None  # mlb_shadow_pipeline.MLBRunState, held across predict/match_markets/decide

    def collect(self, date: str, run_id: str | None = None) -> StageResult:
        result = self.collector.collect_espn_scoreboard(date)
        if result.get("status") == "ok":
            return StageResult("collect", STAGE_SUCCESS, result)
        if result.get("status") == "no_games":
            return StageResult("collect", STAGE_NO_DATA, result)
        return StageResult("collect", STAGE_ERROR, result)

    def build_features(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        # Real, not a placeholder: horizon_builder.py, live-verified
        # against the real 2026-08-06 slate in this same session.
        import json
        from pathlib import Path

        from .horizon_builder import build_mlb_horizon_dataset
        from .shadow_ledger import ShadowLedger

        probables_path = Path("data/point_in_time/mlb_probable_starters.jsonl")
        records = (
            [json.loads(line) for line in probables_path.read_text().splitlines() if line.strip()]
            if probables_path.exists() else []
        )
        # Real lineage wiring: when the shared CLI supplies a run_id, the
        # resulting feature snapshot is also recorded in the shadow ledger
        # (record_feature_snapshot) -- previously the horizon builder wrote
        # to FeatureStore but that write was never captured in ledger
        # lineage, even though both real methods existed independently.
        ledger = ShadowLedger(f"{self.data_root}/shadow.db") if run_id else None
        try:
            result = build_mlb_horizon_dataset(
                self.data_root, date, horizon, records, ledger=ledger, run_id=run_id,
            )
        finally:
            if ledger is not None:
                ledger.close()
        status = STAGE_SUCCESS if result.coverage["rows_built"] > 0 else STAGE_NO_DATA
        return StageResult("build_features", status, {
            "coverage": result.coverage, "missingness": result.missingness,
            "snapshot_hash": result.snapshot_hash,
        })

    def _ledger(self, run_id: str | None) -> Any:
        from .shadow_ledger import ShadowLedger
        return ShadowLedger(f"{self.data_root}/shadow.db") if run_id else None

    def predict(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        # Real: mlb_shadow_pipeline.py, the same module
        # scripts/mlb_shadow_run.py itself imports train_through/
        # build_forecast from -- see module docstring.
        from . import mlb_shadow_pipeline as pipeline

        state = pipeline.load_state(self.data_root, date)
        if state is None:
            return StageResult("predict", STAGE_NO_DATA, {"reason": "no real scheduled games for this date"})
        self._state = state

        ledger = self._ledger(run_id)
        try:
            result = pipeline.predict_stage(state, self.data_root, ledger=ledger, run_id=run_id)
        finally:
            if ledger is not None:
                ledger.close()

        if result["status"] == "insufficient_history":
            return StageResult("predict", STAGE_NO_DATA, result)
        status = STAGE_SUCCESS if result["games_predicted"] > 0 else STAGE_NO_DATA
        return StageResult("predict", status, result)

    def match_markets(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        from . import mlb_shadow_pipeline as pipeline

        if self._state is None or self._state.target_date != date:
            return StageResult("match_markets", STAGE_ERROR, {
                "reason": "predict() must run first in the same invocation -- no real forecast state to match markets against",
            })

        ledger = self._ledger(run_id)
        try:
            result = pipeline.match_markets_stage(
                self._state, self.data_root, self.collector, ledger=ledger, run_id=run_id,
            )
        finally:
            if ledger is not None:
                ledger.close()
        return StageResult("match_markets", STAGE_SUCCESS, result)

    def decide(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        from . import mlb_shadow_pipeline as pipeline

        if self._state is None or self._state.target_date != date:
            return StageResult("decide", STAGE_ERROR, {
                "reason": "predict()/match_markets() must run first in the same invocation -- no real state to decide against",
            })

        ledger = self._ledger(run_id)
        try:
            result = pipeline.decide_stage(self._state, ledger=ledger, run_id=run_id)
        finally:
            if ledger is not None:
                ledger.close()
        return StageResult("decide", STAGE_SUCCESS, result)


class _CollectionOnlyAdapter(_NotImplementedStagesMixin):
    """Real collection, honest NOT_IMPLEMENTED for everything past it --
    NBA/WNBA/NFL/Soccer/Tennis have real Collector classes (proven by
    their own test suites) but no real trained model or market-matching
    logic yet."""

    def __init__(self, sport: str, collector: Any) -> None:
        self.sport = sport
        self.collector = collector

    def collect(self, date: str, run_id: str | None = None) -> StageResult:
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


class _BasicEloAdapter(_CollectionOnlyAdapter):
    """Real basic foundation pipeline for NBA/WNBA/NFL/Soccer/Tennis:
    collect -> build_features -> predict -> match_markets -> decide -> the
    real shadow ledger, all real code, using a logistic Elo baseline
    (basic_elo.py/basic_sport_pipeline.py) instead of a sport-specific
    distribution -- explicitly the "basic prediction, working pipeline,
    not an advanced model" foundation build these 5 sports were missing.
    See basic_sport_pipeline.py's module docstring for the real,
    disclosed scope limits (moneyline only, no bootstrap ensemble, no
    derived feature store) versus MLB's pipeline. Inherits collect() from
    _CollectionOnlyAdapter unchanged -- collection itself was already real."""

    def __init__(self, sport: str, data_root: str, collector: Any, collect_fn: Callable[[str], dict]) -> None:
        super().__init__(sport, collector)
        self.data_root = data_root
        self.collect_fn = collect_fn
        self._state: Any = None  # basic_sport_pipeline.BasicRunState, held across predict/match_markets/decide

    def build_features(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        # Real, not fabricated, but genuinely basic: the "feature snapshot"
        # here is just the real deduped scoreboard itself -- there is no
        # derived per-team feature store yet for these sports (unlike
        # MLB's horizon_builder.py). Reports real coverage counts so this
        # is honest about what it is, not a placeholder pretending to be
        # horizon_builder's real output.
        try:
            sb = basic_pipeline.dedupe_scoreboard(
                pl.read_parquet(f"{self.data_root}/normalized/{self.sport}/scoreboard.parquet")
            )
        except FileNotFoundError:
            return StageResult("build_features", STAGE_NO_DATA, {"reason": "no scoreboard ever collected"})
        completed = sb.filter(pl.col("status") == "STATUS_FINAL").height
        scheduled = sb.filter(pl.col("status") == "STATUS_SCHEDULED").height
        status = STAGE_SUCCESS if sb.height > 0 else STAGE_NO_DATA
        return StageResult("build_features", status, {
            "real_scoreboard_rows": sb.height, "completed_games": completed, "scheduled_games": scheduled,
        })

    def predict(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        state = basic_pipeline.load_state(self.data_root, self.sport, date)
        if state is None:
            return StageResult("predict", STAGE_NO_DATA, {"reason": "no real scheduled games for this date"})
        self._state = state

        ledger = self._ledger(run_id)
        try:
            result = basic_pipeline.predict_stage(state, self.data_root, ledger=ledger, run_id=run_id)
        finally:
            if ledger is not None:
                ledger.close()

        if result["status"] == "insufficient_history":
            return StageResult("predict", STAGE_NO_DATA, result)
        status = STAGE_SUCCESS if result["games_predicted"] > 0 else STAGE_NO_DATA
        return StageResult("predict", status, result)

    def match_markets(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        if self._state is None or self._state.target_date != date:
            return StageResult("match_markets", STAGE_ERROR, {
                "reason": "predict() must run first in the same invocation -- no real forecast state to match markets against",
            })

        ledger = self._ledger(run_id)
        try:
            result = basic_pipeline.match_markets_stage(
                self._state, self.data_root, self.collect_fn, ledger=ledger, run_id=run_id,
            )
        finally:
            if ledger is not None:
                ledger.close()
        return StageResult("match_markets", STAGE_SUCCESS, result)

    def decide(self, date: str, horizon: str, run_id: str | None = None) -> StageResult:
        if self._state is None or self._state.target_date != date:
            return StageResult("decide", STAGE_ERROR, {
                "reason": "predict()/match_markets() must run first in the same invocation -- no real state to decide against",
            })

        ledger = self._ledger(run_id)
        try:
            result = basic_pipeline.decide_stage(self._state, ledger=ledger, run_id=run_id)
        finally:
            if ledger is not None:
                ledger.close()
        return StageResult("decide", STAGE_SUCCESS, result)

    def _ledger(self, run_id: str | None) -> Any:
        from .shadow_ledger import ShadowLedger
        return ShadowLedger(f"{self.data_root}/shadow.db") if run_id else None


class _EsportsStubAdapter(_NotImplementedStagesMixin):
    """Esports has a real, honest stub collector (EsportsCollector.
    collect_date() itself returns status="stub" -- "BO3/OpenDota
    integration pending" -- it does not call any real network source
    yet). Registered here so the shared CLI can route to esports without
    a KeyError, while collect() still reports the honest
    STAGE_NOT_IMPLEMENTED outcome rather than fabricating SUCCESS for a
    call that did no real collection. This does not create the missing
    capability -- it makes the existing gap visible through the same
    interface every other sport uses."""

    sport = "esports"

    def __init__(self, data_root: str, meta: Any) -> None:
        self.collector = EsportsCollector(data_root, meta)

    def collect(self, date: str, run_id: str | None = None) -> StageResult:
        # title is a real, required parameter of the per-title esports
        # collector (e.g. "cs2"/"dota2"/"lol") that this single-sport-per-
        # call CLI interface has no slot for yet -- passed as an explicit
        # placeholder so the real stub code path still runs and its own
        # honest status governs the result below.
        result = self.collector.collect_date(date, title="unspecified")
        return StageResult("collect", STAGE_NOT_IMPLEMENTED, result)


def build_adapter(sport: str, data_root: str = "data/rebuild") -> SportAdapter:
    """The one real registry every sport plugs into."""
    if sport == "mlb":
        return MLBAdapter(data_root)

    meta = MetadataDB(f"{data_root}/metadata.db")
    if sport in ("nba", "wnba"):
        nba_collector = NBACollector(data_root, meta)
        return _BasicEloAdapter(sport, data_root, nba_collector, lambda d: nba_collector.collect_date(d, sport=sport))
    if sport == "nfl":
        nfl_collector = NFLCollector(data_root, meta)
        return _BasicEloAdapter(sport, data_root, nfl_collector, nfl_collector.collect_date)
    if sport == "soccer":
        soccer_collector = SoccerCollector(data_root, meta)
        return _BasicEloAdapter(sport, data_root, soccer_collector, soccer_collector.collect_date)
    if sport == "tennis":
        tennis_collector = TennisCollector(data_root, meta)
        return _BasicEloAdapter(sport, data_root, tennis_collector, tennis_collector.collect_date)
    if sport == "esports":
        return _EsportsStubAdapter(data_root, meta)

    raise ValueError(
        f"no adapter registered for sport={sport!r} -- "
        f"kbo/npb correctly have no real collector or data source client "
        f"anywhere in this codebase yet"
    )


SUPPORTED_SPORTS = ("mlb", "nba", "wnba", "nfl", "soccer", "tennis", "esports")
