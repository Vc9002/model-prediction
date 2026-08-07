"""Operational early/mid/late horizon dataset builder (FOUNDATION_COMPLETION.md
Phase 7).

MLB only, for now -- the one sport with real point-in-time feature builders
(mlb_features.py). Other sports correctly remain out of scope per CLAUDE.md's
own sequencing ("MLB must clear its own gate first").

Real, disclosed limitation, not hidden: the rolling Statcast features
(pitcher/bullpen quality) are computed from completed-game history at
calendar-day granularity (mlb_features.pitcher_rolling_features's
`game_date_str < before_game_date`) -- Statcast pitch data only carries a
game_date, not a wall-clock timestamp, so day-boundary is the real available
granularity for that history, identical across all three horizons for the
same game_date. What genuinely differs by horizon is decision_time_utc
itself and anything joined against it with a real observation timestamp --
today, that's the probable-starter lookup (point_in_time_probable_starters),
which can legitimately return different starters (or none yet) at T-36h vs
T-60m for the same real game.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from .horizons import HORIZONS, compute_decision_times, horizon_specs_for_sport
from .mlb_features import (
    build_game_feature_row,
    dedupe_scoreboard,
    identify_starters,
    load_probable_starter_records,
    load_raw_statcast_dates,
    normalize_statcast_pitches,
    point_in_time_probable_starters,
)
from .storage import FeatureStore, NormalizedStore


@dataclass
class HorizonBuildResult:
    sport: str
    horizon: str
    game_date: str
    decision_times: dict[str, str]  # event_id -> real decision_time_utc used
    rows: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    missingness: dict[str, Any] = field(default_factory=dict)
    available_information: list[str] = field(default_factory=list)  # CLAUDE.md Part 1 sec 9
    snapshot_hash: str | None = None

    def to_dataframe(self) -> pl.DataFrame:
        return pl.DataFrame(self.rows) if self.rows else pl.DataFrame()


def build_mlb_horizon_dataset(
    data_root: str,
    game_date: str,
    horizon: str,
    probable_starter_records: list[dict],
    *,
    ledger: Any | None = None,  # ShadowLedger -- typed Any to avoid a hard import cycle risk
    run_id: str | None = None,
) -> HorizonBuildResult:
    """Real horizon-dataset build for one MLB calendar date.

    1. determine each real scheduled game's decision_time_utc for this horizon
    2. retrieve only valid prior probable-starter observations (point-in-time)
    3. execute the real feature-group builders (starter/bullpen/park/weather)
    4. record missing groups honestly, not silently
    5. persist an immutable, versioned feature snapshot (FeatureStore)
    6. generate a real coverage/missingness report

    When `ledger` and `run_id` are both given, also records a real
    feature_snapshots row (FOUNDATION_COMPLETION.md Phase 12 lineage) --
    real gap closed here: the horizon builder wrote to FeatureStore but
    never recorded that write in ShadowLedger, so the lineage chain from
    raw source through feature snapshot was not actually reconstructable
    even though both real methods existed.
    """
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}, got {horizon!r}")

    # Deferred import: build_live_game_feature_row needs pybaseball's
    # lookup_pitcher_id, expensive to import for callers that never touch
    # the live-starter path (e.g. offline analysis of a completed date).
    from .mlb_features import build_live_game_feature_row

    norm = NormalizedStore(f"{data_root}/normalized")
    try:
        scoreboard = dedupe_scoreboard(norm.read("mlb", "scoreboard"))
    except FileNotFoundError:
        # No scoreboard collection has ever run for this data_root -- zero
        # real games is a valid, honest result, not a crash.
        scoreboard = pl.DataFrame(schema={"event_id": pl.Utf8, "event_start_utc": pl.Utf8, "status": pl.Utf8})
    todays_games = scoreboard.filter(pl.col("event_start_utc").str.starts_with(game_date))

    decision_times = compute_decision_times(scoreboard, game_date, horizon)

    starters_by_event = point_in_time_probable_starters(decision_times, probable_starter_records)

    # Rolling features need real PRIOR history, not just the decision
    # dates -- use every real completed game's date up through game_date,
    # matching train_mlb_rebuild_real_features.py's own backfill pattern.
    completed = scoreboard.filter(
        (pl.col("status") == "STATUS_FINAL") & (pl.col("event_start_utc") <= f"{game_date}T23:59:59")
    )
    backfill_dates = sorted({row["event_start_utc"][:10] for row in completed.iter_rows(named=True)} | {game_date})
    raw = load_raw_statcast_dates(data_root, backfill_dates)
    pitches = normalize_statcast_pitches(raw)
    starters_table = identify_starters(pitches)

    rows: list[dict[str, Any]] = []
    missing_reasons: dict[str, int] = {}
    decision_times_out: dict[str, str] = {}
    available_features = horizon_specs_for_sport("mlb")[horizon].available_features

    for g in todays_games.iter_rows(named=True):
        event_id = g["event_id"]
        decision_times_out[event_id] = decision_times[event_id].isoformat()
        probable = starters_by_event.get(event_id)
        if probable is None:
            missing_reasons["no_point_in_time_probable_starters"] = (
                missing_reasons.get("no_point_in_time_probable_starters", 0) + 1
            )
            continue

        row = build_live_game_feature_row(
            g, probable["home_starter"], probable["away_starter"],
            pitches, starters_table, data_root,
            decision_time_utc=decision_times[event_id],
        )
        if row is None:
            missing_reasons["starter_name_not_resolved_to_real_statcast_id"] = (
                missing_reasons.get("starter_name_not_resolved_to_real_statcast_id", 0) + 1
            )
            continue

        row["horizon"] = horizon
        row["decision_time_utc"] = decision_times_out[event_id]
        # CLAUDE.md Part 1 sec 9: "Every prediction must store:
        # decision_timestamp, horizon, available_information." Real gap
        # closed here: horizon_specs_for_sport() declared per-horizon
        # available_features for every sport but had zero real callers
        # anywhere in this codebase (verified via grep) -- a real spec
        # disconnected from any actual prediction record. Comma-joined
        # (not a list column) since FeatureStore.write_snapshot() writes
        # plain columnar Parquet with no list-type handling exercised
        # elsewhere in this module.
        row["available_information"] = ",".join(available_features)
        rows.append(row)

    total_games = todays_games.height
    coverage = {
        "total_games": total_games,
        "rows_built": len(rows),
        "coverage_fraction": (len(rows) / total_games) if total_games else 0.0,
    }
    missingness = {
        "missing_games": total_games - len(rows),
        "missing_reasons": missing_reasons,
    }

    result = HorizonBuildResult(
        sport="mlb", horizon=horizon, game_date=game_date,
        decision_times=decision_times_out, rows=rows,
        coverage=coverage, missingness=missingness,
        available_information=available_features,
    )

    df = result.to_dataframe()
    if df.height > 0:
        # Real bug fixed here (found by external review, verified before
        # fixing): this previously hashed only {game_date, horizon,
        # event_ids} -- metadata, not the actual feature content.
        # FeatureStore.write_snapshot() treats snapshot_hash as an
        # immutable content identity (a write with an already-seen hash is
        # a silent no-op, by design -- see storage.py). If the exact same
        # date/horizon/event_ids were rebuilt after a real source
        # correction (a probable-starter revision, a corrected Statcast
        # pitch, a reprocessed weather observation), the metadata-only
        # hash would be identical even though the real feature values
        # changed -- the corrected data would silently never be
        # persisted, masquerading as the stale version already on disk.
        # Hash the actual row content instead, sorted by event_id so row
        # order never affects the hash.
        canonical_rows = sorted(rows, key=lambda r: r["event_id"])
        payload_hash_input = json.dumps(
            {"game_date": game_date, "horizon": horizon, "rows": canonical_rows},
            sort_keys=True, default=str,
        ).encode("utf-8")
        snapshot_hash = hashlib.sha256(payload_hash_input).hexdigest()
        store = FeatureStore(f"{data_root}/features")
        store.write_snapshot("mlb", horizon, df, snapshot_hash=snapshot_hash)
        result.snapshot_hash = snapshot_hash

        if ledger is not None and run_id is not None:
            ledger.record_feature_snapshot(
                run_id=run_id, sport="mlb", horizon=horizon, dataset_hash=snapshot_hash,
                row_count=df.height,
            )

    return result


@dataclass
class MLBHistoricalDatasetResult:
    """One immutable historical feature dataset (CLAUDE.md's next-phase
    Task 4: "ONE DATASET -> all model-family experiments"), covering every
    real completed MLB game between start_date and end_date at one
    horizon. Every model-family training/ablation script should consume
    this instead of independently rebuilding the same feature-row loop --
    before this, three real training scripts plus mlb_shadow_pipeline.py's
    own walk-forward retraining step each had their own copy of it, which
    could silently drift from each other."""
    horizon: str
    start_date: str
    end_date: str
    features: pl.DataFrame
    unmatched_games: int  # real completed games whose ESPN team names never mapped to a real Statcast club
    dataset_hash: str

    @property
    def matched_games(self) -> int:
        return self.features.height

    @property
    def starters_known_games(self) -> int:
        return int(self.features["starters_known"].sum()) if self.features.height else 0


def build_mlb_historical_horizon_dataset(
    data_root: str, start_date: str, end_date: str, horizon: str,
) -> MLBHistoricalDatasetResult:
    """The one authoritative historical MLB feature dataset builder.

    Required sequence (CLAUDE.md): canonical event -> horizon decision
    timestamp -> point-in-time-safe probable starter -> prior-only
    Statcast pitcher/bullpen history -> park/environment -> explicit
    missingness -> targets attached only after features freeze. This is
    exactly what build_game_feature_row() already does per game (Task 1's
    starter-parity fix); this function is the shared loop over every real
    completed game in [start_date, end_date] that calls it once, instead
    of four independent copies of that loop.

    Uses the identical real feature definitions the live pipeline uses --
    build_game_feature_row() and build_live_game_feature_row() both
    ultimately call the same underlying pitcher/bullpen/park/weather
    functions in mlb_features.py, so there is no separate "training-only"
    feature computation path to silently drift from live inference.

    Every real completed game in range gets a row (never dropped for a
    missing starter -- starters_known=0.0/starter_missing_reason marks
    that explicitly instead, per CLAUDE.md's "missingness is data"
    principle), except when the ESPN team names themselves can't be
    mapped to a real Statcast club abbreviation, which build_game_feature_row()
    already treats as un-buildable rather than fabricated.

    dataset_hash is a real content hash (sorted by event_id, not
    insertion order) over the actual row values -- an immutable identity
    for exactly this dataset, matching horizon_builder.build_mlb_horizon_dataset()'s
    own snapshot_hash fix (metadata-only hashing would silently mask a
    real feature-value correction that didn't also change which games
    matched).
    """
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}, got {horizon!r}")

    sb = dedupe_scoreboard(pl.read_parquet(f"{data_root}/normalized/mlb/scoreboard.parquet"))
    completed = sb.filter(
        (pl.col("status") == "STATUS_FINAL")
        & (pl.col("event_start_utc") >= start_date)
        & (pl.col("event_start_utc") <= f"{end_date}T23:59:59")
    ).sort("event_start_utc")

    backfill_dates = sorted({row["event_start_utc"][:10] for row in completed.iter_rows(named=True)})
    raw = load_raw_statcast_dates(data_root, backfill_dates)
    pitches = normalize_statcast_pitches(raw)
    starters = identify_starters(pitches)
    probable_records = load_probable_starter_records()

    rows: list[dict[str, Any]] = []
    unmatched = 0
    for g in completed.iter_rows(named=True):
        row = build_game_feature_row(g, pitches, starters, data_root, horizon, probable_records)
        if row is None:
            unmatched += 1
            continue
        rows.append(row)

    features = pl.DataFrame(rows).sort("event_start_utc") if rows else pl.DataFrame()
    canonical_rows = sorted(rows, key=lambda r: r["event_id"])
    dataset_hash = hashlib.sha256(
        json.dumps(
            {"start_date": start_date, "end_date": end_date, "horizon": horizon, "rows": canonical_rows},
            sort_keys=True, default=str,
        ).encode("utf-8")
    ).hexdigest()

    return MLBHistoricalDatasetResult(
        horizon=horizon, start_date=start_date, end_date=end_date,
        features=features, unmatched_games=unmatched, dataset_hash=dataset_hash,
    )
