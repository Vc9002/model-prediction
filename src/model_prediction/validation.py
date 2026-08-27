"""Chronological learned-model validation and feature ablation.

The model-development cohort fits coefficients, the later validation cohort
learns a confidence threshold, and the final cohort remains untouched until
one locked evaluation. Market prices never enter these independent models.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sklearn.linear_model import LogisticRegression

from .calibration import calibration_metrics
from .config import PROJECT_ROOT
from .data_sources.espn_probables import point_in_time_pitcher_era_gap
from .domain import EASTERN
from .features.base import FeatureStore, GameRecord
from .features.bullpen import FATIGUE_WINDOW_DAYS, bullpen_profile
from .features.elo_ratings import build_elo
from .features.park_factors import park_factor
from .features.park_factors_pit import park_factor_at
from .features.schedule_load import matchup_schedule_load
from .features.team_runs import pitcher_era_gap_from_history
from .features.trends import TrendEngine
from .lifecycle import evaluate_locked_holdout
from .models.learned_market import build_artifact, learn_confidence_threshold
from .pricing import american_to_decimal

logger = logging.getLogger(__name__)

PRIMARY_THRESHOLD_TARGET_HIT_RATE = 0.65
DIAGNOSTIC_THRESHOLD_TARGET_HIT_RATE = 0.60
QUALIFICATION_MINIMUM_HIT_RATE = 0.60
MINIMUM_CALLS = 50
MINIMUM_MONTHLY_CALLS = 10
# v5/v4/v2 lineage (2026-07-21): Eastern-time point-in-time cutoff (was UTC)
# and unified train/serve feature definitions (pitcher_era_gap = shared rolling
# runs-allowed gap). Bump again on any further change to the training basis.
#
# Real bug fixed 2026-08-02: this dict is the *only* thing that decided the
# output filename in write_production_artifacts, and it silently fell out of
# sync with production (mlb rebuilt to v7 on 2026-07-30; this constant still
# said v5). Running validate-models --write-artifacts today would have
# silently overwritten mlb-elo-trend-lr-v5.json -- the immutable rollback
# target v7's own qualification_override_reason explicitly relies on staying
# available -- while the other four sports' constants already matched their
# current production file, meaning a rerun would have quietly rewritten the
# *live* artifact in place with a fresh fit under the same filename. Bumped
# mlb to the next unused version and added a hard overwrite guard below so
# this can't happen silently again regardless of whether this dict drifts.
LEARNED_ARTIFACT_VERSIONS = {
    "mlb": "mlb-elo-trend-lr-v8",
    "nba": "nba-elo-trend-lr-v4",
    "wnba": "wnba-elo-trend-lr-v4",
    "nfl": "nfl-elo-trend-lr-v4",
    "soccer": "soccer-elo-trend-lr-v2",
}


@dataclass(frozen=True)
class ValidationRow:
    date: str
    event_id: str
    outcome: int
    elo_probability: float
    trend_gap: float
    park_factor: float
    weather_factor: float
    park_available: bool
    weather_available: bool
    park_factor_pit: float = 1.0
    elo_neutral_probability: float = 0.5
    trailing_home_win_rate_30d: float = 0.5
    trailing_home_games_30d: int = 0
    residual_trend_gap: float = 0.0
    defensive_trend_gap: float = 0.0
    pitcher_era_gap: float = 0.0
    starter_era_gap: float = 0.0
    starter_fip_gap: float = 0.0
    starter_kbb_gap: float = 0.0
    probable_starter_era_gap: float = 0.0
    probable_starter_available: bool = False
    bullpen_weakness_gap: float = 0.0
    bullpen_available: bool = False
    bullpen_fatigue_gap: float = 0.0
    bullpen_fatigue_available: bool = False
    offense_pit_gap: float = 0.0
    offense_pit_available: bool = False
    consistency_gap: float = 0.0
    hot_cold_gap: float = 0.0
    rest_disparity: float = 0.0
    back_to_back_gap: float = 0.0
    games_last_7_gap: float = 0.0
    schedule_available: float = 0.0
    pythagorean_probability: float = 0.5
    log5_probability: float = 0.5
    outcome_3way: int = 0  # 2=home, 1=draw, 0=away — used for soccer 3-way


FEATURE_VARIANTS: dict[str, tuple[str, ...]] = {
    "elo_only": ("elo_probability",),
    "pythagorean_only": ("pythagorean_probability",),
    "log5_only": ("log5_probability",),
    "elo_pythagorean": ("elo_probability", "pythagorean_probability"),
    "elo_log5": ("elo_probability", "log5_probability"),
    "elo_trend": ("elo_probability", "trend_gap"),
    "elo_trend_defense": ("elo_probability", "trend_gap", "defensive_trend_gap"),
    "elo_trend_full": (
        "elo_probability",
        "trend_gap",
        "defensive_trend_gap",
        "consistency_gap",
        "hot_cold_gap",
    ),
    "elo_trend_adaptive_hfa": (
        "elo_neutral_probability",
        "trend_gap",
        "trailing_home_win_rate_30d",
    ),
    "elo_trend_park": ("elo_probability", "trend_gap", "park_factor"),
    "elo_trend_park_weather": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
    ),
    "elo_trend_park_pitcher": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "pitcher_era_gap",
    ),
    "elo_trend_park_starter": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "starter_era_gap",
    ),
    "elo_trend_park_starter_fip": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "starter_fip_gap",
    ),
    "elo_trend_park_weather_pitcher": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "pitcher_era_gap",
    ),
    "elo_trend_park_weather_pitcher_bullpen": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "pitcher_era_gap",
        "bullpen_weakness_gap",
    ),
    "elo_trend_park_weather_starter_bullpen": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
    ),
    "elo_trend_park_weather_starter_bullpen_fip": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_fip_gap",
        "bullpen_weakness_gap",
    ),
    "elo_trend_park_weather_starter_bullpen_era_fip": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_era_gap",
        "starter_fip_gap",
        "bullpen_weakness_gap",
    ),
    "elo_trend_park_weather_pitcher_bullpen_fatigue": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "pitcher_era_gap",
        "bullpen_fatigue_gap",
    ),
    "elo_trend_park_probable_starter": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "probable_starter_era_gap",
    ),
    "elo_trend_park_weather_probable_starter": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "probable_starter_era_gap",
    ),
    "soccer_3way": ("elo_probability", "trend_gap"),
    "elo_trend_schedule": (
        "elo_probability",
        "trend_gap",
        "rest_disparity",
        "back_to_back_gap",
        "games_last_7_gap",
    ),
    "elo_trend_defense_schedule": (
        "elo_probability",
        "trend_gap",
        "defensive_trend_gap",
        "rest_disparity",
        "back_to_back_gap",
        "games_last_7_gap",
    ),
    # ── v9 ablation: K-BB% replaces ERA ──────────────────────────────────
    # v9 variants consume the empirical point-in-time park factor
    # (park_factor_pit), NOT the static table the v8 variants use -- the
    # static table contains 2026-season data and leaks for any walk-forward
    # before season end (2026-08-13 audit).
    "elo_trend_park_weather_starter_kbb_bullpen": (
        "elo_probability",
        "trend_gap",
        "park_factor_pit",
        "weather_factor",
        "starter_kbb_gap",
        "bullpen_weakness_gap",
    ),
    # ── v9 ablation: ERA + K-BB% together ────────────────────────────────
    "elo_trend_park_weather_starter_era_kbb_bullpen": (
        "elo_probability",
        "trend_gap",
        "park_factor_pit",
        "weather_factor",
        "starter_era_gap",
        "starter_kbb_gap",
        "bullpen_weakness_gap",
    ),
    # ── v9 ablation: Elo-residualized trend (trailing win% - Elo expect) ─
    "elo_residual_trend_park_weather_starter_era_bullpen": (
        "elo_probability",
        "residual_trend_gap",
        "park_factor_pit",
        "weather_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
    ),
    # ── v9 ablation: bullpen fatigue (recent workload) instead of quality ─
    "elo_trend_park_weather_starter_era_bullpen_fatigue": (
        "elo_probability",
        "trend_gap",
        "park_factor_pit",
        "weather_factor",
        "starter_era_gap",
        "bullpen_fatigue_gap",
    ),
    # ── v9 ISOLATING ladder: each entry changes EXACTLY ONE term against the
    # v8 control (elo_trend_park_weather_starter_bullpen), so a delta is
    # attributable to that term alone. The v9 variants above each move two
    # things at once (e.g. PIT park AND the starter stat), which confounds
    # the rung they were meant to test.
    "v8_iso_park_pit": (
        "elo_probability",
        "trend_gap",
        "park_factor_pit",  # <- only change vs v8 control
        "weather_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
    ),
    "v8_iso_residual_trend": (
        "elo_probability",
        "residual_trend_gap",  # <- only change
        "park_factor",
        "weather_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
    ),
    "v8_iso_starter_kbb": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_kbb_gap",  # <- only change
        "bullpen_weakness_gap",
    ),
    "v8_iso_starter_fip": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_fip_gap",  # <- only change
        "bullpen_weakness_gap",
    ),
    "v8_iso_bullpen_fatigue": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_era_gap",
        "bullpen_fatigue_gap",  # <- only change
    ),
    "v8_iso_bullpen_both": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
        "bullpen_fatigue_gap",  # <- only addition
    ),
    # weather_factor carries 26%/97%/52% availability by year and a standard
    # deviation of 0.0084 (near-constant). It is in the v8 control, so every
    # ladder comparison inherits it -- this drops it to measure what it is
    # actually contributing.
    "v8_iso_drop_weather": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
    ),
    # ── v9 Phase 3: batter PIT priors (projected_offense_pit) isolating-
    # ladder control + single-variable candidate (docs/MODEL_IMPROVEMENTS.md
    # section 8, corrected v9 plan 2026-08-19). "_control" is the v9 base
    # (park_factor_pit variant of the ERA+bullpen combo) with no offense
    # feature; "_offense_pit" adds exactly one variable to it.
    "elo_trend_park_weather_starter_era_bullpen_control": (
        "elo_probability",
        "trend_gap",
        "park_factor_pit",
        "weather_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
    ),
    "elo_trend_park_weather_starter_era_bullpen_offense_pit": (
        "elo_probability",
        "trend_gap",
        "park_factor_pit",
        "weather_factor",
        "starter_era_gap",
        "bullpen_weakness_gap",
        "offense_pit_gap",
    ),
}


def build_walk_forward_rows(
    store: FeatureStore,
    sport: str,
    *,
    minimum_history_games: int = 50,
    end_date: str | None = None,
) -> list[ValidationRow]:
    """Construct pregame features using only prior completed dates.

    ``end_date`` (ISO ``YYYY-MM-DD``), when given, excludes any row whose
    date is on/after that cutoff -- lets a replay pin the walk-forward
    dataset to exactly what an artifact's own recorded ``training`` block
    describes, instead of picking up games ``games.jsonl`` accumulated
    afterward (it keeps growing daily). Default ``None`` preserves current
    behavior exactly (all available history).
    """
    games = store.load_games(sport)
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for game in games:
        by_date[game.start.astimezone(EASTERN).date().isoformat()].append(game)

    history: list[GameRecord] = []
    rows: list[ValidationRow] = []
    for day in sorted(by_date):
        if end_date is not None and day >= end_date:
            break
        day_games = sorted(by_date[day], key=lambda item: (item.start, item.event_id))
        if len(history) >= minimum_history_games:
            elo = build_elo(history, sport)
            trends = TrendEngine(history)
            for game in day_games:
                is_soccer = sport.lower() == "soccer"
                if not is_soccer and game.home_score == game.away_score:
                    continue
                home_trend = trends.team_trend(game.home_team)
                away_trend = trends.team_trend(game.away_team)
                # Team-specific (matches learned_forward.py's
                # residual_trend_gap serving definition exactly -- the
                # league-wide rate it used to be was a train/serve skew,
                # fixed 2026-08-13; this also changes v9 ablation numbers,
                # which were computed on the mismatched definition).
                home_win_rate_30d, home_games_30d = _trailing_home_rate(history, day, game.home_team)
                schedule = matchup_schedule_load(
                    history,
                    game.home_team,
                    game.away_team,
                    game.start,
                )

                # Rolling pitching quality: runs allowed per game (last 5).
                # Shared definition with forward inference (features/team_runs).
                pitcher_gap = pitcher_era_gap_from_history(history, game.home_team, game.away_team)

                # Real starter ERA gap from MLB Stats API snapshots (point-in-time)
                starter_gap = _starter_era_gap(game.event_id) if sport.lower() == "mlb" else 0.0
                # Real starter FIP gap (same methodology, FIP instead of ERA)
                starter_fip_gap = _starter_fip_gap(game.event_id) if sport.lower() == "mlb" else 0.0
                # Real starter K-BB% gap (same methodology, (K-BB)/IP instead of ERA/FIP)
                starter_kbb_gap = _starter_kbb_gap(game.event_id) if sport.lower() == "mlb" else 0.0

                # Real bullpen weakness gap from MLB Stats API snapshots (point-in-time)
                bullpen_gap, bullpen_ok = (
                    _bullpen_weakness_gap(game.event_id) if sport.lower() == "mlb" else (0.0, False)
                )

                # Real bullpen recent-workload (fatigue) gap -- who's actually
                # available tonight, as opposed to season-long bullpen quality.
                bullpen_fatigue_gap, bullpen_fatigue_ok = (
                    _bullpen_fatigue_gap(game.event_id) if sport.lower() == "mlb" else (0.0, False)
                )

                # Batter PIT priors gap (v9 Phase 3) -- PA-share-weighted,
                # credibility-shrunk offense composite, home minus away.
                offense_pit_gap, offense_pit_ok = (
                    _offense_pit_gap(game.event_id) if sport.lower() == "mlb" else (0.0, False)
                )

                # Probable starter ERA is usable in historical validation only
                # when an append-only observation proves it existed before
                # first pitch. The legacy retroactive ESPN date cache is
                # deliberately excluded from this path.
                probable_gap, probable_available = 0.0, False
                if sport.lower() == "mlb":
                    try:
                        probable_gap = point_in_time_pitcher_era_gap(
                            game.event_id,
                            game.start,
                        )
                        probable_available = True
                    except ValueError as error:
                        # DD-3 (deep debug audit, 2026-08-04): point_in_time_
                        # pitcher_era_gap's only failure mode is a real,
                        # well-scoped "no archived point-in-time-safe
                        # observation exists for this game" signal
                        # (NO_CALL_STARTERS_NO_PIT_ARCHIVE) -- expected and
                        # common across a large historical backtest, so
                        # debug (not warning) to avoid log spam; still gives
                        # real observability into per-game coverage gaps
                        # that a bare `pass` never surfaced at all.
                        logger.debug(
                            "validation: no point-in-time starter ERA gap for %s: %s",
                            game.event_id,
                            error,
                        )

                # Historical weather from Open-Meteo DB
                weather = _lookup_weather(game.home_team, game.start.astimezone(EASTERN).date().isoformat())

                # Static table (v8-compatible: the active
                # mlb-elo-trend-lr-v8 artifact trained on it) and empirical
                # PIT factor, stored separately so each variant consumes the
                # definition it was validated with (v8 variants: static;
                # v9 variants: park_factor_pit -- 2026-08-13 audit).
                park_static: dict[str, Any] = (
                    park_factor(game.home_team)
                    if sport.lower() == "mlb"
                    else {"park_factor": 1.0, "status": "not_applicable"}
                )
                park_pit: dict[str, Any] = (
                    park_factor_at(game.home_team, day, games_data=history)
                    if sport.lower() == "mlb"
                    else {"park_factor": 1.0, "status": "not_applicable"}
                )
                # 3-way outcome for soccer: 2=home, 1=draw, 0=away
                if is_soccer:
                    if game.home_score > game.away_score:
                        outcome_3way = 2
                    elif game.home_score == game.away_score:
                        outcome_3way = 1
                    else:
                        outcome_3way = 0
                else:
                    outcome_3way = 0
                # Elo-residualized trend: how much the home team's recent
                # actual win rate diverges from Elo's expectation for this
                # game. Positive = home is over-performing vs Elo's prior.
                elo_prob = elo.expected_home_win(game.home_team, game.away_team)
                residual_trend_gap = home_win_rate_30d - elo_prob if home_games_30d >= 10 else 0.0
                rows.append(
                    ValidationRow(
                        date=day,
                        event_id=game.event_id,
                        outcome=int(game.home_score > game.away_score),
                        elo_probability=elo_prob,
                        trend_gap=home_trend.offensive_momentum - away_trend.offensive_momentum,
                        defensive_trend_gap=home_trend.defensive_momentum - away_trend.defensive_momentum,
                        consistency_gap=home_trend.consistency - away_trend.consistency,
                        hot_cold_gap=home_trend.hot_cold_score - away_trend.hot_cold_score,
                        rest_disparity=schedule["rest_disparity"],
                        back_to_back_gap=schedule["back_to_back_gap"],
                        games_last_7_gap=schedule["games_last_7_gap"],
                        schedule_available=schedule["schedule_available"],
                        park_factor=float(park_static["park_factor"]),
                        park_factor_pit=float(park_pit["park_factor"]),
                        weather_factor=float(weather.get("run_factor", 1.0)),
                        pitcher_era_gap=pitcher_gap,
                        starter_era_gap=starter_gap,
                        starter_fip_gap=starter_fip_gap,
                        starter_kbb_gap=starter_kbb_gap,
                        residual_trend_gap=residual_trend_gap,
                        probable_starter_era_gap=probable_gap,
                        probable_starter_available=probable_available,
                        bullpen_weakness_gap=bullpen_gap,
                        bullpen_available=bullpen_ok,
                        bullpen_fatigue_gap=bullpen_fatigue_gap,
                        bullpen_fatigue_available=bullpen_fatigue_ok,
                        offense_pit_gap=offense_pit_gap,
                        offense_pit_available=offense_pit_ok,
                        park_available=park_pit["status"] == "available",
                        weather_available=weather.get("available", False),
                        elo_neutral_probability=elo.expected_neutral_win(game.home_team, game.away_team),
                        trailing_home_win_rate_30d=home_win_rate_30d,
                        trailing_home_games_30d=home_games_30d,
                        outcome_3way=outcome_3way,
                    )
                )
        history.extend(day_games)
    return rows


def rolling_walk_forward_splits(
    rows: Sequence[ValidationRow],
    *,
    n_windows: int = 3,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> list[tuple[list[ValidationRow], list[ValidationRow], list[ValidationRow]]]:
    """Rolling-origin walk-forward splits (the nested walk-forward structure).

    Splits the unique dates into ``n_windows`` contiguous chronological
    blocks. Window ``k`` uses block ``k`` as its test set and everything
    strictly before it (split by the usual fractions over its own dates)
    as train/validation — so hyperparameters, feature selection,
    calibrators, and edge thresholds can all be chosen inside historical
    folds while the newest block is only ever a final test. Windows whose
    prior history has fewer than five distinct dates are skipped. Unlike
    ``chronological_split``, the newest period is never mixed into any
    train/validation cohort.
    """
    dates = sorted({row.date for row in rows})
    if len(dates) < 5:
        raise ValueError("rolling walk-forward requires at least five distinct game dates")
    by_date: dict[str, list[ValidationRow]] = {}
    for row in rows:
        by_date.setdefault(row.date, []).append(row)

    block_size = max(1, math.ceil(len(dates) / n_windows))
    blocks = [dates[i : i + block_size] for i in range(0, len(dates), block_size)]

    splits: list[tuple[list[ValidationRow], list[ValidationRow], list[ValidationRow]]] = []
    for block in blocks:
        prior_dates = [d for d in dates if d < block[0]]
        if len(prior_dates) < 5:
            continue
        train_count = max(1, math.floor(len(prior_dates) * train_fraction))
        val_count = max(1, math.floor(len(prior_dates) * validation_fraction))
        train_dates = prior_dates[:train_count]
        val_dates = prior_dates[train_count : train_count + val_count]
        splits.append(
            (
                [row for d in train_dates for row in by_date[d]],
                [row for d in val_dates for row in by_date[d]],
                [row for d in block for row in by_date[d]],
            )
        )
    return splits


def chronological_split(
    rows: Sequence[ValidationRow],
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    train_end_date: str | None = None,
    validation_end_date: str | None = None,
) -> tuple[list[ValidationRow], list[ValidationRow], list[ValidationRow], dict[str, Any]]:
    """Split on complete dates so games from one date never cross cohorts.

    By default splits by fraction-of-unique-dates in ``rows`` (unchanged
    behavior). When both ``train_end_date`` and ``validation_end_date`` are
    given (ISO ``YYYY-MM-DD``, inclusive upper bounds on each cohort), the
    split instead reconstructs the three cohorts at those exact calendar
    boundaries -- e.g. to replay a production artifact's own recorded
    ``training`` block rather than recomputing fractions against however
    many rows the caller happens to hand in today.
    """
    if not rows:
        raise ValueError("cannot split an empty validation dataset")

    if train_end_date is not None or validation_end_date is not None:
        if train_end_date is None or validation_end_date is None:
            raise ValueError("train_end_date and validation_end_date must be given together")
        if train_end_date >= validation_end_date:
            raise ValueError("train_end_date must precede validation_end_date")
        train = [row for row in rows if row.date <= train_end_date]
        validation = [row for row in rows if train_end_date < row.date <= validation_end_date]
        holdout = [row for row in rows if row.date > validation_end_date]
        if not train or not validation or not holdout:
            raise ValueError("chronological split produced an empty cohort")
        metadata = {
            "method": "explicit_date_boundaries",
            "train": _cohort_metadata(train),
            "validation": _cohort_metadata(validation),
            "locked_holdout": _cohort_metadata(holdout),
        }
        return train, validation, holdout, metadata

    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("split fractions must be between zero and one")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("split fractions must leave a holdout")

    dates = sorted({row.date for row in rows})
    if len(dates) < 5:
        raise ValueError("validation requires at least five distinct game dates")
    train_count = max(1, math.floor(len(dates) * train_fraction))
    validation_count = max(1, math.floor(len(dates) * validation_fraction))
    holdout_start_index = min(train_count + validation_count, len(dates) - 1)
    validation_start = dates[train_count]
    holdout_start = dates[holdout_start_index]
    train = [row for row in rows if row.date < validation_start]
    validation = [row for row in rows if validation_start <= row.date < holdout_start]
    holdout = [row for row in rows if row.date >= holdout_start]
    if not train or not validation or not holdout:
        raise ValueError("chronological split produced an empty cohort")
    metadata = {
        "method": "complete_date_60_20_20",
        "train": _cohort_metadata(train),
        "validation": _cohort_metadata(validation),
        "locked_holdout": _cohort_metadata(holdout),
    }
    return train, validation, holdout, metadata


def run_sport_validation(store: FeatureStore, sport: str) -> dict[str, Any]:
    rows = build_walk_forward_rows(store, sport)
    train, validation, holdout, split = chronological_split(rows)
    variants_to_run = ["elo_only", "elo_trend"]
    if sport.lower() in ("nba", "wnba"):
        variants_to_run.append("elo_trend_defense")
    if sport.lower() == "mlb":
        variants_to_run.extend(
            [
                "elo_trend_adaptive_hfa",
                "elo_trend_park",
                "elo_trend_park_weather",
                "elo_trend_park_pitcher",
                "elo_trend_park_weather_pitcher",
                "elo_trend_park_weather_pitcher_bullpen",
                "elo_trend_park_weather_pitcher_bullpen_fatigue",
            ]
        )
    if sport.lower() == "soccer":
        # Tested 2026-07-25 via paired holdout ablation against the production
        # elo_trend baseline (see config/tested_features.json): none of these
        # beat it at a Holm-corrected significance level (p >= 0.37). Kept in
        # the tracked variant set so re-tests as data grows are one command,
        # not a from-scratch script — not because they're expected to win.
        variants_to_run.extend(
            [
                "soccer_3way",
                "elo_trend_defense",
                "elo_trend_schedule",
                "elo_trend_defense_schedule",
                "elo_trend_full",
            ]
        )
    variants = {}
    for name in variants_to_run:
        if name == "soccer_3way":
            variants[name] = evaluate_variant_3way(train, validation, holdout, FEATURE_VARIANTS[name])
        else:
            variants[name] = evaluate_variant(train, validation, holdout, FEATURE_VARIANTS[name])
    agreement = evaluate_agreement(train, validation, holdout)
    return {
        "sport": sport.lower(),
        "walk_forward": True,
        "threshold_source": "later validation cohort; never locked holdout",
        "split": split,
        "feature_coverage": {
            "park": round(sum(row.park_available for row in rows) / len(rows), 6),
            "weather": round(sum(row.weather_available for row in rows) / len(rows), 6),
        },
        "variants": variants,
        "cross_model_agreement": agreement,
        "agreement_comparison": _agreement_comparison(variants["elo_trend"], agreement),
        "confidence_gap_audit": confidence_gap_equivalence(variants["elo_trend"]["primary_65"]),
        "pitcher_feature_audit": (
            historical_pitcher_feature_audit(store) if sport.lower() == "mlb" else None
        ),
        "multi_market_readiness": multi_market_readiness(store, sport),
        "feature_decisions": _feature_decisions(variants, sport),
    }


def _trailing_home_rate(history: Sequence[GameRecord], day: str, home_team: str) -> tuple[float, int]:
    """Trailing-30-day HOME-TEAM win rate (wins / non-tie home games).

    Must match learned_forward.py's residual_trend_gap serving definition
    exactly: same 30-day window, same ``home_team`` filter, same tie
    exclusion; the caller applies the same >=10-team-game gate with the same
    0.0 fallback. (It used to be a league-wide rate -- a train/serve skew,
    fixed 2026-08-13.)
    """
    cutoff = date.fromisoformat(day) - timedelta(days=30)
    recent = [
        game
        for game in history
        if cutoff <= game.start.astimezone(EASTERN).date() < date.fromisoformat(day)
        and game.home_score != game.away_score
        and game.home_team == home_team
    ]
    if not recent:
        return 0.5, 0
    return sum(game.home_score > game.away_score for game in recent) / len(recent), len(recent)


def confidence_gap_equivalence(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Show why a binary confidence-gap gate is only a threshold reparameterization."""
    if evaluation.get("status") != "evaluated":
        return {"status": "unavailable", "reason": evaluation.get("reason", "not evaluated")}
    confidence_threshold = float(evaluation["learned_threshold"])
    return {
        "status": "mathematically_equivalent",
        "identity": "abs(P(home)-P(away)) = 2*max(P(home),P(away))-1",
        "confidence_threshold": confidence_threshold,
        "equivalent_gap_threshold": round(2 * confidence_threshold - 1, 8),
        "changes_selection_order": False,
        "decision": "REJECT_AS_REDUNDANT_GATE",
    }


def historical_pitcher_feature_audit(store: FeatureStore) -> dict[str, Any]:
    """Measure raw starter coverage while refusing postgame-retrieved leakage."""
    events = both_probables = both_era = 0
    raw_root = store.data_root / "raw" / "mlb"
    for path in raw_root.glob("*/scores_mlb.json") if raw_root.exists() else ():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for event in payload.get("events", []):
            events += 1
            competitors = (event.get("competitions") or [{}])[0].get("competitors", [])
            probables = [(competitor.get("probables") or []) for competitor in competitors]
            if len(probables) != 2 or not all(probables):
                continue
            both_probables += 1
            era_values = []
            for probable in probables:
                stats = probable[0].get("statistics") or []
                era_values.append(any(item.get("name") == "ERA" for item in stats))
            both_era += all(era_values)
    return {
        "events_scanned": events,
        "both_probable_starters": both_probables,
        "both_starter_era_values": both_era,
        "raw_coverage": round(both_era / events, 6) if events else 0.0,
        "point_in_time_valid": False,
        "decision": "REJECT_HISTORICAL_PITCHER_FEATURES_LEAKAGE_RISK",
        "reason": (
            "Scoreboard caches were retrieved retrospectively and do not pin an observed-at "
            "timestamp before first pitch; displayed season records can include future games."
        ),
        "activation_requirement": (
            "Prospectively cache starter game logs and bullpen usage with observed_at_utc, "
            "then train a new version on only records available before each event."
        ),
    }


def multi_market_readiness(store: FeatureStore, sport: str) -> dict[str, Any]:
    """Report whether exact non-moneyline contracts can be validated honestly.

    Two-stage scan: (1) ESPN raw score files for legacy odds entries (informational
    only — these are almost always empty), then (2) timestamp-valid Polymarket
    BBO snapshots under ``data/odds/{sport}/*/polymarket_snapshots.jsonl`` as the
    authoritative source for spread and total contract lines.
    """
    key = sport.lower()

    # ── Stage 1: legacy ESPN raw-file scan (diagnostic only) ──────────────
    raw_root = store.data_root / "raw" / key
    events = 0
    first_inning_outcomes = first_five_outcomes = 0
    for path in raw_root.glob(f"*/scores_{key}.json") if raw_root.exists() else ():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for event in payload.get("events", []):
            events += 1
            if key != "mlb":
                continue
            competition = (event.get("competitions") or [{}])[0]
            competitors = competition.get("competitors", [])
            if len(competitors) != 2:
                continue
            periods = [
                {int(item.get("period", 0)) for item in competitor.get("linescores") or []}
                for competitor in competitors
            ]
            first_inning_outcomes += all(1 in values for values in periods)
            first_five_outcomes += all(set(range(1, 6)).issubset(values) for values in periods)

    # ── Stage 2: Polymarket BBO snapshot scan (authoritative) ─────────────
    odds_root = store.data_root / "odds" / key
    spread_snapshots = total_snapshots = 0
    if odds_root.exists():
        for snap_path in sorted(odds_root.glob("*/polymarket_snapshots.jsonl")):
            try:
                with snap_path.open(encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            snap = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        mt = snap.get("market_type")
                        if mt not in ("spread", "total"):
                            continue
                        # Exclude sub-market contracts (F5, YRFI, team totals, etc.)
                        # whose lines are not valid for full-game spread/total validation.
                        slug = str(snap.get("market_slug") or "").casefold()
                        if _is_sub_market_slug(slug, key):
                            continue
                        # Note: we do NOT filter by timestamp_valid here. The
                        # line is a contract parameter, not a price observation.
                        # A post-start snapshot still carries the correct line.
                        if mt == "spread":
                            spread_snapshots += 1
                        elif mt == "total":
                            total_snapshots += 1
            except OSError as error:
                # DD-3 (deep debug audit, 2026-08-04): this used to be a
                # bare `continue` -- a genuine I/O failure mid-read (not
                # "file doesn't exist", already excluded by the caller's own
                # exists()/glob() check) silently truncated this file's
                # count with no way to tell it apart from a file that
                # legitimately had nothing left to contribute.
                logger.warning("multi_market_readiness: failed reading %s: %s", snap_path, error)
                continue

    # ── Stage 3: legacy flat-file backfill ────────────────────────────────
    spread_snapshots, total_snapshots = _add_legacy_backfill(
        store.data_root, key, spread_snapshots, total_snapshots
    )

    # ── Per-sport readiness ───────────────────────────────────────────────
    if key in {"nba", "wnba", "nfl"}:
        spread_status, total_status = _readiness_for_market(
            spread_snapshots, total_snapshots, "basketball" if key != "nfl" else "football"
        )
        return {
            "events_scanned": events,
            "spread_lines": spread_snapshots,
            "total_lines": total_snapshots,
            "model_parameters_changed": False,
            "spread": spread_status,
            "total": total_status,
            "reason": _readiness_reason(spread_snapshots, total_snapshots),
            "source": "data/odds/polymarket_snapshots.jsonl",
        }

    if key == "mlb":
        # F5/YRFI/NRFI lines are excluded from the full-game count by
        # _is_sub_market_slug during Stage 2. Count them separately here
        # from the same snapshot files (without the sub-market filter).
        f5_spread = f5_total = yrfi_nrfi = 0
        if odds_root.exists():
            for snap_path in sorted(odds_root.glob("*/polymarket_snapshots.jsonl")):
                try:
                    with snap_path.open(encoding="utf-8") as fh:
                        for line in fh:
                            try:
                                snap = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            slug = str(snap.get("market_slug") or "").casefold()
                            mt = snap.get("market_type")
                            if "-f5-" in slug:
                                if mt == "spread":
                                    f5_spread += 1
                                elif mt == "total":
                                    f5_total += 1
                            elif "-yrfi" in slug or "-nrfi" in slug:
                                yrfi_nrfi += 1
                except OSError as error:
                    # DD-3: see Stage 2's matching comment above.
                    logger.warning("multi_market_readiness: failed reading %s: %s", snap_path, error)
                    continue

        fg_spread_status, fg_total_status = _readiness_for_market(
            spread_snapshots, total_snapshots, "baseball"
        )
        f5_spread_status, f5_total_status = _readiness_for_market(f5_spread, f5_total, "baseball")
        return {
            "events_scanned": events,
            "first_inning_outcomes": first_inning_outcomes,
            "first_five_outcomes": first_five_outcomes,
            "full_game_spread": fg_spread_status,
            "full_game_total": fg_total_status,
            "full_game_spread_lines": spread_snapshots,
            "full_game_total_lines": total_snapshots,
            "first_five_spread": f5_spread_status,
            "first_five_total": f5_total_status,
            "first_five_spread_lines": f5_spread,
            "first_five_total_lines": f5_total,
            "yrfi_nrfi": (
                "DATA_READY_PENDING_BACKTEST"
                if yrfi_nrfi >= _MINIMUM_SNAPSHOT_COUNT
                else "BLOCKED_MISSING_POINT_IN_TIME_STARTER_INPUTS"
            ),
            "yrfi_nrfi_lines": yrfi_nrfi,
            "reason": (
                f"Full-game: {spread_snapshots} spread, {total_snapshots} total lines. "
                f"F5: {f5_spread} spread, {f5_total} total. "
                f"YRFI/NRFI: {yrfi_nrfi} snapshots. "
                "Line validation from Polymarket snapshots; "
                "pregame starter/bullpen snapshots are still pending."
            ),
            "source": "data/odds/polymarket_snapshots.jsonl",
        }

    if key in {"kbo", "npb"}:
        spread_status, total_status = _readiness_for_market(spread_snapshots, total_snapshots, "baseball")
        return {
            "events_scanned": events,
            "spread_lines": spread_snapshots,
            "total_lines": total_snapshots,
            "model_parameters_changed": False,
            "spread": spread_status,
            "total": total_status,
            "reason": _readiness_reason(spread_snapshots, total_snapshots),
            "source": "data/odds/polymarket_snapshots.jsonl",
        }

    if key == "esports":
        return {
            "events_scanned": events,
            "spread_lines": spread_snapshots,
            "total_lines": total_snapshots,
            "model_parameters_changed": False,
            "spread": "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES",
            "total": "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES",
            "reason": (
                "Esports contracts on Polymarket are moneyline-only; "
                "spread and total markets do not exist for LoL/CS2/Valorant/etc."
            ),
            "source": "data/odds/polymarket_snapshots.jsonl",
        }

    return {"status": "not_requested", "events_scanned": events}


def _is_sub_market_slug(slug: str, sport: str) -> bool:
    """Return True if the slug belongs to a sub-market that is not a full-game contract.

    First-5-innings (F5), first-half, team totals, and player props are
    excluded because their lines cannot validate full-game spread/total models.
    """
    # MLB: F5 (first 5 innings), YRFI, team totals, player props
    if sport == "mlb":
        _MLB_SUB_PATTERNS = (
            "-f5-",  # first 5 innings
            "-yrfi",  # yes run first inning
            "-nrfi",  # no run first inning
            "-tt-",  # team total
        )
        if any(pattern in slug for pattern in _MLB_SUB_PATTERNS):
            return True
    # NBA/WNBA: quarter/half markets, player props
    if sport in ("nba", "wnba"):
        _BBALL_SUB_PATTERNS = (
            "-1q-",
            "-2q-",
            "-3q-",
            "-4q-",  # quarter markets
            "-1h-",
            "-2h-",  # half markets
        )
        if any(pattern in slug for pattern in _BBALL_SUB_PATTERNS):
            return True
    return False


_MINIMUM_SNAPSHOT_COUNT = 50


def _readiness_for_market(
    spread_count: int,
    total_count: int,
    sport_family: str,
) -> tuple[str, str]:
    """Map snapshot counts to readiness status strings."""

    def _status(count: int) -> str:
        if count >= _MINIMUM_SNAPSHOT_COUNT:
            return "DATA_READY_PENDING_BACKTEST"
        if count > 0:
            return "INSUFFICIENT_DATA_IN_PROGRESS"
        return "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES"

    return _status(spread_count), _status(total_count)


def _readiness_reason(spread_count: int, total_count: int) -> str:
    """Human-readable reason derived from the snapshot counts."""
    if spread_count == 0 and total_count == 0:
        return (
            "A spread or total outcome is undefined without its exact pregame line; "
            "score-only history cannot validate the configured normal-CDF heads. "
            "No Polymarket spread or total contract snapshots found — the daily BBO "
            "capture pipeline has not yet collected these markets for this sport."
        )
    parts = []
    if spread_count < _MINIMUM_SNAPSHOT_COUNT:
        parts.append(f"spread: {spread_count}/{_MINIMUM_SNAPSHOT_COUNT} contract snapshots")
    if total_count < _MINIMUM_SNAPSHOT_COUNT:
        parts.append(f"total: {total_count}/{_MINIMUM_SNAPSHOT_COUNT} contract snapshots")
    if parts:
        return (
            f"Need >= {_MINIMUM_SNAPSHOT_COUNT} contract line snapshots "
            f"per market for backtesting. {'; '.join(parts)}. "
            "Spread and total outcomes are undefined without their exact pregame lines."
        )
    return (
        f"Historical contract lines available from Polymarket snapshots "
        f"(spread: {spread_count}, total: {total_count}). "
        "Spread and total backtesting is data-ready pending a locked-holdout evaluation."
    )


def _add_legacy_backfill(data_root: Path, sport: str, spread_count: int, total_count: int) -> tuple[int, int]:
    """Scan legacy flat-file snapshots that predate the per-sport odds directory.

    Sources:
    - ``data/polymarket_us_snapshots.jsonl`` — early Polymarket snapshots
      where sport and market_type must be inferred from the slug prefix.
    - ``data/market_odds_snapshots.jsonl`` — MLB-specific market odds
      format with explicit ``markets.spread`` / ``markets.total`` keys.
    """

    # ── Legacy Polymarket flat file (slug-based inference) ─────────────
    legacy_pm_path = data_root / "polymarket_us_snapshots.jsonl"
    if legacy_pm_path.exists():
        # Slug prefixes: aec=moneyline, asc=spread, tsc=total
        _PM_PREFIX_MAP = {"asc": "spread", "tsc": "total"}
        try:
            with legacy_pm_path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        snap = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    slug = str(snap.get("market_slug") or "")
                    parts = slug.split("-")
                    if len(parts) < 2:
                        continue
                    prefix = parts[0].casefold()
                    slug_sport = parts[1].casefold() if len(parts) > 1 else ""
                    if slug_sport != sport:
                        continue
                    mt = _PM_PREFIX_MAP.get(prefix)
                    if mt is None:
                        continue
                    if mt == "spread":
                        spread_count += 1
                    elif mt == "total":
                        total_count += 1
        except OSError as error:
            # DD-3 (deep debug audit, 2026-08-04): this used to be a bare
            # `pass` -- a genuine I/O failure mid-read (the file's own
            # exists() check above already ruled out "doesn't exist")
            # silently left spread_count/total_count at whatever partial
            # value they'd reached, indistinguishable from a legacy file
            # that legitimately had nothing more relevant in it.
            logger.warning("_add_legacy_backfill: failed reading %s: %s", legacy_pm_path, error)

    # ── Legacy market odds file (MLB-specific dict format) ─────────────
    legacy_mo_path = data_root / "market_odds_snapshots.jsonl"
    if sport == "mlb" and legacy_mo_path.exists():
        try:
            with legacy_mo_path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        snap = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    markets = snap.get("markets") or {}
                    if isinstance(markets.get("spread"), dict):
                        spread_count += 1
                    if isinstance(markets.get("total"), dict):
                        total_count += 1
        except OSError as error:
            # DD-3: see the legacy Polymarket file's matching comment above.
            logger.warning("_add_legacy_backfill: failed reading %s: %s", legacy_mo_path, error)

    return spread_count, total_count


def run_validation_audit(
    store: FeatureStore,
    sports: Sequence[str],
    reconstructed_mlb_prices: str | Path | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "1",
        "primary_qualification": {
            "minimum_locked_holdout_calls": MINIMUM_CALLS,
            "minimum_hit_rate": QUALIFICATION_MINIMUM_HIT_RATE,
            "confidence_threshold_validation_target": PRIMARY_THRESHOLD_TARGET_HIT_RATE,
        },
        "secondary_reporting": ["brier_score", "calibration"],
        "unit_pnl": "diagnostic flat one-unit staking at -110",
        "sports": {sport.lower(): run_sport_validation(store, sport) for sport in sports},
    }
    if "mlb" in report["sports"]:
        report["sports"]["mlb"]["historical_price_diagnostic"] = evaluate_reconstructed_mlb_moneyline(
            store, reconstructed_mlb_prices
        )
    return report


def build_production_artifact(sport_report: Mapping[str, Any]) -> dict[str, Any]:
    """Pin the audited Elo+trend LR and validation-learned moneyline gate."""
    sport = str(sport_report["sport"]).lower()
    if sport not in LEARNED_ARTIFACT_VERSIONS:
        raise ValueError(f"no learned artifact version configured for {sport}")
    if sport in ("nba", "wnba"):
        variant_name = "elo_trend_defense"
    elif sport == "mlb":
        variant_name = "elo_trend_park_weather_pitcher"
    else:
        variant_name = "elo_trend"
    variants = sport_report["variants"]
    if variant_name not in variants:
        variant_name = "elo_trend"  # fallback for tests/legacy
    variant = variants[variant_name]
    primary = variant["primary_65"]
    if primary.get("status") != "evaluated":
        raise ValueError(f"{sport} has no evaluated primary confidence gate")
    feature_names = tuple(variant["features"])
    split = sport_report["split"]
    qualification = dict(primary["locked_holdout"])
    qualification["market_type"] = "moneyline"
    qualification["framework"] = "locked_complete_date_60_20_20"
    return build_artifact(
        sport=sport,
        model_version=LEARNED_ARTIFACT_VERSIONS[sport],
        market_models={
            "moneyline": {
                "feature_names": list(feature_names),
                "coefficients": [float(variant["coefficients"][name]) for name in feature_names],
                "intercept": float(variant["intercept"]),
                "confidence_threshold": float(primary["learned_threshold"]),
                "positive_class": "home",
            }
        },
        training={
            "coefficient_fit": split["train"],
            "threshold_selection": split["validation"],
            "locked_holdout": split["locked_holdout"],
            "threshold_source": sport_report["threshold_source"],
            "walk_forward_features": True,
            "market_inputs_used": False,
        },
        qualification=qualification,
    )


def write_production_artifacts(report: Mapping[str, Any], destination: str | Path) -> dict[str, str]:
    """Write one immutable, hash-verified artifact per audited sport.

    Refuses to overwrite an existing versioned artifact file -- these are
    rollback/promotion targets other config (qualification_override_reason,
    legacy_research_rollback pointers) relies on staying exactly as written.
    A stale or unbumped LEARNED_ARTIFACT_VERSIONS entry must fail loudly here,
    not silently rewrite production or a kept rollback artifact in place.
    """
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for sport, sport_report in report["sports"].items():
        artifact = build_production_artifact(sport_report)
        path = root / f"{artifact['model_version']}.json"
        if path.exists():
            raise FileExistsError(
                f"refusing to overwrite existing versioned artifact {path} -- "
                f"bump LEARNED_ARTIFACT_VERSIONS[{sport!r}] to a new, unused "
                "version before writing production artifacts again"
            )
        path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths[sport] = str(path)
    return paths


def evaluate_reconstructed_mlb_moneyline(
    store: FeatureStore,
    price_path: str | Path | None,
) -> dict[str, Any]:
    """Price the learned MLB calls on postgame-reconstructed opening odds."""
    if price_path is None or not Path(price_path).exists():
        return {"status": "unavailable", "reason": "reconstructed price file not found"}
    quotes: dict[str, dict[str, Any]] = {}
    with Path(price_path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            moneyline = item.get("markets", {}).get("moneyline", {})
            if moneyline:
                quotes[str(item["event_id"])] = {"metadata": item, "sides": moneyline}

    rows = build_walk_forward_rows(store, "mlb")
    train, validation, holdout, _ = chronological_split(rows)
    feature_names = FEATURE_VARIANTS["elo_trend"]
    model = _fit(train, feature_names)
    validation_probabilities = _predict(model, validation, feature_names)
    try:
        threshold, _ = learn_confidence_threshold(
            validation_probabilities,
            [row.outcome for row in validation],
            target_hit_rate=PRIMARY_THRESHOLD_TARGET_HIT_RATE,
            minimum_calls=MINIMUM_CALLS,
        )
    except ValueError as error:
        return {"status": "unavailable", "reason": str(error)}

    holdout_probabilities = _predict(model, holdout, feature_names)
    pnl = 0.0
    hits = 0
    priced_calls = 0
    all_calls = 0
    edges: list[float] = []
    providers: set[str] = set()
    timestamp_valid_values: set[bool] = set()
    for probability, row in zip(holdout_probabilities, holdout, strict=True):
        confidence = max(probability, 1 - probability)
        if confidence < threshold:
            continue
        all_calls += 1
        quote = quotes.get(row.event_id)
        if quote is None:
            continue
        selection = "home" if probability >= 0.5 else "away"
        odds = int(quote["sides"][selection]["american_odds"])
        implied = {
            side: 1 / american_to_decimal(int(values["american_odds"]))
            for side, values in quote["sides"].items()
        }
        market_probability = implied[selection] / sum(implied.values())
        selected_outcome = row.outcome if selection == "home" else 1 - row.outcome
        hits += selected_outcome
        pnl += american_to_decimal(odds) - 1 if selected_outcome else -1
        edges.append(confidence - market_probability)
        priced_calls += 1
        metadata = quote["metadata"]
        providers.add(str(metadata.get("provider", "unknown")))
        timestamp_valid_values.add(bool(metadata.get("timestamp_valid", False)))
    return {
        "status": "diagnostic_only",
        "model": "elo_trend_logistic_regression",
        "learned_threshold": threshold,
        "holdout_calls": all_calls,
        "priced_calls": priced_calls,
        "priced_hit_rate": round(hits / priced_calls, 6) if priced_calls else None,
        "flat_pnl_at_reconstructed_odds": round(pnl, 6),
        "roi_at_reconstructed_odds": round(pnl / priced_calls, 6) if priced_calls else None,
        "mean_model_minus_no_vig_market_probability": (round(sum(edges) / len(edges), 6) if edges else None),
        "providers": sorted(providers),
        "timestamp_valid_values": sorted(timestamp_valid_values),
        "qualification_gate": False,
        "limitation": (
            "Postgame-retrieved sportsbook openings are not Polymarket executable asks and cannot "
            "establish trade profitability."
        ),
    }


# Features whose availability is tracked by a per-row flag on ValidationRow.
# Everything else is always computed from history and counts as available.
_FEATURE_AVAILABILITY_FLAGS: dict[str, str] = {
    "park_factor": "park_available",
    "weather_factor": "weather_available",
    "probable_starter_era_gap": "probable_starter_available",
    "bullpen_weakness_gap": "bullpen_available",
    "bullpen_fatigue_gap": "bullpen_fatigue_available",
    "offense_pit_gap": "offense_pit_available",
}


def _all_rows_calibration(probabilities: Sequence[float], rows: Sequence[ValidationRow]) -> dict[str, object]:
    """Model-quality metrics over EVERY row of a split (not just called
    rows): log loss, Brier, ECE, and calibration slope/intercept. These are
    threshold-independent, so they are the honest first-order comparison
    between variants; units/P&L come after (operator directive 2026-08-13:
    metrics-first reporting, units secondary)."""
    metrics = calibration_metrics(
        [min(1 - 1e-12, max(1e-12, float(p))) for p in probabilities],
        [row.outcome for row in rows],
    )
    if metrics.get("status") != "ok":
        return metrics
    return {
        "log_loss": round(float(metrics["log_loss"]), 6),
        "brier_score": round(float(metrics["brier_score"]), 6),
        "expected_calibration_error": round(float(metrics["expected_calibration_error"]), 6),
        "calibration_slope": (
            round(float(metrics["calibration_slope"]), 6)
            if metrics["calibration_slope"] is not None
            else None
        ),
        "calibration_intercept": (
            round(float(metrics["calibration_intercept"]), 6)
            if metrics["calibration_intercept"] is not None
            else None
        ),
        "sample_size": metrics["sample_size"],
    }


def _variant_coverage(rows: Sequence[ValidationRow], feature_names: Sequence[str]) -> float:
    """Fraction of rows where every availability-flagged feature is present."""
    if not rows:
        return 0.0
    flagged = [name for name in feature_names if name in _FEATURE_AVAILABILITY_FLAGS]
    if not flagged:
        return 1.0
    available = sum(
        1 for row in rows if all(bool(getattr(row, _FEATURE_AVAILABILITY_FLAGS[name])) for name in flagged)
    )
    return round(available / len(rows), 6)


def evaluate_variant(
    train: Sequence[ValidationRow],
    validation: Sequence[ValidationRow],
    holdout: Sequence[ValidationRow],
    feature_names: Sequence[str],
    *,
    fixed_threshold: float | None = None,
) -> dict[str, Any]:
    """Fit on train, learn (or reuse) a threshold, grade the locked holdout.

    ``fixed_threshold``, when given, is passed through to the primary
    (0.65-target) threshold step only -- a reproduction replay pins the
    primary evaluation to a pre-supplied threshold; the diagnostic (0.60
    target) view still learns its own threshold as before. Default ``None``
    preserves current behavior exactly.
    """
    model = _fit(train, feature_names)
    train_probabilities = _predict(model, train, feature_names)
    validation_probabilities = _predict(model, validation, feature_names)
    holdout_probabilities = _predict(model, holdout, feature_names)
    primary = _learn_and_grade(
        validation_probabilities,
        validation,
        holdout_probabilities,
        holdout,
        target_hit_rate=PRIMARY_THRESHOLD_TARGET_HIT_RATE,
        fixed_threshold=fixed_threshold,
    )
    diagnostic = _learn_and_grade(
        validation_probabilities,
        validation,
        holdout_probabilities,
        holdout,
        target_hit_rate=DIAGNOSTIC_THRESHOLD_TARGET_HIT_RATE,
    )
    return {
        "features": list(feature_names),
        "coefficients": {
            name: round(float(value), 10) for name, value in zip(feature_names, model.coef_[0], strict=True)
        },
        "intercept": round(float(model.intercept_[0]), 10),
        "primary_65": primary,
        "diagnostic_60": diagnostic,
        # Threshold-independent model-quality metrics over the full holdout
        # (operator directive 2026-08-13: LogLoss/Brier/ECE/calibration lead;
        # units are secondary evidence).
        "holdout_all_rows": _all_rows_calibration(holdout_probabilities, holdout),
        "coverage": _variant_coverage(holdout, feature_names),
        # Same all-rows metrics per split so cross-fold stability is visible
        # (a variant whose holdout beats its validation by a wide margin is
        # overfit to the fold boundaries, not better).
        "per_split": {
            "train": _all_rows_calibration(train_probabilities, train),
            "validation": _all_rows_calibration(validation_probabilities, validation),
            "holdout": _all_rows_calibration(holdout_probabilities, holdout),
        },
    }


def evaluate_agreement(
    train: Sequence[ValidationRow],
    validation: Sequence[ValidationRow],
    holdout: Sequence[ValidationRow],
) -> dict[str, Any]:
    """Require independently learned Elo and trend models to agree."""
    elo_model = _fit(train, ("elo_probability",))
    trend_model = _fit(train, ("trend_gap",))
    validation_elo = _predict(elo_model, validation, ("elo_probability",))
    validation_trend = _predict(trend_model, validation, ("trend_gap",))
    holdout_elo = _predict(elo_model, holdout, ("elo_probability",))
    holdout_trend = _predict(trend_model, holdout, ("trend_gap",))
    return {
        "rule": "Elo and trend predict the same side; minimum of both confidences clears learned threshold",
        "primary_65": _learn_and_grade_agreement(
            validation_elo,
            validation_trend,
            validation,
            holdout_elo,
            holdout_trend,
            holdout,
            PRIMARY_THRESHOLD_TARGET_HIT_RATE,
        ),
        "diagnostic_60": _learn_and_grade_agreement(
            validation_elo,
            validation_trend,
            validation,
            holdout_elo,
            holdout_trend,
            holdout,
            DIAGNOSTIC_THRESHOLD_TARGET_HIT_RATE,
        ),
    }


def evaluate_variant_3way(
    train: Sequence[ValidationRow],
    validation: Sequence[ValidationRow],
    holdout: Sequence[ValidationRow],
    feature_names: Sequence[str],
) -> dict[str, Any]:
    """3-way multinomial LR for soccer (home/draw/away)."""
    model = LogisticRegression(max_iter=2_000, solver="lbfgs")
    X_train = _matrix(train, feature_names)
    y_train = [row.outcome_3way for row in train]
    model.fit(X_train, y_train)

    # Predict on holdout: 3 probabilities per row
    holdout_probs = model.predict_proba(_matrix(holdout, feature_names))
    validation_probs = model.predict_proba(_matrix(validation, feature_names))

    # For each row: confidence = max prob, selection = argmax (0=away,1=draw,2=home)
    val_confidences = [float(max(p)) for p in validation_probs]
    val_selections = [int(p.argmax()) for p in validation_probs]
    ho_confidences = [float(max(p)) for p in holdout_probs]
    ho_selections = [int(p.argmax()) for p in holdout_probs]

    # Learn threshold on validation
    val_outcomes = [1 if s == r.outcome_3way else 0 for s, r in zip(val_selections, validation)]
    try:
        threshold, val_stats = learn_confidence_threshold(
            val_confidences,
            val_outcomes,
            target_hit_rate=PRIMARY_THRESHOLD_TARGET_HIT_RATE,
            minimum_calls=MINIMUM_CALLS,
        )
    except ValueError as error:
        return {
            "features": list(feature_names),
            "coefficients_by_class": {
                f"class_{k}": {name: round(float(c), 10) for name, c in zip(feature_names, model.coef_[k])}
                for k in range(3)
            },
            "intercept": [round(float(i), 10) for i in model.intercept_],
            "primary_65": {"status": "no_validation_threshold", "reason": str(error)},
        }

    # Grade holdout
    calls = hits = 0
    for conf, sel, row in zip(ho_confidences, ho_selections, holdout):
        if conf < threshold:
            continue
        calls += 1
        hits += 1 if sel == row.outcome_3way else 0

    brier = 0.0
    if holdout:
        for p, r in zip(holdout_probs, holdout):
            y_true = r.outcome_3way
            brier += sum((p[j] - (1 if j == y_true else 0)) ** 2 for j in range(3))
        brier /= len(holdout)
    hit_rate = hits / calls if calls else 0.0
    units = hits * (10 / 11) - (calls - hits) if calls else 0.0

    # Monthly breakdown — shared helper so 3-way gets partial_month on incomplete
    # final months just like the binary path does.
    selected_for_grade = [
        (float(conf), 1 if sel == row.outcome_3way else 0, row.date)
        for conf, sel, row in zip(ho_confidences, ho_selections, holdout)
        if conf >= threshold
    ]
    holdout_end = (
        max(date.fromisoformat(row.date) for _, _, row in zip(ho_confidences, ho_selections, holdout))
        if holdout
        else date.today()  # noqa: DTZ011 — holdout boundary is an ET game date, timezone N/A
    )
    monthly_list = _monthly_grade(selected_for_grade, holdout_end=holdout_end)

    every_month_positive = all(
        m["units_at_minus_110"] > 0 for m in monthly_list if m["qualification_status"] == "qualifying"
    )
    qualified = calls >= MINIMUM_CALLS and hit_rate >= QUALIFICATION_MINIMUM_HIT_RATE and every_month_positive

    return {
        "features": list(feature_names),
        "coefficients_by_class": {
            f"class_{k}": {name: round(float(c), 10) for name, c in zip(feature_names, model.coef_[k])}
            for k in range(3)
        },
        "intercept": [round(float(i), 10) for i in model.intercept_],
        "classes": ["away", "draw", "home"],
        "primary_65": {
            "status": "evaluated",
            "learned_threshold": threshold,
            "validation": val_stats,
            "locked_holdout": {
                "qualified": qualified,
                "calls": calls,
                "hits": hits,
                "hit_rate": round(hit_rate, 6),
                "units_at_minus_110": round(units, 6),
                "called_rate": round(calls / len(holdout), 6) if holdout else 0,
                "qualification_eligible": True,
                "failures": [] if qualified else ["below qualification gate"],
                "locked_holdout": True,
                "brier_score": round(brier, 6),
                "monthly_at_minus_110": monthly_list,
                "monthly_minimum_calls": MINIMUM_MONTHLY_CALLS,
                "every_called_month_positive_at_minus_110": every_month_positive,
                "every_qualifying_month_positive_at_minus_110": every_month_positive,
                "total_predictions": len(holdout),
                "selectivity": round(calls / len(holdout), 6) if holdout else 0,
            },
        },
    }


def _learn_threshold_from_confidence_hit(
    confidences: Sequence[float],
    hits: Sequence[int],
    *,
    target_hit_rate: float,
    minimum_calls: int,
) -> tuple[float, dict[str, Any]]:
    """Least-selective confidence threshold meeting a target hit rate.

    Deliberately NOT models.learned_market.learn_confidence_threshold: that
    helper assumes its ``probabilities`` argument is P(a specific class) and
    internally takes ``max(value, 1 - value)``. evaluate_variant_3way passes
    it an already-collapsed argmax confidence instead (which can be < 0.5 for
    a 3-way split), silently corrupting the hit label on exactly those rows.
    This operates directly on (confidence, hit) pairs, so it is correct
    regardless of how many outcome classes the confidence was collapsed from.
    """
    if len(confidences) != len(hits):
        raise ValueError("confidence/hit length mismatch")
    if not 0.5 < target_hit_rate < 1:
        raise ValueError("target_hit_rate must be between 0.5 and 1")
    for threshold in sorted(set(confidences)):
        selected = [hit for conf, hit in zip(confidences, hits, strict=True) if conf >= threshold]
        if len(selected) < minimum_calls:
            continue
        hit_rate = sum(selected) / len(selected)
        if hit_rate >= target_hit_rate:
            return threshold, {
                "validation_calls": len(selected),
                "validation_hit_rate": round(hit_rate, 6),
                "target_hit_rate": target_hit_rate,
                "minimum_calls": minimum_calls,
                "selectivity": round(len(selected) / len(confidences), 6) if confidences else 0,
            }
    raise ValueError(
        f"no confidence threshold reaches {target_hit_rate:.0%} hit rate with >= {minimum_calls} calls"
    )


def qualify_soccer_poisson_model(store: FeatureStore, minimum_history_games: int = 200) -> dict[str, Any]:
    """Walk-forward qualify the independent Poisson/Dixon-Coles 3-way soccer
    model (models.soccer.SoccerModel) on the same locked 60/20/20 date split
    and hit-rate/monthly-consistency gate as every other production model, so
    it can be judged on equal terms before any promotion decision.

    Unlike evaluate_variant/evaluate_variant_3way (a single sklearn .fit call
    on static per-row features), SoccerModel is walked forward day by day:
    it only ever sees games strictly before the day it is predicting, exactly
    mirroring how it would run in the live forecast pipeline.
    """
    from .models.soccer import SoccerModel, UpcomingMatch

    rows = build_walk_forward_rows(store, "soccer")
    _train, validation, holdout, _split = chronological_split(rows)
    validation_ids = {row.event_id for row in validation}
    holdout_ids = {row.event_id for row in holdout}

    games = store.load_games("soccer")
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for game in games:
        by_date[game.start.astimezone(EASTERN).date().isoformat()].append(game)
    dates = sorted(by_date)

    model = SoccerModel()
    history: list[GameRecord] = []
    val_confidences: list[float] = []
    val_hits: list[int] = []
    ho_confidences: list[float] = []
    ho_hits: list[int] = []
    ho_dates: list[str] = []

    for day in dates:
        day_games = by_date[day]
        if len(history) >= minimum_history_games:
            relevant = [
                game for game in day_games if game.event_id in validation_ids or game.event_id in holdout_ids
            ]
            if relevant:
                upcoming = [
                    UpcomingMatch(
                        game.event_id, game.start.isoformat(), game.away_team, game.home_team, "SOCCER"
                    )
                    for game in relevant
                ]
                predictions = {
                    prediction.event_id: prediction
                    for prediction in model.predict_games(history, upcoming)
                    if prediction.market_type == "moneyline"
                }
                for game in relevant:
                    prediction = predictions.get(game.event_id)
                    if prediction is None:
                        continue
                    probabilities = prediction.probabilities
                    if game.home_score > game.away_score:
                        true_outcome = "home"
                    elif game.home_score < game.away_score:
                        true_outcome = "away"
                    else:
                        true_outcome = "draw"
                    confidence = max(probabilities.values())
                    selection = max(probabilities, key=probabilities.get)
                    hit = 1 if selection == true_outcome else 0
                    if game.event_id in validation_ids:
                        val_confidences.append(confidence)
                        val_hits.append(hit)
                    else:
                        ho_confidences.append(confidence)
                        ho_hits.append(hit)
                        ho_dates.append(day)
        history.extend(day_games)

    try:
        threshold, val_stats = _learn_threshold_from_confidence_hit(
            val_confidences,
            val_hits,
            target_hit_rate=PRIMARY_THRESHOLD_TARGET_HIT_RATE,
            minimum_calls=MINIMUM_CALLS,
        )
    except ValueError as error:
        return {
            "model": SoccerModel.version,
            "classes": ["away", "draw", "home"],
            "primary_65": {"status": "no_validation_threshold", "reason": str(error)},
        }

    calls = hits = 0
    selected_for_grade: list[tuple[float, int, str]] = []
    for confidence, hit, day in zip(ho_confidences, ho_hits, ho_dates, strict=True):
        if confidence < threshold:
            continue
        calls += 1
        hits += hit
        selected_for_grade.append((confidence, hit, day))
    hit_rate = hits / calls if calls else 0.0
    units = hits * (10 / 11) - (calls - hits) if calls else 0.0
    holdout_end = (
        max(date.fromisoformat(day) for _, _, day in selected_for_grade)
        if selected_for_grade
        else date.today()  # noqa: DTZ011 — holdout boundary is an ET game date, timezone N/A
    )
    monthly_list = _monthly_grade(selected_for_grade, holdout_end=holdout_end)
    every_month_positive = all(
        month["units_at_minus_110"] > 0
        for month in monthly_list
        if month["qualification_status"] == "qualifying"
    )
    qualified = calls >= MINIMUM_CALLS and hit_rate >= QUALIFICATION_MINIMUM_HIT_RATE and every_month_positive

    return {
        "model": SoccerModel.version,
        "classes": ["away", "draw", "home"],
        "primary_65": {
            "status": "evaluated",
            "learned_threshold": threshold,
            "validation": val_stats,
            "locked_holdout": {
                "qualified": qualified,
                "calls": calls,
                "hits": hits,
                "hit_rate": round(hit_rate, 6),
                "units_at_minus_110": round(units, 6),
                "called_rate": round(calls / len(ho_confidences), 6) if ho_confidences else 0,
                "qualification_eligible": True,
                "failures": [] if qualified else ["below qualification gate"],
                "locked_holdout": True,
                "monthly_at_minus_110": monthly_list,
                "monthly_minimum_calls": MINIMUM_MONTHLY_CALLS,
                "every_called_month_positive_at_minus_110": every_month_positive,
                "every_qualifying_month_positive_at_minus_110": every_month_positive,
                "total_predictions": len(ho_confidences),
                "selectivity": round(calls / len(ho_confidences), 6) if ho_confidences else 0,
            },
        },
    }


def qualify_soccer_total_model(store: FeatureStore, minimum_history_games: int = 200) -> dict[str, Any]:
    """Walk-forward qualify the soccer model's full-game 2.5-goal TOTAL market
    on the same locked 60/20/20 date split and hit-rate/monthly-consistency
    gate as ``qualify_soccer_poisson_model`` above.

    Deliberately separate from that function rather than a shared helper:
    the two markets have different outcome definitions (3-way win/draw/loss
    vs. binary over/under) and, more importantly, different confidence
    semantics -- moneyline's natural confidence is the 3-way argmax
    probability, while total's is ``abs(p_over - 0.5)``, the SAME quantity
    ``evaluate_gated_research_eligibility``'s ``minimum_confidence`` check
    actually gates on for every non-moneyline contract. Totals is soccer's
    primary priced market (build_soccer_total_slate's own name, and the only
    one with real historical Polymarket depth) so this is the qualification
    that actually matters for tuning that gate, not the moneyline one.
    """
    from .models.soccer import SoccerModel, UpcomingMatch

    rows = build_walk_forward_rows(store, "soccer")
    _train, validation, holdout, _split = chronological_split(rows)
    validation_ids = {row.event_id for row in validation}
    holdout_ids = {row.event_id for row in holdout}

    games = store.load_games("soccer")
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for game in games:
        by_date[game.start.astimezone(EASTERN).date().isoformat()].append(game)
    dates = sorted(by_date)

    model = SoccerModel()
    history: list[GameRecord] = []
    val_confidences: list[float] = []
    val_hits: list[int] = []
    ho_confidences: list[float] = []
    ho_hits: list[int] = []
    ho_dates: list[str] = []

    for day in dates:
        day_games = by_date[day]
        if len(history) >= minimum_history_games:
            relevant = [
                game for game in day_games if game.event_id in validation_ids or game.event_id in holdout_ids
            ]
            if relevant:
                upcoming = [
                    UpcomingMatch(
                        game.event_id, game.start.isoformat(), game.away_team, game.home_team, "SOCCER"
                    )
                    for game in relevant
                ]
                predictions = {
                    prediction.event_id: prediction
                    for prediction in model.predict_games(history, upcoming)
                    if prediction.market_type == "total"
                }
                for game in relevant:
                    prediction = predictions.get(game.event_id)
                    if prediction is None:
                        continue
                    p_over = prediction.probabilities["over"]
                    true_over = 1 if (game.home_score + game.away_score) > 2.5 else 0
                    selection_over = p_over >= 0.5
                    hit = 1 if selection_over == bool(true_over) else 0
                    confidence = abs(p_over - 0.5)
                    if game.event_id in validation_ids:
                        val_confidences.append(confidence)
                        val_hits.append(hit)
                    else:
                        ho_confidences.append(confidence)
                        ho_hits.append(hit)
                        ho_dates.append(day)
        history.extend(day_games)

    try:
        threshold, val_stats = _learn_threshold_from_confidence_hit(
            val_confidences,
            val_hits,
            target_hit_rate=PRIMARY_THRESHOLD_TARGET_HIT_RATE,
            minimum_calls=MINIMUM_CALLS,
        )
    except ValueError as error:
        return {
            "model": SoccerModel.version,
            "market": "total_2.5",
            "primary_65": {"status": "no_validation_threshold", "reason": str(error)},
        }

    calls = hits = 0
    selected_for_grade: list[tuple[float, int, str]] = []
    for confidence, hit, day in zip(ho_confidences, ho_hits, ho_dates, strict=True):
        if confidence < threshold:
            continue
        calls += 1
        hits += hit
        selected_for_grade.append((confidence, hit, day))
    hit_rate = hits / calls if calls else 0.0
    units = hits * (10 / 11) - (calls - hits) if calls else 0.0
    holdout_end = (
        max(date.fromisoformat(day) for _, _, day in selected_for_grade)
        if selected_for_grade
        else date.today()  # noqa: DTZ011 — holdout boundary is an ET game date, timezone N/A
    )
    monthly_list = _monthly_grade(selected_for_grade, holdout_end=holdout_end)
    every_month_positive = all(
        month["units_at_minus_110"] > 0
        for month in monthly_list
        if month["qualification_status"] == "qualifying"
    )
    qualified = calls >= MINIMUM_CALLS and hit_rate >= QUALIFICATION_MINIMUM_HIT_RATE and every_month_positive

    return {
        "model": SoccerModel.version,
        "market": "total_2.5",
        "primary_65": {
            "status": "evaluated",
            "learned_threshold": threshold,
            "validation": val_stats,
            "locked_holdout": {
                "qualified": qualified,
                "calls": calls,
                "hits": hits,
                "hit_rate": round(hit_rate, 6),
                "units_at_minus_110": round(units, 6),
                "called_rate": round(calls / len(ho_confidences), 6) if ho_confidences else 0,
                "qualification_eligible": True,
                "failures": [] if qualified else ["below qualification gate"],
                "locked_holdout": True,
                "monthly_at_minus_110": monthly_list,
                "monthly_minimum_calls": MINIMUM_MONTHLY_CALLS,
                "every_called_month_positive_at_minus_110": every_month_positive,
                "every_qualifying_month_positive_at_minus_110": every_month_positive,
                "total_predictions": len(ho_confidences),
                "selectivity": round(calls / len(ho_confidences), 6) if ho_confidences else 0,
            },
        },
    }


def qualify_tennis_elo_model(data_root: str | Path, minimum_history_matches: int = 200) -> dict[str, Any]:
    """Walk-forward qualify TennisModel's surface-blended Elo, same locked
    60/20/20 date split and hit-rate/monthly-consistency gate as
    ``qualify_soccer_poisson_model``/``qualify_soccer_total_model`` above.

    Self-contained rather than reusing ``build_walk_forward_rows``/
    ``chronological_split``: those are tied to ``FeatureStore.load_games``'s
    ``GameRecord`` shape, which tennis rows are structurally incompatible
    with (player-vs-player winner/loser, no scores -- see
    ``tennis_forward._tennis_history_before``'s docstring for the same
    incompatibility that silently zeroed every point-in-time tennis feature
    before that was fixed). Reads ``data/processed/tennis/games.jsonl``
    directly instead.

    Binary market (no draw), so confidence is ``abs(p_one - 0.5)`` --
    already the SAME quantity ``evaluate_gated_research_eligibility``'s
    ``minimum_confidence`` checks, unlike soccer moneyline's 3-way argmax.
    """
    from .models.tennis import TennisModel, UpcomingMatch

    path = Path(data_root) / "processed" / "tennis" / "games.jsonl"
    rows: list[dict[str, Any]] = []
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    row["_date"] = str(row["event_start_utc"])[:10]
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
                rows.append(row)
    if not rows:
        return {
            "model": TennisModel.version,
            "primary_65": {"status": "no_validation_threshold", "reason": "no history"},
        }

    dates = sorted({row["_date"] for row in rows})
    if len(dates) < 5:
        return {
            "model": TennisModel.version,
            "primary_65": {
                "status": "no_validation_threshold",
                "reason": "fewer than five distinct match dates",
            },
        }
    train_count = max(1, math.floor(len(dates) * 0.60))
    validation_count = max(1, math.floor(len(dates) * 0.20))
    holdout_start_index = min(train_count + validation_count, len(dates) - 1)
    validation_start = dates[train_count]
    holdout_start = dates[holdout_start_index]
    validation_ids = {row["event_id"] for row in rows if validation_start <= row["_date"] < holdout_start}
    holdout_ids = {row["event_id"] for row in rows if row["_date"] >= holdout_start}

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[row["_date"]].append(row)

    model = TennisModel()
    history: list[dict[str, Any]] = []
    val_confidences: list[float] = []
    val_hits: list[int] = []
    ho_confidences: list[float] = []
    ho_hits: list[int] = []
    ho_dates: list[str] = []

    for day in dates:
        day_rows = by_date[day]
        if len(history) >= minimum_history_matches:
            relevant = [
                row for row in day_rows if row["event_id"] in validation_ids or row["event_id"] in holdout_ids
            ]
            if relevant:
                # player_one is always the real winner here -- the model
                # doesn't care about upcoming-match slot order (Elo lookup is
                # by name, not position), and fixing it this way turns "did
                # the model favor player_one" directly into the hit label.
                upcoming = [
                    UpcomingMatch(
                        str(row["event_id"]),
                        str(row["event_start_utc"]),
                        str(row["winner"]),
                        str(row["loser"]),
                        str(row.get("surface", "Hard")),
                        str(row.get("league", "ATP")),
                    )
                    for row in relevant
                ]
                predictions = {
                    prediction.event_id: prediction for prediction in model.predict_games(history, upcoming)
                }
                for row in relevant:
                    prediction = predictions.get(str(row["event_id"]))
                    if prediction is None:
                        continue
                    p_one = prediction.probabilities["away"]
                    hit = 1 if p_one >= 0.5 else 0
                    confidence = abs(p_one - 0.5)
                    if row["event_id"] in validation_ids:
                        val_confidences.append(confidence)
                        val_hits.append(hit)
                    else:
                        ho_confidences.append(confidence)
                        ho_hits.append(hit)
                        ho_dates.append(day)
        history.extend(day_rows)

    try:
        threshold, val_stats = _learn_threshold_from_confidence_hit(
            val_confidences,
            val_hits,
            target_hit_rate=PRIMARY_THRESHOLD_TARGET_HIT_RATE,
            minimum_calls=MINIMUM_CALLS,
        )
    except ValueError as error:
        return {
            "model": TennisModel.version,
            "primary_65": {"status": "no_validation_threshold", "reason": str(error)},
        }

    calls = hits = 0
    selected_for_grade: list[tuple[float, int, str]] = []
    for confidence, hit, day in zip(ho_confidences, ho_hits, ho_dates, strict=True):
        if confidence < threshold:
            continue
        calls += 1
        hits += hit
        selected_for_grade.append((confidence, hit, day))
    hit_rate = hits / calls if calls else 0.0
    units = hits * (10 / 11) - (calls - hits) if calls else 0.0
    holdout_end = (
        max(date.fromisoformat(day) for _, _, day in selected_for_grade)
        if selected_for_grade
        else date.today()  # noqa: DTZ011 — holdout boundary is an ET game date, timezone N/A
    )
    monthly_list = _monthly_grade(selected_for_grade, holdout_end=holdout_end)
    every_month_positive = all(
        month["units_at_minus_110"] > 0
        for month in monthly_list
        if month["qualification_status"] == "qualifying"
    )
    qualified = calls >= MINIMUM_CALLS and hit_rate >= QUALIFICATION_MINIMUM_HIT_RATE and every_month_positive

    return {
        "model": TennisModel.version,
        "primary_65": {
            "status": "evaluated",
            "learned_threshold": threshold,
            "validation": val_stats,
            "locked_holdout": {
                "qualified": qualified,
                "calls": calls,
                "hits": hits,
                "hit_rate": round(hit_rate, 6),
                "units_at_minus_110": round(units, 6),
                "called_rate": round(calls / len(ho_confidences), 6) if ho_confidences else 0,
                "qualification_eligible": True,
                "failures": [] if qualified else ["below qualification gate"],
                "locked_holdout": True,
                "monthly_at_minus_110": monthly_list,
                "monthly_minimum_calls": MINIMUM_MONTHLY_CALLS,
                "every_called_month_positive_at_minus_110": every_month_positive,
                "every_qualifying_month_positive_at_minus_110": every_month_positive,
                "total_predictions": len(ho_confidences),
                "selectivity": round(calls / len(ho_confidences), 6) if ho_confidences else 0,
            },
        },
    }


def _fit(rows: Sequence[ValidationRow], feature_names: Sequence[str]) -> LogisticRegression:
    model = LogisticRegression(max_iter=2_000, solver="lbfgs")
    model.fit(_matrix(rows, feature_names), [row.outcome for row in rows])
    return model


def _matrix(rows: Sequence[ValidationRow], feature_names: Sequence[str]) -> list[list[float]]:
    return [[float(getattr(row, name)) for name in feature_names] for row in rows]


def _predict(
    model: LogisticRegression,
    rows: Sequence[ValidationRow],
    feature_names: Sequence[str],
) -> list[float]:
    return [float(item[1]) for item in model.predict_proba(_matrix(rows, feature_names))]


def _learn_and_grade(
    validation_probabilities: Sequence[float],
    validation: Sequence[ValidationRow],
    holdout_probabilities: Sequence[float],
    holdout: Sequence[ValidationRow],
    *,
    target_hit_rate: float,
    fixed_threshold: float | None = None,
) -> dict[str, Any]:
    """Learn a threshold from validation, or reuse one already pinned.

    ``fixed_threshold``, when given, skips ``learn_confidence_threshold``
    entirely and grades the locked holdout directly against that value --
    lets a reproduction replay plug in a production artifact's own recorded
    ``confidence_threshold`` instead of deriving a fresh one from today's
    validation cohort. Default ``None`` preserves current behavior exactly.
    """
    if fixed_threshold is not None:
        threshold = fixed_threshold
        validation_stats: dict[str, Any] = {
            "target_hit_rate": target_hit_rate,
            "minimum_calls": MINIMUM_CALLS,
            "source": "fixed_threshold_replay",
        }
    else:
        try:
            threshold, validation_stats = learn_confidence_threshold(
                validation_probabilities,
                [row.outcome for row in validation],
                target_hit_rate=target_hit_rate,
                minimum_calls=MINIMUM_CALLS,
            )
        except ValueError as error:
            return {
                "status": "no_validation_threshold",
                "target_hit_rate": target_hit_rate,
                "minimum_calls": MINIMUM_CALLS,
                "reason": str(error),
            }
    return {
        "status": "evaluated",
        "learned_threshold": threshold,
        "validation": validation_stats,
        "locked_holdout": _grade(
            holdout_probabilities,
            holdout,
            threshold,
            qualification_eligible=target_hit_rate == PRIMARY_THRESHOLD_TARGET_HIT_RATE,
        ),
    }


def _learn_and_grade_agreement(
    validation_elo: Sequence[float],
    validation_trend: Sequence[float],
    validation: Sequence[ValidationRow],
    holdout_elo: Sequence[float],
    holdout_trend: Sequence[float],
    holdout: Sequence[ValidationRow],
    target_hit_rate: float,
) -> dict[str, Any]:
    development = _agreement_probabilities(validation_elo, validation_trend, validation)
    if len(development[0]) < MINIMUM_CALLS:
        return {
            "status": "insufficient_agreement_calls_in_validation",
            "agreement_rows": len(development[0]),
            "minimum_calls": MINIMUM_CALLS,
        }
    try:
        threshold, validation_stats = learn_confidence_threshold(
            development[0],
            development[1],
            target_hit_rate=target_hit_rate,
            minimum_calls=MINIMUM_CALLS,
        )
    except ValueError as error:
        return {
            "status": "no_validation_threshold",
            "target_hit_rate": target_hit_rate,
            "minimum_calls": MINIMUM_CALLS,
            "reason": str(error),
        }
    evaluation = _agreement_probabilities(holdout_elo, holdout_trend, holdout)
    synthetic_rows = [
        ValidationRow(game_date, str(index), outcome, probability, 0, 1, 1, False, False)
        for index, (probability, outcome, game_date) in enumerate(
            zip(evaluation[0], evaluation[1], evaluation[2], strict=True)
        )
    ]
    return {
        "status": "evaluated",
        "learned_threshold": threshold,
        "validation": {**validation_stats, "agreement_rows": len(development[0])},
        "locked_holdout": _grade(
            evaluation[0],
            synthetic_rows,
            threshold,
            qualification_eligible=target_hit_rate == PRIMARY_THRESHOLD_TARGET_HIT_RATE,
        ),
        "holdout_agreement_rows": len(evaluation[0]),
    }


def _agreement_probabilities(
    elo_probabilities: Sequence[float],
    trend_probabilities: Sequence[float],
    rows: Sequence[ValidationRow],
) -> tuple[list[float], list[int], list[str]]:
    probabilities: list[float] = []
    outcomes: list[int] = []
    dates: list[str] = []
    for elo_probability, trend_probability, row in zip(
        elo_probabilities, trend_probabilities, rows, strict=True
    ):
        elo_home = elo_probability >= 0.5
        trend_home = trend_probability >= 0.5
        if elo_home != trend_home:
            continue
        confidence = min(
            max(elo_probability, 1 - elo_probability),
            max(trend_probability, 1 - trend_probability),
        )
        probabilities.append(confidence if elo_home else 1 - confidence)
        outcomes.append(row.outcome)
        dates.append(row.date)
    return probabilities, outcomes, dates


def _grade(
    probabilities: Sequence[float],
    rows: Sequence[ValidationRow],
    threshold: float,
    *,
    qualification_eligible: bool,
) -> dict[str, Any]:
    selected: list[tuple[float, int, str]] = []
    for probability, row in zip(probabilities, rows, strict=True):
        confidence = max(probability, 1 - probability)
        if confidence < threshold:
            continue
        selected_outcome = row.outcome if probability >= 0.5 else 1 - row.outcome
        selected.append((confidence, selected_outcome, row.date))
    calls = len(selected)
    hits = sum(outcome for _, outcome, _ in selected)
    brier = (
        sum((probability - outcome) ** 2 for probability, outcome, _ in selected) / calls if calls else None
    )
    calibration = (
        calibration_metrics(
            [probability for probability, _, _ in selected],
            [outcome for _, outcome, _ in selected],
        )
        if calls
        else None
    )
    qualification = evaluate_locked_holdout(
        calls=calls,
        hits=hits,
        total_predictions=len(rows),
        locked_holdout=True,
        brier_score=round(brier, 6) if brier is not None else None,
        calibration=calibration,
        roi=None,
    ).to_dict()
    result = {
        **qualification,
        "meets_primary_holdout_metrics": qualification["qualified"],
        "qualification_eligible": qualification_eligible,
        "called_rate": round(calls / len(rows), 6) if rows else None,
        "units_at_minus_110": round(hits * (10 / 11) - (calls - hits), 6),
        "monthly_at_minus_110": _monthly_grade(
            selected,
            holdout_end=max(date.fromisoformat(row.date) for row in rows),
        ),
        "monthly_minimum_calls": MINIMUM_MONTHLY_CALLS,
    }
    qualifying_months = [
        month for month in result["monthly_at_minus_110"] if month["qualification_status"] == "qualifying"
    ]
    result["every_qualifying_month_positive_at_minus_110"] = bool(qualifying_months) and all(
        month["units_at_minus_110"] > 0 for month in qualifying_months
    )
    # Backwards-compatible alias; its meaning now follows the documented
    # minimum-sample and complete-month policy.
    result["every_called_month_positive_at_minus_110"] = result[
        "every_qualifying_month_positive_at_minus_110"
    ]
    if qualification_eligible and not result["every_qualifying_month_positive_at_minus_110"]:
        failed_months = [month["month"] for month in qualifying_months if month["units_at_minus_110"] <= 0]
        result["qualified"] = False
        result["meets_primary_holdout_metrics"] = False
        result["failures"] = [
            *result["failures"],
            (
                f"non-positive qualifying months at -110: {', '.join(failed_months)}"
                if failed_months
                else "no complete month reached the 10-call qualification minimum"
            ),
        ]
    if not qualification_eligible:
        result["qualified"] = False
    return result


def _monthly_grade(
    selected: Sequence[tuple[float, int, str]],
    *,
    holdout_end: date,
) -> list[dict[str, Any]]:
    by_month: dict[str, list[int]] = defaultdict(list)
    for _, outcome, game_date in selected:
        by_month[game_date[:7]].append(outcome)
    output = []
    for month, outcomes in sorted(by_month.items()):
        calls = len(outcomes)
        hits = sum(outcomes)
        year, month_number = (int(value) for value in month.split("-"))
        month_end = date(year, month_number, calendar.monthrange(year, month_number)[1])
        if holdout_end < month_end:
            status = "partial_month"
        elif calls < MINIMUM_MONTHLY_CALLS:
            status = "insufficient_calls"
        else:
            status = "qualifying"
        output.append(
            {
                "month": month,
                "calls": calls,
                "hits": hits,
                "hit_rate": round(hits / calls, 6),
                "units_at_minus_110": round(hits * (10 / 11) - (calls - hits), 6),
                "qualification_status": status,
            }
        )
    return output


def _feature_decisions(variants: dict[str, dict[str, Any]], sport: str) -> list[dict[str, Any]]:
    if sport.lower() != "mlb":
        return []
    decisions = []
    pairs = [
        ("trend_gap", "elo_only", "elo_trend"),
        ("adaptive_hfa", "elo_trend", "elo_trend_adaptive_hfa"),
        ("park_factor", "elo_trend", "elo_trend_park"),
        ("weather_factor", "elo_trend_park", "elo_trend_park_weather"),
    ]
    for feature, baseline_name, candidate_name in pairs:
        baseline, candidate = _paired_comparison_metrics(variants[baseline_name], variants[candidate_name])
        if feature == "adaptive_hfa":
            if baseline is None or candidate is None:
                action = "RESEARCH_ONLY_INSUFFICIENT_SELECTIVE_SAMPLE"
                reason = "no comparable 50-call result"
            elif candidate["hit_rate"] > baseline["hit_rate"]:
                action = "RESEARCH_ONLY_FRESH_HOLDOUT_REQUIRED"
                reason = "improved the already-opened holdout; promotion requires new outcomes"
            else:
                action = "REJECT_NO_HIT_RATE_GAIN"
                reason = "did not improve selective holdout hit rate"
        elif feature == "weather_factor":
            action = "REJECT_UNAVAILABLE"
            reason = "zero point-in-time weather coverage and zero feature variance"
        elif feature == "park_factor":
            if baseline is None or candidate is None:
                action = "REJECT_INSUFFICIENT_SELECTIVE_SAMPLE"
                reason = "no comparable 50-call locked-holdout result"
            elif candidate["hit_rate"] <= baseline["hit_rate"]:
                action = "REJECT_NO_HIT_RATE_GAIN"
                reason = "did not improve hit rate; static table is also not archived point-in-time"
            else:
                action = "DIAGNOSTIC_ONLY"
                reason = "improved hit rate, but static table is not archived point-in-time"
        elif baseline is None or candidate is None:
            action = "REJECT_INSUFFICIENT_SELECTIVE_SAMPLE"
            reason = "no comparable 50-call locked-holdout result"
        elif candidate["hit_rate"] > baseline["hit_rate"]:
            action = "RETAIN"
            reason = "improved selective locked-holdout hit rate"
        else:
            action = "REJECT_NO_HIT_RATE_GAIN"
            reason = "did not improve selective locked-holdout hit rate"
        decisions.append(
            {
                "feature": feature,
                "action": action,
                "reason": reason,
                "baseline": baseline,
                "candidate": candidate,
            }
        )
    return decisions


def _paired_comparison_metrics(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for tier in ("primary_65", "diagnostic_60"):
        baseline_evaluation = baseline[tier]
        candidate_evaluation = candidate[tier]
        if (
            baseline_evaluation.get("status") != "evaluated"
            or candidate_evaluation.get("status") != "evaluated"
        ):
            continue
        baseline_holdout = baseline_evaluation["locked_holdout"]
        candidate_holdout = candidate_evaluation["locked_holdout"]
        if baseline_holdout["calls"] >= MINIMUM_CALLS and candidate_holdout["calls"] >= MINIMUM_CALLS:
            return (
                {
                    "tier": tier,
                    "calls": baseline_holdout["calls"],
                    "hit_rate": baseline_holdout["hit_rate"],
                    "units_at_minus_110": baseline_holdout["units_at_minus_110"],
                },
                {
                    "tier": tier,
                    "calls": candidate_holdout["calls"],
                    "hit_rate": candidate_holdout["hit_rate"],
                    "units_at_minus_110": candidate_holdout["units_at_minus_110"],
                },
            )
    return None, None


def _agreement_comparison(single: dict[str, Any], agreement: dict[str, Any]) -> dict[str, Any]:
    for tier in ("primary_65", "diagnostic_60"):
        single_evaluation = single[tier]
        agreement_evaluation = agreement[tier]
        if (
            single_evaluation.get("status") != "evaluated"
            or agreement_evaluation.get("status") != "evaluated"
        ):
            continue
        single_holdout = single_evaluation["locked_holdout"]
        agreement_holdout = agreement_evaluation["locked_holdout"]
        if single_holdout["calls"] >= MINIMUM_CALLS and agreement_holdout["calls"] >= MINIMUM_CALLS:
            return {
                "tier": tier,
                "single_model": {
                    "calls": single_holdout["calls"],
                    "hit_rate": single_holdout["hit_rate"],
                },
                "agreement": {
                    "calls": agreement_holdout["calls"],
                    "hit_rate": agreement_holdout["hit_rate"],
                },
                "improves_hit_rate": agreement_holdout["hit_rate"] > single_holdout["hit_rate"],
                "qualifies": agreement_holdout["qualified"],
            }
    return {"status": "no_same-tier_50-call_comparison"}


def _cohort_metadata(rows: Sequence[ValidationRow]) -> dict[str, Any]:
    return {
        "start": rows[0].date,
        "end": rows[-1].date,
        "observations": len(rows),
    }


# ── Rolling metrics for validation ────────────────────────────────────────

_WEATHER_DB: dict | None = None


def _lookup_weather(home_team: str, game_date: str) -> dict:
    """Look up historical weather for a ballpark on a given date."""
    global _WEATHER_DB
    import json as _json

    if _WEATHER_DB is None:
        db_path = PROJECT_ROOT / "data/features/historical_weather.json"
        if db_path.exists():
            _WEATHER_DB = _json.loads(db_path.read_text())
        else:
            _WEATHER_DB = {}

    park_data = _WEATHER_DB.get(home_team, {})
    if isinstance(park_data, dict) and park_data.get("dome"):
        return {"run_factor": 1.0, "available": True}

    day_data = park_data.get(game_date) if isinstance(park_data, dict) else None
    if day_data:
        return {
            "run_factor": day_data.get("run_factor", 1.0),
            "temp": day_data.get("temp"),
            "wind": day_data.get("wind"),
            "available": True,
        }
    return {"run_factor": 1.0, "available": False}


# ── Starter ERA gap from MLB Stats API snapshots ─────────────────────────

_STARTER_ERA_MAP: dict[str, float] | None = None


def _load_starter_era_map() -> dict[str, float]:
    """Build point-in-time starter ERA gap map from mlb_statsapi snapshots.

    Returns dict of event_id → (home_starter_era - away_starter_era).
    Computed from rolling 5-start ERA for each confirmed starting pitcher.
    History is built strictly chronologically — the current game's stats
    are NOT included in its own ERA computation."""
    global _STARTER_ERA_MAP
    if _STARTER_ERA_MAP is not None:
        return _STARTER_ERA_MAP

    import json as _json

    snap_path = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
    crosswalk_path = PROJECT_ROOT / "data/processed/mlb/games.jsonl"
    if not snap_path.exists() or not crosswalk_path.exists():
        _STARTER_ERA_MAP = {}
        return _STARTER_ERA_MAP

    def _ip_float(v):
        w, _, f = v.partition(".")
        return int(w) + {"0": 0.0, "1": 1 / 3, "2": 2 / 3}.get(f, 0.0)

    # Load crosswalk: (time, home, away) → event_id
    crosswalk = {}
    with crosswalk_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            g = _json.loads(line)
            crosswalk[(g["event_start_utc"][:16], g["home_team"], g["away_team"])] = g["event_id"]

    # Load snapshots, build point-in-time ERA history
    with snap_path.open(encoding="utf-8") as f:
        snaps = [_json.loads(line) for line in f if line.strip()]
    snaps.sort(key=lambda r: r["game_start_utc"])

    history: dict[int, list[dict]] = {}
    result: dict[str, float] = {}

    for snap in snaps:
        key = (snap["game_start_utc"][:16], snap["home"]["team_name"], snap["away"]["team_name"])
        eid = crosswalk.get(key)
        if not eid:
            continue

        home_era = away_era = None
        for side_key, side_data in [("home", snap["home"]), ("away", snap["away"])]:
            order = side_data.get("pitcher_order") or []
            if not order:
                continue
            pid = order[0]
            player = next((p for p in side_data["players"] if p["player_id"] == pid), None)
            if not player or "inningsPitched" not in player.get("pitching", {}):
                continue
            stats = player["pitching"]
            ip = _ip_float(stats["inningsPitched"])
            er = int(stats.get("earnedRuns", 0))

            prior = history.get(pid, [])
            if len(prior) >= 2:
                era = sum(g["er"] for g in prior[-5:]) / max(0.001, sum(g["ip"] for g in prior[-5:])) * 9
                if side_key == "home":
                    home_era = era
                else:
                    away_era = era

            # Update history AFTER computing this game's feature (point-in-time)
            history.setdefault(pid, []).append({"ip": ip, "er": er})

        if home_era is not None and away_era is not None:
            result[eid] = round(home_era - away_era, 6)

    _STARTER_ERA_MAP = result
    return result


def _starter_era_gap(event_id: str) -> float:
    """Get the real starter ERA gap for a given event, or 0.0 if unavailable."""
    return _load_starter_era_map().get(event_id, 0.0)


# ── Starter FIP gap (same methodology, FIP instead of ERA) ──────────────

_STARTER_FIP_MAP: dict[str, float] | None = None
_FIP_CONSTANT = 3.10


def _load_starter_fip_map() -> dict[str, float]:
    """Build point-in-time starter FIP gap map from mlb_statsapi snapshots.

    Mirrors ``_load_starter_era_map`` exactly — same chronological point-in-time
    logic, same rolling 5-start window, same >=2 prior starts minimum, same
    crosswalk — but stores FIP components (SO, BB, HR, HBP) alongside IP and
    computes FIP instead of ERA."""
    global _STARTER_FIP_MAP
    if _STARTER_FIP_MAP is not None:
        return _STARTER_FIP_MAP

    import json as _json

    snap_path = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
    crosswalk_path = PROJECT_ROOT / "data/processed/mlb/games.jsonl"
    if not snap_path.exists() or not crosswalk_path.exists():
        _STARTER_FIP_MAP = {}
        return _STARTER_FIP_MAP

    def _ip_float(v):
        w, _, f = v.partition(".")
        return int(w) + {"0": 0.0, "1": 1 / 3, "2": 2 / 3}.get(f, 0.0)

    crosswalk = {}
    with crosswalk_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            g = _json.loads(line)
            crosswalk[(g["event_start_utc"][:16], g["home_team"], g["away_team"])] = g["event_id"]

    with snap_path.open(encoding="utf-8") as f:
        snaps = [_json.loads(line) for line in f if line.strip()]
    snaps.sort(key=lambda r: r["game_start_utc"])

    history: dict[int, list[dict]] = {}
    result: dict[str, float] = {}

    for snap in snaps:
        key = (snap["game_start_utc"][:16], snap["home"]["team_name"], snap["away"]["team_name"])
        eid = crosswalk.get(key)
        if not eid:
            continue

        home_fip = away_fip = None
        for side_key, side_data in [("home", snap["home"]), ("away", snap["away"])]:
            order = side_data.get("pitcher_order") or []
            if not order:
                continue
            pid = order[0]
            player = next((p for p in side_data["players"] if p["player_id"] == pid), None)
            if not player or "inningsPitched" not in player.get("pitching", {}):
                continue
            stats = player["pitching"]
            ip = _ip_float(stats["inningsPitched"])
            so = int(stats.get("strikeOuts", 0) or 0)
            bb = int(stats.get("baseOnBalls", 0) or 0)
            hr = int(stats.get("homeRuns", 0) or 0)
            hbp = int(stats.get("hitByPitch", 0) or stats.get("hitBatsmen", 0) or 0)

            prior = history.get(pid, [])
            if len(prior) >= 2:
                recent = prior[-5:]
                pip = sum(g["ip"] for g in recent)
                if pip > 0:
                    fip = (
                        (
                            13 * sum(g["hr"] for g in recent)
                            + 3 * (sum(g["bb"] for g in recent) + sum(g["hbp"] for g in recent))
                            - 2 * sum(g["so"] for g in recent)
                        )
                        / pip
                    ) + _FIP_CONSTANT
                    if side_key == "home":
                        home_fip = fip
                    else:
                        away_fip = fip

            history.setdefault(pid, []).append({"ip": ip, "so": so, "bb": bb, "hr": hr, "hbp": hbp})

        if home_fip is not None and away_fip is not None:
            result[eid] = round(home_fip - away_fip, 6)

    _STARTER_FIP_MAP = result
    return result


def _starter_fip_gap(event_id: str) -> float:
    """Get the real starter FIP gap for a given event, or 0.0 if unavailable."""
    return _load_starter_fip_map().get(event_id, 0.0)


# ── Starter K-BB% gap (same methodology, (K-BB)/BF instead of ERA/FIP) ───

_STARTER_KBB_MAP: dict[str, float] | None = None


def _load_starter_kbb_map() -> dict[str, float]:
    """Build point-in-time starter K-BB% gap map from mlb_statsapi snapshots.

    Mirrors ``_load_starter_fip_map`` exactly — same chronological point-in-time
    logic, same rolling 5-start window, same >=2 prior starts minimum, same
    crosswalk — but computes real K-BB% = (K-BB)/battersFaced instead of FIP.

    F-real (2026-08-20): this previously computed (K-BB)/IP -- K-BB per
    inning, not K-BB% -- because batters_faced wasn't carried in the
    per-start history dict below. The closed ``starter_kbb: REJECT``
    verdict was measured against that wrong statistic; see
    MODEL_IMPROVEMENTS.md section 8 for the corrected rerun."""
    global _STARTER_KBB_MAP
    if _STARTER_KBB_MAP is not None:
        return _STARTER_KBB_MAP

    import json as _json

    snap_path = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
    crosswalk_path = PROJECT_ROOT / "data/processed/mlb/games.jsonl"
    if not snap_path.exists() or not crosswalk_path.exists():
        _STARTER_KBB_MAP = {}
        return _STARTER_KBB_MAP

    def _ip_float(v):
        w, _, f = v.partition(".")
        return int(w) + {"0": 0.0, "1": 1 / 3, "2": 2 / 3}.get(f, 0.0)

    crosswalk = {}
    with crosswalk_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            g = _json.loads(line)
            crosswalk[(g["event_start_utc"][:16], g["home_team"], g["away_team"])] = g["event_id"]

    with snap_path.open(encoding="utf-8") as f:
        snaps = [_json.loads(line) for line in f if line.strip()]
    snaps.sort(key=lambda r: r["game_start_utc"])

    history: dict[int, list[dict]] = {}
    result: dict[str, float] = {}

    for snap in snaps:
        key = (snap["game_start_utc"][:16], snap["home"]["team_name"], snap["away"]["team_name"])
        eid = crosswalk.get(key)
        if not eid:
            continue

        home_kbb = away_kbb = None
        for side_key, side_data in [("home", snap["home"]), ("away", snap["away"])]:
            order = side_data.get("pitcher_order") or []
            if not order:
                continue
            pid = order[0]
            player = next((p for p in side_data["players"] if p["player_id"] == pid), None)
            if not player or "inningsPitched" not in player.get("pitching", {}):
                continue
            stats = player["pitching"]
            so = int(stats.get("strikeOuts", 0) or 0)
            bb = int(stats.get("baseOnBalls", 0) or 0)
            bf = int(stats.get("battersFaced", 0) or 0)

            prior = history.get(pid, [])
            if len(prior) >= 2:
                recent = prior[-5:]
                pbf = sum(g["bf"] for g in recent)
                if pbf > 0:
                    kbb = (sum(g["so"] for g in recent) - sum(g["bb"] for g in recent)) / pbf
                    if side_key == "home":
                        home_kbb = kbb
                    else:
                        away_kbb = kbb

            history.setdefault(pid, []).append({"bf": bf, "so": so, "bb": bb})

        if home_kbb is not None and away_kbb is not None:
            result[eid] = round(home_kbb - away_kbb, 6)

    _STARTER_KBB_MAP = result
    return result


def _starter_kbb_gap(event_id: str) -> float:
    """Get the real starter K-BB% gap for a given event, or 0.0 if unavailable."""
    return _load_starter_kbb_map().get(event_id, 0.0)


# ── Bullpen weakness gap from MLB Stats API snapshots ────────────────────

_BULLPEN_MAP: dict[str, tuple[float, bool]] | None = None


def _load_bullpen_map() -> dict[str, tuple[float, bool]]:
    """Build point-in-time bullpen weakness gap map from mlb_statsapi snapshots.

    Returns dict of event_id → (home_weakness_index - away_weakness_index, available).
    Computed from each team's rolling relief performance (via
    features.bullpen.bullpen_profile) over its last 5 games with a bullpen
    appearance. History is built strictly chronologically — the current
    game's relievers are NOT included in its own team's pregame bullpen
    history, mirroring `_load_starter_era_map`."""
    global _BULLPEN_MAP
    if _BULLPEN_MAP is not None:
        return _BULLPEN_MAP

    import json as _json

    snap_path = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
    crosswalk_path = PROJECT_ROOT / "data/processed/mlb/games.jsonl"
    if not snap_path.exists() or not crosswalk_path.exists():
        _BULLPEN_MAP = {}
        return _BULLPEN_MAP

    def _ip_float(v):
        w, _, f = v.partition(".")
        return int(w) + {"0": 0.0, "1": 1 / 3, "2": 2 / 3}.get(f, 0.0)

    # Load crosswalk: (time, home, away) → event_id
    crosswalk = {}
    with crosswalk_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            g = _json.loads(line)
            crosswalk[(g["event_start_utc"][:16], g["home_team"], g["away_team"])] = g["event_id"]

    # Load snapshots, build point-in-time bullpen history
    with snap_path.open(encoding="utf-8") as f:
        snaps = [_json.loads(line) for line in f if line.strip()]
    snaps.sort(key=lambda r: r["game_start_utc"])

    history: dict[str, list[dict]] = {}
    result: dict[str, tuple[float, bool]] = {}

    for snap in snaps:
        key = (snap["game_start_utc"][:16], snap["home"]["team_name"], snap["away"]["team_name"])
        eid = crosswalk.get(key)
        if not eid:
            continue

        home_weakness = away_weakness = None
        game_relief: dict[str, dict] = {}
        for side_key, side_data in [("home", snap["home"]), ("away", snap["away"])]:
            team = side_data["team_name"]
            order = side_data.get("pitcher_order") or []
            relievers = order[1:]  # index 0 is the starter
            innings = earned = 0.0
            for pid in relievers:
                player = next((p for p in side_data["players"] if p["player_id"] == pid), None)
                if not player or "inningsPitched" not in player.get("pitching", {}):
                    continue
                stats = player["pitching"]
                innings += _ip_float(stats["inningsPitched"])
                earned += float(stats.get("earnedRuns", 0))
            game_relief[team] = {"innings": innings, "earned_runs": earned}

            prior = history.get(team, [])
            if len(prior) >= 2:
                profile = bullpen_profile(prior[-5:])
                if profile["status"] == "available":
                    weakness = profile["bullpen_weakness_index"]
                    if side_key == "home":
                        home_weakness = weakness
                    else:
                        away_weakness = weakness

        # Update history AFTER computing this game's feature (point-in-time)
        for team, line in game_relief.items():
            if line["innings"] > 0:
                history.setdefault(team, []).append(line)

        if home_weakness is not None and away_weakness is not None:
            result[eid] = (round(home_weakness - away_weakness, 6), True)

    _BULLPEN_MAP = result
    return _BULLPEN_MAP


def _bullpen_weakness_gap(event_id: str) -> tuple[float, bool]:
    """Get the real bullpen weakness gap for a given event, or (0.0, False) if unavailable."""
    return _load_bullpen_map().get(event_id, (0.0, False))


# ── Bullpen recent-workload (fatigue) gap from MLB Stats API snapshots ───

_FATIGUE_WINDOW_DAYS = FATIGUE_WINDOW_DAYS  # single source: features/bullpen.py
_BULLPEN_FATIGUE_MAP: dict[str, tuple[float, bool]] | None = None


def _load_bullpen_fatigue_map() -> dict[str, tuple[float, bool]]:
    """Build point-in-time bullpen fatigue (recent relief workload) gap map.

    Unlike `_load_bullpen_map` (relief quality/ERA), this tracks how many
    relief innings each team has thrown in the trailing
    `_FATIGUE_WINDOW_DAYS` calendar days -- a proxy for which relievers are
    actually available tonight, not season-long bullpen quality. History is
    built strictly chronologically, mirroring `_load_bullpen_map`."""
    global _BULLPEN_FATIGUE_MAP
    if _BULLPEN_FATIGUE_MAP is not None:
        return _BULLPEN_FATIGUE_MAP

    import json as _json
    from datetime import date as _date

    snap_path = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
    crosswalk_path = PROJECT_ROOT / "data/processed/mlb/games.jsonl"
    if not snap_path.exists() or not crosswalk_path.exists():
        _BULLPEN_FATIGUE_MAP = {}
        return _BULLPEN_FATIGUE_MAP

    def _ip_float(v):
        w, _, f = v.partition(".")
        return int(w) + {"0": 0.0, "1": 1 / 3, "2": 2 / 3}.get(f, 0.0)

    crosswalk = {}
    with crosswalk_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            g = _json.loads(line)
            crosswalk[(g["event_start_utc"][:16], g["home_team"], g["away_team"])] = g["event_id"]

    with snap_path.open(encoding="utf-8") as f:
        snaps = [_json.loads(line) for line in f if line.strip()]
    snaps.sort(key=lambda r: r["game_start_utc"])

    history: dict[str, list[tuple[Any, float]]] = {}
    result: dict[str, tuple[float, bool]] = {}

    for snap in snaps:
        key = (snap["game_start_utc"][:16], snap["home"]["team_name"], snap["away"]["team_name"])
        eid = crosswalk.get(key)
        if not eid:
            continue
        game_date = _date.fromisoformat(snap["game_start_utc"][:10])

        fatigue: dict[str, float] = {}
        game_relief: dict[str, float] = {}
        for side_key, side_data in [("home", snap["home"]), ("away", snap["away"])]:
            team = side_data["team_name"]
            order = side_data.get("pitcher_order") or []
            innings = 0.0
            for pid in order[1:]:  # index 0 is the starter
                player = next((p for p in side_data["players"] if p["player_id"] == pid), None)
                if not player or "inningsPitched" not in player.get("pitching", {}):
                    continue
                innings += _ip_float(player["pitching"]["inningsPitched"])
            game_relief[team] = innings

            prior = history.get(team, [])
            fatigue[side_key] = sum(
                ip for game_day, ip in prior if 0 <= (game_date - game_day).days <= _FATIGUE_WINDOW_DAYS
            )

        # Update history AFTER computing this game's feature (point-in-time)
        for team, innings in game_relief.items():
            history.setdefault(team, []).append((game_date, innings))

        result[eid] = (round(fatigue["home"] - fatigue["away"], 6), True)

    _BULLPEN_FATIGUE_MAP = result
    return _BULLPEN_FATIGUE_MAP


def _bullpen_fatigue_gap(event_id: str) -> tuple[float, bool]:
    """Get the real bullpen fatigue gap for a given event, or (0.0, False) if unavailable."""
    return _load_bullpen_fatigue_map().get(event_id, (0.0, False))


# ── Batter offense PIT priors gap from MLB Stats API snapshots ───────────

_OFFENSE_PIT_MAP: dict[str, tuple[float, bool]] | None = None


def _load_offense_pit_map() -> dict[str, tuple[float, bool]]:
    """Build point-in-time ``offense_pit_gap`` map (home - away composite,
    see ``features.batter_offense``) for every crosswalked MLB event.

    Delegates the actual player-shrinkage/team-composite math to
    ``features.batter_offense.matchup_offense_pit_gap``, which already
    enforces point-in-time discipline (games strictly before the decision
    time) via its own snapshot index -- this function only owns the
    event_id crosswalk and process-lifetime memoization, mirroring
    ``_load_bullpen_map``."""
    global _OFFENSE_PIT_MAP
    if _OFFENSE_PIT_MAP is not None:
        return _OFFENSE_PIT_MAP

    import json as _json

    from .domain import parse_utc as _parse_utc
    from .features.batter_offense import matchup_offense_pit_gap

    snap_path = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
    crosswalk_path = PROJECT_ROOT / "data/processed/mlb/games.jsonl"
    if not snap_path.exists() or not crosswalk_path.exists():
        _OFFENSE_PIT_MAP = {}
        return _OFFENSE_PIT_MAP

    crosswalk = {}
    with crosswalk_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            g = _json.loads(line)
            crosswalk[(g["event_start_utc"][:16], g["home_team"], g["away_team"])] = g["event_id"]

    with snap_path.open(encoding="utf-8") as f:
        snaps = [_json.loads(line) for line in f if line.strip()]

    result: dict[str, tuple[float, bool]] = {}
    for snap in snaps:
        key = (snap["game_start_utc"][:16], snap["home"]["team_name"], snap["away"]["team_name"])
        eid = crosswalk.get(key)
        if not eid:
            continue
        decision = _parse_utc(snap["game_start_utc"])
        gap, available = matchup_offense_pit_gap(
            snap["home"]["team_name"], snap["away"]["team_name"], decision, snapshot_path=snap_path
        )
        if available:
            result[eid] = (gap, available)

    _OFFENSE_PIT_MAP = result
    return _OFFENSE_PIT_MAP


def _offense_pit_gap(event_id: str) -> tuple[float, bool]:
    """Get the real offense PIT-priors gap for a given event, or (0.0, False) if unavailable."""
    return _load_offense_pit_map().get(event_id, (0.0, False))
