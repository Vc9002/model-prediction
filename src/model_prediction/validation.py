"""Chronological learned-model validation and feature ablation.

The model-development cohort fits coefficients, the later validation cohort
learns a confidence threshold, and the final cohort remains untouched until
one locked evaluation. Market prices never enter these independent models.
"""

from __future__ import annotations

import calendar
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from sklearn.linear_model import LogisticRegression

from .config import PROJECT_ROOT

from .calibration import calibration_metrics
from .domain import EASTERN
from .features.base import FeatureStore, GameRecord
from .features.elo_ratings import build_elo
from .features.park_factors import park_factor
from .features.team_runs import pitcher_era_gap_from_history
from .features.schedule_load import matchup_schedule_load
from .features.trends import TrendEngine
from .lifecycle import evaluate_locked_holdout
from .models.learned_market import build_artifact, learn_confidence_threshold
from .pricing import american_to_decimal


PRIMARY_THRESHOLD_TARGET_HIT_RATE = 0.65
DIAGNOSTIC_THRESHOLD_TARGET_HIT_RATE = 0.60
QUALIFICATION_MINIMUM_HIT_RATE = 0.60
MINIMUM_CALLS = 50
MINIMUM_MONTHLY_CALLS = 10
# v5/v4/v2 lineage (2026-07-21): Eastern-time point-in-time cutoff (was UTC)
# and unified train/serve feature definitions (pitcher_era_gap = shared rolling
# runs-allowed gap). Bump again on any further change to the training basis.
LEARNED_ARTIFACT_VERSIONS = {
    "mlb": "mlb-elo-trend-lr-v5",
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
    elo_neutral_probability: float = 0.5
    trailing_home_win_rate_30d: float = 0.5
    trailing_home_games_30d: int = 0
    defensive_trend_gap: float = 0.0
    pitcher_era_gap: float = 0.0
    starter_era_gap: float = 0.0
    consistency_gap: float = 0.0
    hot_cold_gap: float = 0.0
    rest_disparity: float = 0.0
    back_to_back_gap: float = 0.0
    games_last_7_gap: float = 0.0
    schedule_available: float = 0.0
    outcome_3way: int = 0  # 2=home, 1=draw, 0=away — used for soccer 3-way


FEATURE_VARIANTS: dict[str, tuple[str, ...]] = {
    "elo_only": ("elo_probability",),
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
    "elo_trend_park_weather_pitcher": (
        "elo_probability",
        "trend_gap",
        "park_factor",
        "weather_factor",
        "pitcher_era_gap",
    ),
    "soccer_3way": ("elo_probability", "trend_gap"),
}


def build_walk_forward_rows(
    store: FeatureStore,
    sport: str,
    *,
    minimum_history_games: int = 50,
) -> list[ValidationRow]:
    """Construct pregame features using only prior completed dates."""
    games = store.load_games(sport)
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for game in games:
        by_date[game.start.astimezone(EASTERN).date().isoformat()].append(game)

    history: list[GameRecord] = []
    rows: list[ValidationRow] = []
    for day in sorted(by_date):
        day_games = sorted(by_date[day], key=lambda item: (item.start, item.event_id))
        if len(history) >= minimum_history_games:
            elo = build_elo(history, sport)
            trends = TrendEngine(history)
            home_win_rate_30d, home_games_30d = _trailing_home_rate(history, day)
            for game in day_games:
                is_soccer = sport.lower() == "soccer"
                if not is_soccer and game.home_score == game.away_score:
                    continue
                home_trend = trends.team_trend(game.home_team)
                away_trend = trends.team_trend(game.away_team)
                schedule = matchup_schedule_load(
                    history,
                    game.home_team,
                    game.away_team,
                    game.start,
                )
                
                # Rolling pitching quality: runs allowed per game (last 5).
                # Shared definition with forward inference (features/team_runs).
                pitcher_gap = pitcher_era_gap_from_history(
                    history, game.home_team, game.away_team
                )
                
                # Real starter ERA gap from MLB Stats API snapshots (point-in-time)
                starter_gap = _starter_era_gap(game.event_id) if sport.lower() == "mlb" else 0.0
                
                # Historical weather from Open-Meteo DB
                weather = _lookup_weather(game.home_team, game.start.astimezone(EASTERN).date().isoformat())
                
                park = (
                    park_factor(game.home_team)
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
                rows.append(
                    ValidationRow(
                        date=day,
                        event_id=game.event_id,
                        outcome=int(game.home_score > game.away_score),
                        elo_probability=elo.expected_home_win(game.home_team, game.away_team),
                        trend_gap=home_trend.offensive_momentum - away_trend.offensive_momentum,
                        defensive_trend_gap=home_trend.defensive_momentum - away_trend.defensive_momentum,
                        consistency_gap=home_trend.consistency - away_trend.consistency,
                        hot_cold_gap=home_trend.hot_cold_score - away_trend.hot_cold_score,
                        rest_disparity=schedule["rest_disparity"],
                        back_to_back_gap=schedule["back_to_back_gap"],
                        games_last_7_gap=schedule["games_last_7_gap"],
                        schedule_available=schedule["schedule_available"],
                        park_factor=float(park["park_factor"]),
                        weather_factor=float(weather.get("run_factor", 1.0)),
                        pitcher_era_gap=pitcher_gap,
                        starter_era_gap=starter_gap,
                        park_available=park["status"] == "available",
                        weather_available=weather.get("available", False),
                        elo_neutral_probability=elo.expected_neutral_win(
                            game.home_team, game.away_team
                        ),
                        trailing_home_win_rate_30d=home_win_rate_30d,
                        trailing_home_games_30d=home_games_30d,
                        outcome_3way=outcome_3way,
                    )
                )
        history.extend(day_games)
    return rows


def chronological_split(
    rows: Sequence[ValidationRow],
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> tuple[list[ValidationRow], list[ValidationRow], list[ValidationRow], dict[str, Any]]:
    """Split on complete dates so games from one date never cross cohorts."""
    if not rows:
        raise ValueError("cannot split an empty validation dataset")
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
        variants_to_run.extend([
            "elo_trend_adaptive_hfa", "elo_trend_park", "elo_trend_park_weather",
            "elo_trend_park_pitcher", "elo_trend_park_weather_pitcher",
        ])
    if sport.lower() == "soccer":
        variants_to_run.append("soccer_3way")
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
        "confidence_gap_audit": confidence_gap_equivalence(
            variants["elo_trend"]["primary_65"]
        ),
        "pitcher_feature_audit": (
            historical_pitcher_feature_audit(store) if sport.lower() == "mlb" else None
        ),
        "multi_market_readiness": multi_market_readiness(store, sport),
        "feature_decisions": _feature_decisions(variants, sport),
    }


def _trailing_home_rate(history: Sequence[GameRecord], day: str) -> tuple[float, int]:
    cutoff = date.fromisoformat(day) - timedelta(days=30)
    recent = [
        game
        for game in history
        if cutoff <= game.start.astimezone(EASTERN).date() < date.fromisoformat(day)
        and game.home_score != game.away_score
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
            first_five_outcomes += all(
                set(range(1, 6)).issubset(values) for values in periods
            )

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
            except OSError:
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
                except OSError:
                    continue

        fg_spread_status, fg_total_status = _readiness_for_market(
            spread_snapshots, total_snapshots, "baseball"
        )
        f5_spread_status, f5_total_status = _readiness_for_market(
            f5_spread, f5_total, "baseball"
        )
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
                "DATA_READY_PENDING_BACKTEST" if yrfi_nrfi >= _MINIMUM_SNAPSHOT_COUNT
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
        spread_status, total_status = _readiness_for_market(
            spread_snapshots, total_snapshots, "baseball"
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
            "-f5-",      # first 5 innings
            "-yrfi",     # yes run first inning
            "-nrfi",     # no run first inning
            "-tt-",      # team total
        )
        if any(pattern in slug for pattern in _MLB_SUB_PATTERNS):
            return True
    # NBA/WNBA: quarter/half markets, player props
    if sport in ("nba", "wnba"):
        _BBALL_SUB_PATTERNS = (
            "-1q-", "-2q-", "-3q-", "-4q-",  # quarter markets
            "-1h-", "-2h-",                    # half markets
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
        parts.append(
            f"spread: {spread_count}/{_MINIMUM_SNAPSHOT_COUNT} contract snapshots"
        )
    if total_count < _MINIMUM_SNAPSHOT_COUNT:
        parts.append(
            f"total: {total_count}/{_MINIMUM_SNAPSHOT_COUNT} contract snapshots"
        )
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


def _add_legacy_backfill(
    data_root: Path, sport: str, spread_count: int, total_count: int
) -> tuple[int, int]:
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
        except OSError:
            pass

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
        except OSError:
            pass

    return spread_count, total_count


def run_validation_audit(
    store: FeatureStore,
    sports: Sequence[str],
    reconstructed_mlb_prices: str | Path | None = None,
) -> dict[str, Any]:
    report = {
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
        report["sports"]["mlb"]["historical_price_diagnostic"] = (
            evaluate_reconstructed_mlb_moneyline(store, reconstructed_mlb_prices)
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
    """Write one immutable, hash-verified artifact per audited sport."""
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for sport, sport_report in report["sports"].items():
        artifact = build_production_artifact(sport_report)
        path = root / f"{artifact['model_version']}.json"
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
        "mean_model_minus_no_vig_market_probability": (
            round(sum(edges) / len(edges), 6) if edges else None
        ),
        "providers": sorted(providers),
        "timestamp_valid_values": sorted(timestamp_valid_values),
        "qualification_gate": False,
        "limitation": (
            "Postgame-retrieved sportsbook openings are not Polymarket executable asks and cannot "
            "establish trade profitability."
        ),
    }


def evaluate_variant(
    train: Sequence[ValidationRow],
    validation: Sequence[ValidationRow],
    holdout: Sequence[ValidationRow],
    feature_names: Sequence[str],
) -> dict[str, Any]:
    model = _fit(train, feature_names)
    validation_probabilities = _predict(model, validation, feature_names)
    holdout_probabilities = _predict(model, holdout, feature_names)
    primary = _learn_and_grade(
        validation_probabilities,
        validation,
        holdout_probabilities,
        holdout,
        target_hit_rate=PRIMARY_THRESHOLD_TARGET_HIT_RATE,
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
            name: round(float(value), 10)
            for name, value in zip(feature_names, model.coef_[0], strict=True)
        },
        "intercept": round(float(model.intercept_[0]), 10),
        "primary_65": primary,
        "diagnostic_60": diagnostic,
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
            val_confidences, val_outcomes,
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
    units = hits * (10/11) - (calls - hits) if calls else 0.0

    # Monthly breakdown — shared helper so 3-way gets partial_month on incomplete
    # final months just like the binary path does.
    selected_for_grade = [
        (float(conf), 1 if sel == row.outcome_3way else 0, row.date)
        for conf, sel, row in zip(ho_confidences, ho_selections, holdout)
        if conf >= threshold
    ]
    holdout_end = max(row.date for _, _, row in zip(ho_confidences, ho_selections, holdout)) if holdout else date.today()
    monthly_list = _monthly_grade(selected_for_grade, holdout_end=holdout_end)

    every_month_positive = all(m["units_at_minus_110"] > 0 for m in monthly_list if m["qualification_status"] == "qualifying")
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
) -> dict[str, Any]:
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
        sum((probability - outcome) ** 2 for probability, outcome, _ in selected) / calls
        if calls
        else None
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
        failed_months = [
            month["month"]
            for month in qualifying_months
            if month["units_at_minus_110"] <= 0
        ]
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
        baseline, candidate = _paired_comparison_metrics(
            variants[baseline_name], variants[candidate_name]
        )
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
        if baseline_evaluation.get("status") != "evaluated" or candidate_evaluation.get(
            "status"
        ) != "evaluated":
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
        if single_evaluation.get("status") != "evaluated" or agreement_evaluation.get(
            "status"
        ) != "evaluated":
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
