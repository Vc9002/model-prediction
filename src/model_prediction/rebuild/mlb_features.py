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
from pathlib import Path

import polars as pl

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

SWING_DESCRIPTIONS = {
    "foul", "foul_tip", "hit_into_play", "swinging_strike",
    "swinging_strike_blocked", "missed_bunt", "foul_bunt",
}
WHIFF_DESCRIPTIONS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
CSW_DESCRIPTIONS = WHIFF_DESCRIPTIONS | {"called_strike"}


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


def normalize_statcast_pitches(pitches: pl.DataFrame) -> pl.DataFrame:
    """Select/type the columns feature-building actually needs, and derive
    the pitching team per row (Statcast doesn't label it directly — the
    pitching team is whichever team isn't batting: away bats in the top of
    the inning, home bats in the bottom)."""
    if pitches.is_empty():
        return pitches
    keep = [
        "game_pk", "game_date", "pitcher", "batter", "home_team", "away_team",
        "inning", "inning_topbot", "at_bat_number", "pitch_number",
        "pitch_type", "release_speed", "release_spin_rate", "description",
        "events", "zone", "p_throws", "stand",
        "pitcher_days_since_prev_game", "n_thruorder_pitcher",
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


def pitcher_rolling_features(
    pitches: pl.DataFrame, pitcher_id: int, before_game_date: str, lookback_starts: int = 3,
) -> dict[str, float]:
    """Real, point-in-time-safe rolling features for one pitcher, computed
    only from that pitcher's own pitches in games strictly before
    before_game_date. Returns availability=0.0 with neutral defaults when
    there isn't enough real history yet — never silently substitutes a
    league-average guess as if it were observed data (the caller is
    responsible for treating availability=0.0 as missingness, not signal).
    """
    prior = pitches.filter(
        (pl.col("pitcher") == pitcher_id) & (pl.col("game_date_str") < before_game_date)
    )
    if prior.is_empty():
        return {
            "availability": 0.0, "starts_seen": 0.0, "avg_velocity": 0.0,
            "k_pct": 0.0, "bb_pct": 0.0, "csw_pct": 0.0, "whiff_pct": 0.0,
            "days_rest": 0.0, "pitches_last_start": 0.0,
        }

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
        recent.filter(pl.col("game_date_str") == last_game_date).height if last_game_date else 0
    )
    days_rest_vals = recent.filter(
        (pl.col("game_date_str") == last_game_date) & pl.col("pitcher_days_since_prev_game").is_not_null()
    )["pitcher_days_since_prev_game"]
    days_rest = float(days_rest_vals[0]) if days_rest_vals.len() > 0 else 0.0

    return {
        "availability": 1.0,
        "starts_seen": float(len(recent_game_dates)),
        "avg_velocity": float(recent["release_speed"].mean() or 0.0),
        "k_pct": (strikeouts / batters_faced) if batters_faced else 0.0,
        "bb_pct": (walks / batters_faced) if batters_faced else 0.0,
        "csw_pct": (csw / total_pitches) if total_pitches else 0.0,
        "whiff_pct": (whiffs / swings) if swings else 0.0,
        "days_rest": days_rest,
        "pitches_last_start": float(pitches_last_start),
    }


def bullpen_rolling_features(
    pitches: pl.DataFrame, team: str, before_game_date: str, starters: pl.DataFrame, lookback_days: int = 3,
) -> dict[str, float]:
    """Team-level relief-pitching workload/quality over the prior N calendar
    days, excluding each game's own starter (identified via identify_starters,
    the same real first-pitch rule, not a name heuristic)."""
    from datetime import date, timedelta

    cutoff = (date.fromisoformat(before_game_date) - timedelta(days=lookback_days)).isoformat()
    team_pitches = pitches.filter(
        (pl.col("pitching_team") == team)
        & (pl.col("game_date_str") >= cutoff)
        & (pl.col("game_date_str") < before_game_date)
    )
    if team_pitches.is_empty():
        return {"availability": 0.0, "bullpen_pitches": 0.0, "bullpen_avg_velocity": 0.0, "bullpen_appearances": 0.0}

    team_starters = set(
        starters.filter(pl.col("pitching_team") == team)["pitcher"].to_list()
    )
    relief = team_pitches.filter(~pl.col("pitcher").is_in(list(team_starters)) if team_starters else pl.lit(True))
    if relief.is_empty():
        return {"availability": 0.0, "bullpen_pitches": 0.0, "bullpen_avg_velocity": 0.0, "bullpen_appearances": 0.0}

    appearances = relief.select(["game_pk", "pitcher"]).unique().height
    return {
        "availability": 1.0,
        "bullpen_pitches": float(relief.height),
        "bullpen_avg_velocity": float(relief["release_speed"].mean() or 0.0),
        "bullpen_appearances": float(appearances),
    }


def park_factor(venue: str) -> float:
    return float(MLB_PARK_FACTORS.get(venue, DEFAULT_PARK_FACTOR))


def load_weather_daily_aggregate(raw_root: str | Path, venue_id: str, game_date: str) -> dict[str, float]:
    """Coarse daily mean/max from the venue's Open-Meteo snapshot for the
    date — not aligned to the exact first-pitch hour yet (see
    outputs/rebuild/takeover_status.md: real, disclosed limitation, not a
    silent approximation)."""
    raw_root = Path(raw_root)
    record_dir = raw_root / "raw" / "open_meteo" / game_date / f"weather_{venue_id}_{game_date}"
    if not record_dir.exists():
        return {"availability": 0.0, "temp_f_mean": 0.0, "wind_mph_mean": 0.0, "precip_mm_total": 0.0}
    snapshots = sorted(record_dir.glob("*.json.gz"))
    if not snapshots:
        return {"availability": 0.0, "temp_f_mean": 0.0, "wind_mph_mean": 0.0, "precip_mm_total": 0.0}
    with gzip.open(snapshots[-1]) as f:
        payload = json.loads(f.read())
    hourly = payload.get("hourly", {})
    temp_c = hourly.get("temperature_2m", [])
    wind_kmh = hourly.get("wind_speed_10m", [])
    precip_mm = hourly.get("precipitation", [])
    if not temp_c:
        return {"availability": 0.0, "temp_f_mean": 0.0, "wind_mph_mean": 0.0, "precip_mm_total": 0.0}
    temp_f_mean = sum(t * 9 / 5 + 32 for t in temp_c) / len(temp_c)
    wind_mph_mean = (sum(wind_kmh) / len(wind_kmh)) * 0.621371 if wind_kmh else 0.0
    return {
        "availability": 1.0,
        "temp_f_mean": temp_f_mean,
        "wind_mph_mean": wind_mph_mean,
        "precip_mm_total": float(sum(precip_mm)) if precip_mm else 0.0,
    }
