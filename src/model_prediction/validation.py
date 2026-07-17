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

from .calibration import calibration_metrics
from .features.base import FeatureStore, GameRecord
from .features.elo_ratings import build_elo
from .features.park_factors import park_factor
from .features.trends import TrendEngine
from .lifecycle import evaluate_locked_holdout
from .models.learned_market import build_artifact, learn_confidence_threshold
from .pricing import american_to_decimal


PRIMARY_THRESHOLD_TARGET_HIT_RATE = 0.65
DIAGNOSTIC_THRESHOLD_TARGET_HIT_RATE = 0.60
QUALIFICATION_MINIMUM_HIT_RATE = 0.60
MINIMUM_CALLS = 50
MINIMUM_MONTHLY_CALLS = 10
LEARNED_ARTIFACT_VERSIONS = {
    "mlb": "mlb-elo-trend-lr-v3",
    "nba": "nba-elo-trend-lr-v3",
    "wnba": "wnba-elo-trend-lr-v3",
    "nfl": "nfl-elo-trend-lr-v3",
    "soccer": "soccer-elo-trend-lr-v1",
    "tennis": "tennis-elo-trend-lr-v1",
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
    defensive_gap: float = 0.0
    consistency_gap: float = 0.0
    hot_cold_gap: float = 0.0


FEATURE_VARIANTS: dict[str, tuple[str, ...]] = {
    "elo_only": ("elo_probability",),
    "elo_trend": ("elo_probability", "trend_gap"),
    "elo_trend_full": (
        "elo_probability",
        "trend_gap",
        "defensive_gap",
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
        by_date[game.start.date().isoformat()].append(game)

    history: list[GameRecord] = []
    rows: list[ValidationRow] = []
    for day in sorted(by_date):
        day_games = sorted(by_date[day], key=lambda item: (item.start, item.event_id))
        if len(history) >= minimum_history_games:
            elo = build_elo(history, sport)
            trends = TrendEngine(history)
            home_win_rate_30d, home_games_30d = _trailing_home_rate(history, day)
            for game in day_games:
                if game.home_score == game.away_score:
                    continue
                home_trend = trends.team_trend(game.home_team)
                away_trend = trends.team_trend(game.away_team)
                park = (
                    park_factor(game.home_team)
                    if sport.lower() == "mlb"
                    else {"park_factor": 1.0, "status": "not_applicable"}
                )
                # Historical scoreboards contain no point-in-time weather in
                # the current cache. Keep the neutral value explicit and mark
                # coverage false so the ablation cannot claim a weather gain.
                rows.append(
                    ValidationRow(
                        date=day,
                        event_id=game.event_id,
                        outcome=int(game.home_score > game.away_score),
                        elo_probability=elo.expected_home_win(game.home_team, game.away_team),
                        trend_gap=home_trend.offensive_momentum - away_trend.offensive_momentum,
                        park_factor=float(park["park_factor"]),
                        weather_factor=1.0,
                        park_available=park["status"] == "available",
                        weather_available=False,
                        elo_neutral_probability=elo.expected_neutral_win(
                            game.home_team, game.away_team
                        ),
                        trailing_home_win_rate_30d=home_win_rate_30d,
                        trailing_home_games_30d=home_games_30d,
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
    if sport.lower() == "mlb":
        variants_to_run.extend(
            ["elo_trend_adaptive_hfa", "elo_trend_park", "elo_trend_park_weather"]
        )
    variants = {
        name: evaluate_variant(train, validation, holdout, FEATURE_VARIANTS[name])
        for name in variants_to_run
    }
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
        if cutoff <= game.start.date() < date.fromisoformat(day)
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
    """Report whether exact non-moneyline contracts can be validated honestly."""
    key = sport.lower()
    raw_root = store.data_root / "raw" / key
    events = spread_lines = total_lines = 0
    first_inning_outcomes = first_five_outcomes = 0
    for path in raw_root.glob(f"*/scores_{key}.json") if raw_root.exists() else ():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for event in payload.get("events", []):
            events += 1
            competition = (event.get("competitions") or [{}])[0]
            odds = competition.get("odds") or []
            if odds:
                spread_lines += any(
                    item.get("spread") is not None or item.get("details") for item in odds
                )
                total_lines += any(item.get("overUnder") is not None for item in odds)
            if key != "mlb":
                continue
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
    if key in {"nba", "wnba", "nfl"}:
        return {
            "events_scanned": events,
            "spread_lines": spread_lines,
            "total_lines": total_lines,
            "model_parameters_changed": False,
            "spread": "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES",
            "total": "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES",
            "reason": (
                "A spread or total outcome is undefined without its exact pregame line; "
                "score-only history cannot validate the configured normal-CDF heads."
            ),
        }
    if key == "mlb":
        return {
            "events_scanned": events,
            "first_inning_outcomes": first_inning_outcomes,
            "first_five_outcomes": first_five_outcomes,
            "full_game_spread": "DIAGNOSTIC_ONLY_RECONSTRUCTED_LINES_TIMESTAMP_INVALID",
            "full_game_total": "DIAGNOSTIC_ONLY_RECONSTRUCTED_LINES_TIMESTAMP_INVALID",
            "first_five_spread": "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES_AND_INPUTS",
            "first_five_total": "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES_AND_INPUTS",
            "yrfi_nrfi": "BLOCKED_MISSING_POINT_IN_TIME_STARTER_INPUTS",
            "reason": (
                "Inning outcomes are recoverable, but exact F5/YRFI lines and pregame "
                "starter/bullpen snapshots are not."
            ),
        }
    return {"status": "not_requested", "events_scanned": events}


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
    variant = sport_report["variants"]["elo_trend"]
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
