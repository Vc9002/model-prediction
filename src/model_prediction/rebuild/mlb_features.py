"""Real MLB pregame features from normalized Statcast pitches, weather, and
static park factors — replaces the rolling-home/away-score placeholder in
scripts/pipeline_mlb_e2e.py (see outputs/rebuild/takeover_status.md
Checkpoint 5).

Point-in-time discipline: every rolling feature for a decision at game G is
computed only from pitches thrown in games with an earlier game_date than G.
No same-game or future-game data ever enters a feature for G.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from .asof import point_in_time_join
from .horizons import HORIZON_HOURS_BEFORE
from .storage import utc_now

# Coarse, static single-season park factors (100 = neutral run environment).
# Sourced from long-run public park-factor consensus (not derived from this
# season's own outcome data — using this season's own results to build a
# park factor that then predicts this season's outcomes would be a real
# leak). Deliberately coarse: only the well-established extreme-and-neutral
# parks are listed with real numbers; everything else defaults to 100
# (neutral) rather than guessing at a number for less firmly established
# parks. Revisit with a real multi-year, run-environment-adjusted park
# factor model before treating this as more than a first-order signal.
MLB_PARK_FACTORS: dict[str, int] = {
    "Coors Field": 112,
    "Great American Ball Park": 105,
    "Chase Field": 104,
    "Guaranteed Rate Field": 103,
    "Yankee Stadium": 102,
    "Globe Life Field": 101,
    "Fenway Park": 101,
    "Wrigley Field": 100,
    "Truist Park": 100,
    "Angel Stadium": 100,
    "Target Field": 99,
    "Nationals Park": 99,
    "Busch Stadium": 98,
    "Citi Field": 97,
    "T-Mobile Park": 96,
    "Petco Park": 96,
    "Oracle Park": 92,
    "loanDepot park": 94,
    "Comerica Park": 97,
    "Kauffman Stadium": 98,
    "PNC Park": 97,
    "Progressive Field": 98,
    "American Family Field": 101,
    "Tropicana Field": 96,
    "Rogers Centre": 101,
    "Oriole Park at Camden Yards": 100,
    "Citizens Bank Park": 103,
    "Minute Maid Park": 101,
    "RingCentral Coliseum": 92,
}
DEFAULT_PARK_FACTOR = 100

# ESPN scoreboard uses full team display names ("Baltimore Orioles");
# Statcast uses 2-3 letter club abbreviations ("BAL"). Needed to join a
# scoreboard game (which has the real home/away score labels) to its
# Statcast pitches (which have the real starter/bullpen signal) — there's no
# shared game ID between the two sources.
ESPN_TO_STATCAST_ABBREV: dict[str, str] = {
    "Arizona Diamondbacks": "AZ", "Atlanta Braves": "ATL", "Athletics": "ATH",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS", "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS", "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET", "Houston Astros": "HOU",
    "Kansas City Royals": "KC", "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN",
    "New York Mets": "NYM", "New York Yankees": "NYY", "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}

SWING_DESCRIPTIONS = {
    "foul", "foul_tip", "hit_into_play", "swinging_strike",
    "swinging_strike_blocked", "missed_bunt", "foul_bunt",
}
WHIFF_DESCRIPTIONS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
CSW_DESCRIPTIONS = WHIFF_DESCRIPTIONS | {"called_strike"}


def dedupe_scoreboard(sb: pl.DataFrame) -> pl.DataFrame:
    """NormalizedStore.write() appends on every call with no primary-key
    enforcement, so repeated ESPN scoreboard collection produces multiple
    identical-content rows per real event_id (verified live: event_id
    401816384 had 2 rows, same score/status, different observed_at_utc —
    and across the full table, 188 STATUS_FINAL rows were only 135 real
    unique games). Real Checkpoint 2 storage-layer gap, not yet fixed there
    (see outputs/rebuild/takeover_status.md) — this is the consumer-side
    fix so every real script reading the scoreboard doesn't silently
    over-count games. Keeps the most-recently-observed row per event_id.
    """
    if sb.is_empty():
        return sb
    return (
        sb.sort("observed_at_utc")
        .group_by("event_id", maintain_order=True)
        .last()
    )


def load_probable_starter_records(
    path: str | Path = "data/rebuild/raw/mlb/probable_starters.jsonl",
) -> list[dict]:
    """Load the real archived probable-starter observations every horizon-
    aware starter resolution (resolve_horizon_starter_names(),
    point_in_time_probable_starters()) reads from. One shared loader so
    every script/pipeline that needs historical PIT-safe starters (training
    scripts, mlb_shadow_pipeline.py) reads the identical file the identical
    way, rather than each re-implementing its own JSONL read. Returns an
    empty list, not an error, when the archive doesn't exist yet -- callers
    already treat "no valid probable" as honest missingness, not a crash."""
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_raw_statcast_dates(raw_root: str | Path, dates: list[str]) -> pl.DataFrame:
    """Read and concatenate raw Statcast snapshot(s) for each date into one
    pitch-level table. Reads whatever the newest snapshot is per date (raw
    storage is content-addressed/immutable, so a date can have more than one
    snapshot — this takes the lexicographically-last, i.e. most recently
    hashed, which is a reasonable default for a single-collection-per-day
    workflow; a true "latest by observed_at" selection would need the
    RawStore ref's timestamp, not just the filename).
    """
    raw_root = Path(raw_root)
    frames = []
    for d in dates:
        record_dir = raw_root / "raw" / "pybaseball" / d
        if not record_dir.exists():
            continue
        for statcast_dir in record_dir.glob("statcast_*"):
            snapshots = sorted(statcast_dir.glob("*.json.gz"))
            if not snapshots:
                continue
            with gzip.open(snapshots[-1]) as f:
                payload = json.loads(f.read())
            if not payload:
                continue
            df = pl.DataFrame(payload, infer_schema_length=len(payload))
            frames.append(df)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


_NORMALIZED_PITCH_SCHEMA: dict[str, pl.PolarsDataType] = {
    "game_pk": pl.Int64, "game_date": pl.Utf8, "pitcher": pl.Int64, "batter": pl.Int64,
    "home_team": pl.Utf8, "away_team": pl.Utf8, "inning": pl.Int64, "inning_topbot": pl.Utf8,
    "at_bat_number": pl.Int64, "pitch_number": pl.Int64, "pitch_type": pl.Utf8,
    "release_speed": pl.Float64, "release_spin_rate": pl.Float64, "description": pl.Utf8,
    "events": pl.Utf8, "zone": pl.Int64, "p_throws": pl.Utf8, "stand": pl.Utf8,
    "pitcher_days_since_prev_game": pl.Int64, "n_thruorder_pitcher": pl.Int64,
    "bat_score": pl.Int64, "post_bat_score": pl.Int64,
    "pitching_team": pl.Utf8, "game_date_str": pl.Utf8,
}


def normalize_statcast_pitches(pitches: pl.DataFrame) -> pl.DataFrame:
    """Select/type the columns feature-building actually needs, and derive
    the pitching team per row (Statcast doesn't label it directly — the
    pitching team is whichever team isn't batting: away bats in the top of
    the inning, home bats in the bottom)."""
    if pitches.is_empty():
        # Real bug fixed here (found via build_mlb_historical_horizon_dataset()
        # exercising a genuinely real case for the first time: a date range
        # with real completed games in the scoreboard but zero raw Statcast
        # collection for those dates -- e.g. a backfill gap). A bare
        # pl.DataFrame() has no columns at all; every downstream consumer
        # (bullpen_rolling_features, pitcher_rolling_features, identify_starters)
        # filters by real column names like "pitching_team" and raised
        # ColumnNotFoundError instead of honestly reporting zero prior
        # history. Returning a well-typed, zero-row frame with the real
        # expected schema lets every downstream .filter(pl.col(...)) resolve
        # to "no rows match" instead of "no such column".
        return pl.DataFrame(schema=_NORMALIZED_PITCH_SCHEMA)
    keep = [
        "game_pk", "game_date", "pitcher", "batter", "home_team", "away_team",
        "inning", "inning_topbot", "at_bat_number", "pitch_number",
        "pitch_type", "release_speed", "release_spin_rate", "description",
        "events", "zone", "p_throws", "stand",
        "pitcher_days_since_prev_game", "n_thruorder_pitcher",
        # bat_score/post_bat_score: the batting team's real score
        # immediately before/after this pitch. Added for
        # pitcher_clean_rate_features() -- runs allowed by the pitcher on
        # the mound is exactly the batting (opposing) team's score delta
        # while that pitcher's pitches are being thrown, real Statcast
        # data, not derived/estimated.
        "bat_score", "post_bat_score",
    ]
    present = [c for c in keep if c in pitches.columns]
    df = pitches.select(present)
    # A batch that happens to have every value null (e.g. a small test
    # fixture, or a real slate with no completed at-bats yet) infers as
    # polars' Null dtype, not Utf8 — .str.* ops then raise. Cast explicitly
    # so downstream filtering never depends on what a particular batch's
    # values happened to look like.
    df = df.with_columns(
        pl.when(pl.col("inning_topbot") == "Top")
        .then(pl.col("home_team"))
        .otherwise(pl.col("away_team"))
        .cast(pl.Utf8)
        .alias("pitching_team"),
        pl.col("game_date").str.slice(0, 10).alias("game_date_str"),
        pl.col("events").cast(pl.Utf8),
        pl.col("description").cast(pl.Utf8),
    )
    return df


def identify_starters(pitches: pl.DataFrame) -> pl.DataFrame:
    """Starter per (game_pk, pitching_team) = whoever threw that team's
    first pitch of the game — the actual rule MLB uses, not a heuristic."""
    if pitches.is_empty():
        return pl.DataFrame(schema={"game_pk": pl.Int64, "pitching_team": pl.Utf8, "pitcher": pl.Int64})
    return (
        pitches.sort(["game_pk", "pitching_team", "at_bat_number", "pitch_number"])
        .group_by(["game_pk", "pitching_team"], maintain_order=True)
        .agg(pl.col("pitcher").first(), pl.col("game_date_str").first())
    )


_NAN = float("nan")

# Task 5 (explicit missingness): a count/sample-size is genuinely 0 when
# zero real observations exist -- that's a true, non-ambiguous statement
# regardless of *why* the sample is empty. A rate, average, or single
# most-recent-observation value computed *from* zero real observations is
# mathematically undefined, not "a real value that happens to be zero" --
# CLAUDE.md's own "0 must not mean both actual measured zero and unknown."
# These module-level "no real history" defaults are the single source of
# truth both pitcher_rolling_features()/pitcher_clean_rate_features() and
# build_game_feature_row()'s "starter identity itself unknown" fallback
# (Task 1) share, so the two can never silently drift apart.
_NO_STARTER_ROLLING = {
    "availability": 0.0, "starts_seen": 0.0, "avg_velocity": _NAN,
    "k_pct": _NAN, "bb_pct": _NAN, "csw_pct": _NAN, "whiff_pct": _NAN,
    "days_rest": _NAN, "pitches_last_start": _NAN,
}
# Clean-rate fields are the one exception to the NaN rule above: their
# beta-binomial shrinkage estimator (missingness.pitcher_clean_rate_shrink)
# is already well-defined at zero real observations -- posterior_mean
# collapses to the pure league prior (alpha=beta=5 -> 0.5), a real,
# principled answer, not a fabricated measurement. pitcher_clean_rate_features()
# computes this identically via its own _no_history() helper; hardcoded
# here as the same deterministic constant (player_id/stat_name never
# affect beta_binomial_shrink()'s math, only its returned labels) so this
# module-level default never needs a live call to compute.
_CLEAN_RATE_PRIOR_MEAN = 5.0 / (5.0 + 5.0)
_NO_STARTER_CLEAN = {
    "availability": 0.0,
    "first_inning_clean_rate": _CLEAN_RATE_PRIOR_MEAN, "first_inning_clean_n": 0.0,
    "scoreless_inning_rate": _CLEAN_RATE_PRIOR_MEAN, "scoreless_inning_n": 0.0,
    "clean_appearance_rate": _CLEAN_RATE_PRIOR_MEAN, "clean_appearance_n": 0.0,
}


def pitcher_rolling_features(
    pitches: pl.DataFrame, pitcher_id: int, before_game_date: str, lookback_starts: int = 3,
) -> dict[str, float]:
    """Real, point-in-time-safe rolling features for one pitcher, computed
    only from that pitcher's own pitches in games strictly before
    before_game_date. Returns availability=0.0 with neutral defaults when
    there isn't enough real history yet — never silently substitutes a
    league-average guess as if it were observed data (the caller is
    responsible for treating availability=0.0 as missingness, not signal).

    Real fields (avg_velocity, k_pct, bb_pct, csw_pct, whiff_pct,
    days_rest, pitches_last_start) are NaN, not 0.0, when unavailable --
    0.0 is itself a plausible real value for several of these (a clean
    strikeout-free outing really can have k_pct=0.0), so a literal zero
    would be indistinguishable from real data. XGBoost and
    HistGradientBoostingRegressor both treat NaN as a native missing
    value; linear-model consumers must impute it explicitly (see
    RunDifferentialHead in models/__init__.py) paired with the real
    `availability` indicator, not silently receive an apparently-measured
    zero.
    """
    prior = pitches.filter(
        (pl.col("pitcher") == pitcher_id) & (pl.col("game_date_str") < before_game_date)
    )
    if prior.is_empty():
        return dict(_NO_STARTER_ROLLING)

    recent_game_dates = (
        prior.select("game_date_str").unique().sort("game_date_str", descending=True)
        .head(lookback_starts)["game_date_str"].to_list()
    )
    recent = prior.filter(pl.col("game_date_str").is_in(recent_game_dates))

    total_pitches = recent.height
    swings = recent.filter(pl.col("description").is_in(SWING_DESCRIPTIONS)).height
    whiffs = recent.filter(pl.col("description").is_in(WHIFF_DESCRIPTIONS)).height
    csw = recent.filter(pl.col("description").is_in(CSW_DESCRIPTIONS)).height

    pa_ending = recent.filter(pl.col("events").is_not_null())
    batters_faced = pa_ending.height
    strikeouts = pa_ending.filter(pl.col("events").str.contains("strikeout")).height
    walks = pa_ending.filter(pl.col("events").is_in(["walk", "intent_walk"])).height

    last_game_date = recent_game_dates[0] if recent_game_dates else None
    pitches_last_start = (
        recent.filter(pl.col("game_date_str") == last_game_date).height if last_game_date else None
    )
    days_rest_vals = recent.filter(
        (pl.col("game_date_str") == last_game_date) & pl.col("pitcher_days_since_prev_game").is_not_null()
    )["pitcher_days_since_prev_game"]
    days_rest = float(days_rest_vals[0]) if days_rest_vals.len() > 0 else _NAN

    avg_velocity_raw = recent["release_speed"].mean()

    return {
        "availability": 1.0,
        "starts_seen": float(len(recent_game_dates)),
        # Real, disclosed 0/0-vs-real-zero distinction (Task 5): a
        # denominator of 0 (no real swings/batters-faced/pitches in this
        # small a real sample) makes the rate mathematically undefined,
        # not a real observed zero -- `else 0.0` would have silently
        # claimed a measured 0% rate from zero real observations.
        "avg_velocity": float(avg_velocity_raw) if avg_velocity_raw is not None else _NAN,
        "k_pct": (strikeouts / batters_faced) if batters_faced else _NAN,
        "bb_pct": (walks / batters_faced) if batters_faced else _NAN,
        "csw_pct": (csw / total_pitches) if total_pitches else _NAN,
        "whiff_pct": (whiffs / swings) if swings else _NAN,
        "days_rest": days_rest,
        "pitches_last_start": float(pitches_last_start) if pitches_last_start is not None else _NAN,
    }


def pitcher_clean_rate_features(
    pitches: pl.DataFrame, pitcher_id: int, before_game_date: str,
) -> dict[str, float]:
    """Real beta-binomial-shrunk pitcher clean-rate features (CLAUDE.md Part
    1 SS10's "Pitcher clean-rate group"), computed strictly from this
    pitcher's own real prior starts before before_game_date --
    point-in-time-safe, same convention as pitcher_rolling_features().

    Computes three of CLAUDE.md's five named rates directly from real
    Statcast run-scoring data (bat_score/post_bat_score per pitch -- the
    batting team's score immediately before/after each pitch, so
    post_bat_score - bat_score is the real runs scored on that exact
    pitch/play; summing it over a pitcher's own pitches gives runs
    allowed while they were on the mound, without needing official
    earned/unearned attribution):

      - first_inning_clean_rate: fraction of real prior starts where this
        pitcher allowed 0 runs during inning 1.
      - scoreless_inning_rate: fraction of real (game, inning) pairs this
        pitcher pitched in where they allowed 0 runs.
      - clean_appearance_rate: fraction of real prior starts where this
        pitcher allowed 0 runs across the entire outing.

    Each is shrunk via missingness.pitcher_clean_rate_shrink() (real,
    tested, previously had zero callers anywhere in this codebase --
    verified via grep) rather than used as a raw, noisy small-sample
    rate.

    Real, disclosed scope: CLAUDE.md also names rolling_10/rolling_20
    variants of clean_appearance (fixed-window rates). With this
    project's real backfill window so far (~10 real days), few if any
    pitchers have 10+ real prior starts -- a separate rolling-10/20
    feature would be degenerate (identical to the all-history rate) for
    nearly every real pitcher right now, so it's deliberately not built
    as a distinct feature yet; needs more real backfill to be
    meaningfully different from what's here.

    Returns availability=0.0 with neutral defaults when there's no real
    prior start (or the source data predates bat_score/post_bat_score
    being collected) -- never substitutes a league-average guess."""
    from .missingness import pitcher_clean_rate_shrink

    # Task 5 fix: the beta-binomial shrinkage estimator is already
    # well-defined at zero real observations (posterior_mean collapses to
    # the league prior, e.g. 0.5 at alpha=beta=5) -- a real, principled
    # answer, not a fabricated measurement. The two early-return branches
    # below previously hardcoded a literal 0.0 instead, which both
    # bypassed the function's own shrinkage design AND (per CLAUDE.md's
    # "0 must not mean both real measured zero and unknown") looked
    # indistinguishable from a genuinely observed 0% clean rate. Routing
    # zero real observations through the identical shrink() call every
    # other case already uses keeps the estimate honest and consistent.
    def _no_history() -> dict[str, float]:
        zero = pitcher_clean_rate_shrink(str(pitcher_id), "clean_appearance", 0.0, 0.0)
        return {
            "availability": 0.0,
            "first_inning_clean_rate": zero.posterior_mean, "first_inning_clean_n": 0.0,
            "scoreless_inning_rate": zero.posterior_mean, "scoreless_inning_n": 0.0,
            "clean_appearance_rate": zero.posterior_mean, "clean_appearance_n": 0.0,
        }

    prior = pitches.filter(
        (pl.col("pitcher") == pitcher_id) & (pl.col("game_date_str") < before_game_date)
    )
    if prior.is_empty() or "bat_score" not in prior.columns or "post_bat_score" not in prior.columns:
        return _no_history()

    prior = prior.filter(
        pl.col("bat_score").is_not_null() & pl.col("post_bat_score").is_not_null()
    ).with_columns(
        (pl.col("post_bat_score") - pl.col("bat_score")).alias("runs_this_pitch")
    )
    if prior.is_empty():
        return _no_history()

    # Clean appearance: per real start (game_pk), total runs allowed.
    per_start = prior.group_by("game_pk").agg(pl.col("runs_this_pitch").sum().alias("runs_allowed"))
    clean_starts = float(per_start.filter(pl.col("runs_allowed") == 0).height)
    total_starts = float(per_start.height)

    # First-inning clean: per real start, runs allowed during inning 1.
    first_inning = prior.filter(pl.col("inning") == 1)
    if first_inning.is_empty():
        first_inning_clean, first_inning_n = 0.0, 0.0
    else:
        per_start_inning1 = first_inning.group_by("game_pk").agg(
            pl.col("runs_this_pitch").sum().alias("runs_allowed")
        )
        first_inning_clean = float(per_start_inning1.filter(pl.col("runs_allowed") == 0).height)
        first_inning_n = float(per_start_inning1.height)

    # Scoreless inning: per real (game, inning) pair this pitcher threw in.
    per_game_inning = prior.group_by(["game_pk", "inning"]).agg(
        pl.col("runs_this_pitch").sum().alias("runs_allowed")
    )
    scoreless_innings = float(per_game_inning.filter(pl.col("runs_allowed") == 0).height)
    total_innings = float(per_game_inning.height)

    first_inning_shrunk = pitcher_clean_rate_shrink(
        str(pitcher_id), "first_inning_clean", first_inning_clean, first_inning_n,
    )
    scoreless_shrunk = pitcher_clean_rate_shrink(
        str(pitcher_id), "scoreless_inning", scoreless_innings, total_innings,
    )
    clean_appearance_shrunk = pitcher_clean_rate_shrink(
        str(pitcher_id), "clean_appearance", clean_starts, total_starts,
    )

    return {
        "availability": 1.0,
        "first_inning_clean_rate": first_inning_shrunk.posterior_mean,
        "first_inning_clean_n": first_inning_n,
        "scoreless_inning_rate": scoreless_shrunk.posterior_mean,
        "scoreless_inning_n": total_innings,
        "clean_appearance_rate": clean_appearance_shrunk.posterior_mean,
        "clean_appearance_n": total_starts,
    }


def bullpen_rolling_features(
    pitches: pl.DataFrame, team: str, before_game_date: str, starters: pl.DataFrame, lookback_days: int = 3,
) -> dict[str, float]:
    """Team-level relief-pitching workload/quality over the prior N calendar
    days, excluding each game's own starter (identified via identify_starters,
    the same real first-pitch rule, not a name heuristic)."""
    from datetime import date, timedelta

    # Task 5: bullpen_pitches/bullpen_appearances are real counts -- 0.0 is
    # a true, unambiguous statement even when the whole window has zero
    # real coverage. bullpen_avg_velocity is a measured average and is
    # mathematically undefined at zero real pitches, so it's NaN rather
    # than an apparently-real 0 mph (see pitcher_rolling_features' own
    # avg_velocity for the identical reasoning).
    no_bullpen = {"availability": 0.0, "bullpen_pitches": 0.0, "bullpen_avg_velocity": _NAN, "bullpen_appearances": 0.0}

    cutoff = (date.fromisoformat(before_game_date) - timedelta(days=lookback_days)).isoformat()
    team_pitches = pitches.filter(
        (pl.col("pitching_team") == team)
        & (pl.col("game_date_str") >= cutoff)
        & (pl.col("game_date_str") < before_game_date)
    )
    if team_pitches.is_empty():
        return dict(no_bullpen)

    team_starters = set(
        starters.filter(pl.col("pitching_team") == team)["pitcher"].to_list()
    )
    relief = team_pitches.filter(~pl.col("pitcher").is_in(list(team_starters)) if team_starters else pl.lit(True))
    if relief.is_empty():
        return dict(no_bullpen)

    appearances = relief.select(["game_pk", "pitcher"]).unique().height
    avg_velocity_raw = relief["release_speed"].mean()
    return {
        "availability": 1.0,
        "bullpen_pitches": float(relief.height),
        "bullpen_avg_velocity": float(avg_velocity_raw) if avg_velocity_raw is not None else _NAN,
        "bullpen_appearances": float(appearances),
    }


def park_factor(venue: str) -> float:
    return float(MLB_PARK_FACTORS.get(venue, DEFAULT_PARK_FACTOR))


def lookup_pitcher_id(full_name: str) -> int | None:
    """Real name -> Statcast pitcher ID (MLBAM) crosswalk via pybaseball's
    own player register. Needed because probable-starter feeds (e.g. ESPN's
    scoreboard probables) identify pitchers by name, while Statcast pitch
    data — the only source real rolling features can come from — identifies
    them only by numeric ID. Without this, a scheduled game (which has no
    Statcast pitches of its own yet, since it hasn't been played) cannot be
    connected to its starter's rolling history at all.

    pybaseball caches its full player-ID table on first call (~8s); repeat
    calls within a process are ~1000x faster. Returns None on no match, or
    on a genuine ambiguity that recency can't resolve, rather than guessing.

    Real, verified ambiguity case: "Drew Anderson" matches two real
    players — one who last played in 2006 (key_mlbam 449776) and one
    currently active through 2026 (key_mlbam 623454). A probable starter
    for a real upcoming game must be the currently active one, so ties are
    broken by `mlb_played_last` — not a guess, a real recency fact already
    in the same lookup result.
    """
    import pybaseball

    parts = full_name.strip().split(" ")
    if len(parts) < 2:
        return None
    first, last = parts[0], " ".join(parts[1:])
    try:
        result = pybaseball.playerid_lookup(last, first)
    except Exception:  # noqa: BLE001 -- external lookup/network; treated as no-match, not fatal
        return None
    if result.empty:
        return None
    result = result.dropna(subset=["key_mlbam"])
    if result.empty:
        return None
    if len(result) > 1:
        result = result[result["mlb_played_last"] == result["mlb_played_last"].max()]
    if len(result) != 1:
        return None
    return int(result["key_mlbam"].iloc[0])


def point_in_time_probable_starters(
    decision_times: dict[str, datetime], probable_records: list[dict],
) -> dict[str, dict[str, str]]:
    """The real, point-in-time-correct probable starters for each game in
    `decision_times` ({event_id: decision_time_utc}), selected from
    `probable_records` (each a dict with real event_id/observed_at_utc/
    home_starter/away_starter fields, e.g. loaded from
    data/rebuild/raw/mlb/probable_starters.jsonl).

    Real gap fixed here (FOUNDATION_COMPLETION.md Phase 3): a naive
    `{rec["event_id"]: rec for rec in records}` keeps whichever record
    happens to be *last in the file* for an event, not necessarily the
    newest observation strictly before that game's real decision_time_utc —
    152 of 163 real events in the incumbent probables file have more than
    one record (confirmed live), so a revision observed after the "late"
    horizon's T-60m cutoff could otherwise silently leak into a decision
    that shouldn't have seen it yet. Uses the shared point_in_time_join()
    utility (asof.py) — fixed and tested per Phase 3 but dead code with no
    real caller in this repo until this.

    Returns {event_id: {"home_starter": ..., "away_starter": ...}} only for
    events with a real, valid prior observation — an event with no
    qualifying observation is simply absent, not filled with a guess.
    """
    if not decision_times or not probable_records:
        return {}
    records = [
        {
            "event_id": rec["event_id"],
            "observed_at_utc": datetime.fromisoformat(rec["observed_at_utc"]),
            "home_starter": rec["home_starter"],
            "away_starter": rec["away_starter"],
        }
        for rec in probable_records
        if rec["event_id"] in decision_times
    ]
    if not records:
        return {}
    observations = pl.DataFrame(records)
    decisions = pl.DataFrame({
        "event_id": list(decision_times.keys()),
        "decision_time_utc": list(decision_times.values()),
    })
    joined = point_in_time_join(decisions, observations, entity_keys=["event_id"])
    result: dict[str, dict[str, str]] = {}
    for row in joined.iter_rows(named=True):
        if row.get("obs_home_starter") is not None and row.get("obs_away_starter") is not None:
            result[row["event_id"]] = {
                "home_starter": row["obs_home_starter"],
                "away_starter": row["obs_away_starter"],
            }
    return result


# Task 5: every field here is a real measured/observed value (or the age
# of one) -- there is no real count/sample-size field to keep as a
# meaningful 0. 0.0 previously stood in for "no forecast" in every one of
# these, indistinguishable from a real, plausible 0 (0mph wind, 0mm rain,
# and -- worst of all -- forecast_age_hours=0.0 looked exactly like "just
# observed," the opposite of "never observed").
_NO_WEATHER = {
    "availability": 0.0, "temp_f_first_pitch": _NAN, "wind_mph_first_pitch": _NAN,
    "wind_direction_deg_first_pitch": _NAN, "precip_mm_first_pitch": _NAN,
    "forecast_age_hours": _NAN,
}


def load_weather_at_decision_time(
    raw_root: str | Path,
    venue_id: str,
    game_date: str,
    decision_time_utc: datetime,
    event_start_utc: str,
) -> dict[str, float]:
    """Real, point-in-time-safe weather feature for one game.

    Fix (see outputs/rebuild/takeover_status.md, Task 3): the previous
    version took whichever snapshot sorted last on disk for the date
    regardless of when it was actually collected, and averaged the
    entire day's hourly values into one number -- diluting the real
    pregame signal with hours that have nothing to do with the game and
    with no point-in-time guarantee at all (a snapshot collected *after*
    a late decision time could silently leak into it).

    Fixed by: (1) only considering real snapshots written with the
    provenance envelope collect_weather_forecast() now embeds
    ({"observed_at_utc", "endpoint", "forecast_data"}) -- a legacy raw
    snapshot from before this fix has no real recorded observed_at_utc to
    check at all, and this must not guess one (e.g. from file mtime) or
    silently treat "the only snapshot that happens to exist" as
    point-in-time-valid; (2) selecting the newest such snapshot with
    observed_at_utc <= decision_time_utc, never a later one; (3) reading
    the one real hourly entry closest to the game's actual first-pitch
    time (both now genuinely UTC-labeled -- see collect_weather_forecast's
    own timezone fix -- so no venue-timezone lookup is needed to compare
    them), not a full-day aggregate.

    Returns availability=0.0 with every value zeroed, never a postgame
    actual or a guess, when no snapshot satisfies both conditions. Also
    reports forecast_age_hours (decision_time_utc minus the selected
    snapshot's real observed_at_utc) so a very stale-but-technically-valid
    forecast is honestly distinguishable from a fresh one downstream.

    For a historical (backfilled) game_date, the underlying Open-Meteo
    Historical Forecast API itself is a disclosed, real approximation --
    Open-Meteo documents it as a stitched series of past model runs, not
    a single exact historical run -- so even a perfectly point-in-time-safe
    *selection* here cannot make that number a literal "forecast run
    exactly as it existed at decision_time_utc." That is an external data
    source limitation, not something this function's selection logic can
    fix; disclosed here rather than silently treated as exact.
    """
    raw_root = Path(raw_root)
    record_dir = raw_root / "raw" / "open_meteo" / game_date / f"weather_{venue_id}_{game_date}"
    if not record_dir.exists():
        return dict(_NO_WEATHER)
    snapshots = sorted(record_dir.glob("*.json.gz"))
    if not snapshots:
        return dict(_NO_WEATHER)

    valid: list[tuple[datetime, dict]] = []
    for snap_path in snapshots:
        with gzip.open(snap_path) as f:
            payload = json.loads(f.read())
        if not isinstance(payload, dict) or "observed_at_utc" not in payload or "forecast_data" not in payload:
            continue  # legacy/unenveloped snapshot -- real observed_at_utc unknown, not PIT-usable
        try:
            observed_at = datetime.fromisoformat(payload["observed_at_utc"])
        except (TypeError, ValueError):
            continue
        if observed_at <= decision_time_utc:
            valid.append((observed_at, payload))

    if not valid:
        return dict(_NO_WEATHER)
    observed_at, payload = max(valid, key=lambda pair: pair[0])

    hourly = payload["forecast_data"].get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return dict(_NO_WEATHER)

    event_start = datetime.fromisoformat(event_start_utc)
    if event_start.tzinfo is None:
        event_start = event_start.replace(tzinfo=UTC)

    def _hourly_time(raw: str) -> datetime:
        # Real bug caught by live verification against the actual
        # Open-Meteo API (not just synthetic fixtures): requesting
        # timezone=UTC (collect_weather_forecast's own fix) makes
        # hourly.time genuinely UTC, but Open-Meteo still returns those
        # timestamps *naive* (e.g. "2026-08-08T00:00", no "+00:00"/"Z"
        # suffix) -- comparing that directly against an aware
        # event_start_utc raised TypeError every time. Attach UTC
        # explicitly rather than assume the caller's data is naive too.
        parsed = datetime.fromisoformat(raw)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed

    idx = min(
        range(len(times)),
        key=lambda i: abs((_hourly_time(times[i]) - event_start).total_seconds()),
    )

    def _hour(series_name: str) -> float | None:
        series = hourly.get(series_name, [])
        value = series[idx] if idx < len(series) else None
        return float(value) if value is not None else None

    temp_c = _hour("temperature_2m")
    wind_kmh = _hour("wind_speed_10m")
    wind_dir = _hour("wind_direction_10m")
    precip_mm = _hour("precipitation")

    return {
        "availability": 1.0,
        "temp_f_first_pitch": (temp_c * 9 / 5 + 32) if temp_c is not None else 0.0,
        "wind_mph_first_pitch": (wind_kmh * 0.621371) if wind_kmh is not None else 0.0,
        "wind_direction_deg_first_pitch": wind_dir if wind_dir is not None else 0.0,
        "precip_mm_first_pitch": precip_mm if precip_mm is not None else 0.0,
        "forecast_age_hours": (decision_time_utc - observed_at).total_seconds() / 3600.0,
    }


def resolve_statcast_game_pk(
    espn_game: dict,
    statsapi_games: list[dict],
    *,
    max_start_time_diff_minutes: float = 180.0,
) -> int | None:
    """Doubleheader-safe replacement for the previous (date, home, away)
    -> first-Statcast-game_pk join (real bug: on a real doubleheader that
    silently picked whichever of the two real games happened to sort
    first by game_pk, with no guarantee that was the actual game being
    featurized). Statcast's own game_pk *is* MLB StatsAPI's real gamePk
    (Baseball Savant sources it directly from MLB's own numbering,
    verified live against a real MLB StatsAPI schedule response) -- so
    this matches ESPN's scheduled event to the real StatsAPI schedule
    game sharing both real team names on the same calendar date, breaking
    ties by the closest real scheduled start timestamp. A doubleheader's
    two real games have real start times hours apart (verified live: a
    real 2026-07-28 CIN/CLE doubleheader was 17:40Z and 23:10Z), so this
    correctly disambiguates them without needing any shared native ID
    between ESPN and Statcast at all -- and without needing the
    doubleHeader/gameNumber fields StatsAPI happens to also carry, which
    keeps this robust to a source that omits them.

    `statsapi_games` should be the real payload from
    MLBStatsAPIClient.schedule() (data_sources/mlb_statsapi.py, the
    existing incumbent StatsAPI adapter, reused here rather than
    reimplemented) for the relevant date range -- each item a real
    schedule game dict with gamePk/gameDate/teams.home.team.name/
    teams.away.team.name.

    Fails closed (returns None), never guesses, when: no candidate shares
    both real team names on the event's calendar date; more than one
    candidate is genuinely tied for closest start time (real, if rare --
    must not be silently broken by list order); or the single best
    candidate's start time differs from ESPN's own by more than
    max_start_time_diff_minutes (a real data problem, e.g. an
    inconsistently-reported postponement/reschedule between sources, not
    a doubleheader case at all -- three real hours is comfortably wider
    than any real doubleheader gap but still catches a genuinely wrong
    date/source mismatch)."""
    espn_start = datetime.fromisoformat(espn_game["event_start_utc"])
    home_name, away_name = espn_game["home_team"], espn_game["away_team"]
    game_date = espn_game["event_start_utc"][:10]

    candidates: list[tuple[float, int]] = []
    for g in statsapi_games:
        game_start_raw = g.get("gameDate")
        game_pk = g.get("gamePk")
        if not game_start_raw or not game_pk:
            continue
        game_start = datetime.fromisoformat(str(game_start_raw))
        if game_start.strftime("%Y-%m-%d") != game_date:
            continue
        teams = g.get("teams") or {}
        g_home = ((teams.get("home") or {}).get("team") or {}).get("name")
        g_away = ((teams.get("away") or {}).get("team") or {}).get("name")
        if g_home != home_name or g_away != away_name:
            continue
        diff_minutes = abs((game_start - espn_start).total_seconds()) / 60.0
        candidates.append((diff_minutes, int(game_pk)))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    best_diff, best_game_pk = candidates[0]
    if len(candidates) > 1 and candidates[1][0] == best_diff:
        return None  # genuine tie -- fail closed, never guess by list order
    if best_diff > max_start_time_diff_minutes:
        return None
    return best_game_pk


def resolve_horizon_starter_names(
    espn_game: dict, horizon: str, probable_records: list[dict],
) -> tuple[str | None, str | None, str | None]:
    """Real, point-in-time-safe probable starter names for one game at the
    given horizon's decision time: (home_starter_name, away_starter_name,
    missing_reason). missing_reason is None only when both names were
    resolved from a genuinely prospective observation
    (pit_eligible=True/provenance="prospective_pregame") strictly before
    decision_time = event_start_utc - HORIZON_HOURS_BEFORE[horizon].

    Records with pit_eligible=False (provenance
    "retroactive_or_unverifiable_non_pit" -- collected after the fact, so
    observed_at_utc doesn't reflect a real pregame observation) are never
    used here, even if their timestamp would otherwise pass the point-in-time
    filter -- using one could silently leak the actual/final starter
    disguised as a "probable" one, exactly the train-serving leak this
    function exists to close (see CLAUDE.md's historical starter-parity
    requirement). When no valid observation exists,
    missing_reason="no_valid_probable_at_horizon" and both names are None --
    callers must not fall back to the game's actual completed-game starter.
    """
    if horizon not in HORIZON_HOURS_BEFORE:
        raise ValueError(f"horizon must be one of {tuple(HORIZON_HOURS_BEFORE)}, got {horizon!r}")
    decision_time = datetime.fromisoformat(espn_game["event_start_utc"]) - timedelta(
        hours=HORIZON_HOURS_BEFORE[horizon]
    )
    pit_records = [r for r in probable_records if r.get("pit_eligible") is True]
    resolved = point_in_time_probable_starters({espn_game["event_id"]: decision_time}, pit_records)
    names = resolved.get(espn_game["event_id"])
    if names is None:
        return None, None, "no_valid_probable_at_horizon"
    return names["home_starter"], names["away_starter"], None


# Task 5 (explicit missingness): the one shared, canonical feature-list
# definition for MLBTwoHeadModel's two heads -- previously each of
# train_mlb_rebuild_real_features.py, train_mlb_xgboost_ensemble.py, and
# mlb_shadow_pipeline.py independently hardcoded its own copy of these
# two lists, with no guarantee they'd stay identical (exactly the kind of
# drift Task 4 eliminated for the dataset-building loop itself, just one
# layer up, for *which* columns of that dataset each model consumes).
# mlb_shadow_pipeline.py's copy matters most: it's the one that actually
# retrains the live-serving artifact, so a silent difference there would
# be a real train-serving mismatch, not just a research-script
# inconsistency.
#
# Includes the real `*_availability` indicators paired with every
# feature that can now be NaN (see pitcher_rolling_features(),
# bullpen_rolling_features(), load_weather_at_decision_time() above) --
# CLAUDE.md's "imputed value + missingness indicator must be paired"
# requirement for RunDifferentialHead's ElasticNet (models/__init__.py),
# and useful signal even for RunIntensityHead's HistGradientBoostingRegressor,
# which handles the NaN natively but still benefits from an explicit flag.
MLB_INTENSITY_FEATURES = [
    "home_sp_avg_velocity", "away_sp_avg_velocity",
    "home_sp_csw_pct", "away_sp_csw_pct",
    "home_bp_bullpen_pitches", "away_bp_bullpen_pitches",
    "park_factor", "temp_f_first_pitch",
    "home_sp_availability", "away_sp_availability",
    "home_bp_availability", "away_bp_availability",
    "weather_availability",
]
MLB_DIFFERENTIAL_FEATURES = [
    "home_sp_k_pct", "away_sp_k_pct",
    "home_sp_bb_pct", "away_sp_bb_pct",
    "home_sp_days_rest", "away_sp_days_rest",
    "home_bp_bullpen_avg_velocity", "away_bp_bullpen_avg_velocity",
    "home_sp_availability", "away_sp_availability",
    "home_bp_availability", "away_bp_availability",
]


def build_game_feature_row(
    espn_game: dict,
    pitches: pl.DataFrame,
    starters: pl.DataFrame,
    raw_root: str | Path,
    horizon: str,
    probable_records: list[dict],
) -> dict[str, float] | None:
    """Real feature row for one completed ESPN-scoreboard game: starter and
    bullpen rolling features for both teams, park factor, and weather.

    Train-serving parity fix (see outputs/rebuild/takeover_status.md):
    starters are resolved the same way live inference resolves them --
    from the point-in-time-valid probable-starter observation at this
    horizon's decision time (resolve_horizon_starter_names(), the same
    point_in_time_probable_starters() lookup build_live_game_feature_row()
    already used) -- never from identify_starters() on this game's own
    completed Statcast pitches, which is the actual pitcher and can differ
    from what was knowable at the decision horizon (a late starter swap).
    When no point-in-time-valid probable exists (or the resolved name can't
    be matched to a real Statcast pitcher ID), starter features are
    returned zeroed and explicitly flagged
    (`starters_known`/`starter_missing_reason`) rather than silently
    substituted with the actual starter -- missingness is data, not a gap
    to paper over.

    All rolling-feature computation remains point-in-time-safe on its own
    terms (strictly before this game's date); this fix is specifically
    about *which pitcher* those rolling features are computed for.

    Returns None only when the ESPN team names can't be mapped to a real
    Statcast club abbreviation (see ESPN_TO_STATCAST_ABBREV) -- every other
    case, including a fully unknown starter, still produces a row.
    """
    game_date = espn_game["event_start_utc"][:10]
    home_name, away_name = espn_game["home_team"], espn_game["away_team"]
    home_abbrev = ESPN_TO_STATCAST_ABBREV.get(home_name)
    away_abbrev = ESPN_TO_STATCAST_ABBREV.get(away_name)
    if home_abbrev is None or away_abbrev is None:
        return None

    home_starter_name, away_starter_name, missing_reason = resolve_horizon_starter_names(
        espn_game, horizon, probable_records,
    )

    home_starter_id = lookup_pitcher_id(home_starter_name) if home_starter_name else None
    away_starter_id = lookup_pitcher_id(away_starter_name) if away_starter_name else None
    if missing_reason is None and (home_starter_id is None or away_starter_id is None):
        missing_reason = "starter_name_not_resolved_to_statcast_id"

    starter_known = missing_reason is None
    if starter_known:
        home_p = pitcher_rolling_features(pitches, home_starter_id, game_date)  # type: ignore[arg-type]
        away_p = pitcher_rolling_features(pitches, away_starter_id, game_date)  # type: ignore[arg-type]
        # Distinct "home_sp_clean_" prefix (not "home_sp_") -- both dicts
        # have their own "availability" key, so sharing home_p's prefix
        # would silently let one clobber the other in the merged row below.
        home_clean = pitcher_clean_rate_features(pitches, home_starter_id, game_date)  # type: ignore[arg-type]
        away_clean = pitcher_clean_rate_features(pitches, away_starter_id, game_date)  # type: ignore[arg-type]
    else:
        home_p = away_p = dict(_NO_STARTER_ROLLING)
        home_clean = away_clean = dict(_NO_STARTER_CLEAN)

    home_bp = bullpen_rolling_features(pitches, home_abbrev, game_date, starters)
    away_bp = bullpen_rolling_features(pitches, away_abbrev, game_date, starters)
    park = park_factor(espn_game.get("venue", ""))
    decision_time_utc = datetime.fromisoformat(espn_game["event_start_utc"]) - timedelta(
        hours=HORIZON_HOURS_BEFORE[horizon]
    )
    weather = load_weather_at_decision_time(
        raw_root, espn_game.get("venue", ""), game_date, decision_time_utc, espn_game["event_start_utc"],
    )

    # Targets attached last, after every feature above is frozen from
    # point-in-time-safe inputs only -- the actual result never influences
    # what features get computed.
    home_score, away_score = espn_game["home_score"], espn_game["away_score"]
    return {
        "event_id": espn_game["event_id"],
        "game_date": game_date,
        "event_start_utc": espn_game["event_start_utc"],
        "horizon": horizon,
        "home_team": home_name, "away_team": away_name,
        "starters_known": 1.0 if starter_known else 0.0,
        "starter_missing_reason": missing_reason or "",
        # Starter features (prefixed by side)
        **{f"home_sp_{k}": v for k, v in home_p.items()},
        **{f"away_sp_{k}": v for k, v in away_p.items()},
        **{f"home_sp_clean_{k}": v for k, v in home_clean.items()},
        **{f"away_sp_clean_{k}": v for k, v in away_clean.items()},
        # Bullpen features
        **{f"home_bp_{k}": v for k, v in home_bp.items()},
        **{f"away_bp_{k}": v for k, v in away_bp.items()},
        # Park and weather (shared, not per-side)
        "park_factor": park,
        "weather_availability": weather["availability"],
        "temp_f_first_pitch": weather["temp_f_first_pitch"],
        "wind_mph_first_pitch": weather["wind_mph_first_pitch"],
        "wind_direction_deg_first_pitch": weather["wind_direction_deg_first_pitch"],
        "precip_mm_first_pitch": weather["precip_mm_first_pitch"],
        "weather_forecast_age_hours": weather["forecast_age_hours"],
        "total_runs": float(home_score + away_score),
        "home_margin": float(home_score - away_score),
        "home_score": float(home_score), "away_score": float(away_score),
    }


def build_live_game_feature_row(
    espn_game: dict,
    home_starter_name: str,
    away_starter_name: str,
    pitches: pl.DataFrame,
    starters: pl.DataFrame,
    raw_root: str | Path,
    identity_registry: Any | None = None,
    decision_time_utc: datetime | None = None,
) -> dict | None:
    """Feature row for a real *scheduled* (not yet played) game, using
    probable-starter names (e.g. from ESPN's scoreboard probables feed)
    instead of build_game_feature_row's Statcast-game_pk matching — a
    scheduled game has no Statcast pitches of its own yet, since it hasn't
    been played, so there is no game_pk to match against. Starter rolling
    features come from lookup_pitcher_id() + the starter's real prior
    starts; bullpen/park don't depend on this game having already
    happened, so they're identical to build_game_feature_row's.

    Returns None when either starter name can't be resolved to a real
    Statcast ID, rather than silently guessing or falling back to a
    league-average-looking feature row.

    identity_registry is optional (canonical player identity is additive
    lineage, not required for the feature row itself to be correct) --
    when given a real IdentityRegistry, both starters' real MLBAM ids are
    registered/resolved as canonical player entities via
    identity.resolve_mlbam_player_id().

    decision_time_utc should be the real decision time the caller already
    computed for this horizon (compute_decision_times()/state.decision_times)
    -- used for point-in-time-safe weather-snapshot selection
    (load_weather_at_decision_time()). Defaults to this game's own
    event_start_utc (i.e. "as of right now, at first pitch") when not
    given, matching this function's existing live-inference callers that
    predate a threaded-through decision time; a caller with a real
    horizon-specific decision time should always pass it explicitly.
    """
    if decision_time_utc is None:
        decision_time_utc = datetime.fromisoformat(espn_game["event_start_utc"])
    game_date = espn_game["event_start_utc"][:10]
    home_name, away_name = espn_game["home_team"], espn_game["away_team"]
    home_abbrev = ESPN_TO_STATCAST_ABBREV.get(home_name)
    away_abbrev = ESPN_TO_STATCAST_ABBREV.get(away_name)
    if home_abbrev is None or away_abbrev is None:
        return None

    home_starter_id = lookup_pitcher_id(home_starter_name)
    away_starter_id = lookup_pitcher_id(away_starter_name)
    if home_starter_id is None or away_starter_id is None:
        return None

    if identity_registry is not None:
        from .identity import resolve_mlbam_player_id

        observed_at = utc_now().isoformat()
        resolve_mlbam_player_id(identity_registry, "mlb", home_starter_name, home_starter_id, observed_at)
        resolve_mlbam_player_id(identity_registry, "mlb", away_starter_name, away_starter_id, observed_at)

    home_p = pitcher_rolling_features(pitches, home_starter_id, game_date)
    away_p = pitcher_rolling_features(pitches, away_starter_id, game_date)
    # Distinct "home_sp_clean_" prefix (not "home_sp_") -- both dicts
    # have their own "availability" key, so sharing home_p's prefix would
    # silently let one clobber the other in the merged row below.
    home_clean = pitcher_clean_rate_features(pitches, home_starter_id, game_date)
    away_clean = pitcher_clean_rate_features(pitches, away_starter_id, game_date)
    home_bp = bullpen_rolling_features(pitches, home_abbrev, game_date, starters)
    away_bp = bullpen_rolling_features(pitches, away_abbrev, game_date, starters)
    park = park_factor(espn_game.get("venue", ""))
    weather = load_weather_at_decision_time(
        raw_root, espn_game.get("venue", ""), game_date, decision_time_utc, espn_game["event_start_utc"],
    )

    return {
        "event_id": espn_game["event_id"],
        "game_date": game_date,
        "event_start_utc": espn_game["event_start_utc"],
        "home_team": home_name, "away_team": away_name,
        "home_starter_name": home_starter_name, "away_starter_name": away_starter_name,
        **{f"home_sp_{k}": v for k, v in home_p.items()},
        **{f"away_sp_{k}": v for k, v in away_p.items()},
        **{f"home_sp_clean_{k}": v for k, v in home_clean.items()},
        **{f"away_sp_clean_{k}": v for k, v in away_clean.items()},
        **{f"home_bp_{k}": v for k, v in home_bp.items()},
        **{f"away_bp_{k}": v for k, v in away_bp.items()},
        "park_factor": park,
        "weather_availability": weather["availability"],
        "temp_f_first_pitch": weather["temp_f_first_pitch"],
        "wind_mph_first_pitch": weather["wind_mph_first_pitch"],
        "wind_direction_deg_first_pitch": weather["wind_direction_deg_first_pitch"],
        "precip_mm_first_pitch": weather["precip_mm_first_pitch"],
        "weather_forecast_age_hours": weather["forecast_age_hours"],
    }
