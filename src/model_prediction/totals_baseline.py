"""Baseline totals (over/under) models using linear regression on elo + trend.

Predicts combined score from Elo + trend features, then estimates over/under
probability using the holdout error distribution. Produces a simple baseline
that future models must beat.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from sklearn.linear_model import LinearRegression

from .features.base import FeatureStore, GameRecord

from .features.elo_ratings import build_elo
from .features.trends import TrendEngine

from .lifecycle import evaluate_locked_holdout


MINIMUM_CALLS = 50
MINIMUM_HIT_RATE = 0.60


@dataclass(frozen=True)
class TotalsRow:
    date: str
    event_id: str
    total: int  # home_score + away_score
    elo_probability: float
    trend_gap: float


def build_totals_rows(
    store: FeatureStore,
    sport: str,
    *,
    minimum_history_games: int = 50,
) -> list[TotalsRow]:
    """Build walk-forward totals features identical to moneyline pipeline."""
    games = store.load_games(sport)
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for game in games:
        by_date[game.start.date().isoformat()].append(game)

    history: list[GameRecord] = []
    rows: list[TotalsRow] = []
    for day in sorted(by_date):
        day_games = sorted(by_date[day], key=lambda item: (item.start, item.event_id))
        if len(history) >= minimum_history_games:
            elo = build_elo(history, sport)
            trends = TrendEngine(history)
            for game in day_games:
                home_trend = trends.team_trend(game.home_team)
                away_trend = trends.team_trend(game.away_team)
                rows.append(
                    TotalsRow(
                        date=day,
                        event_id=game.event_id,
                        total=game.home_score + game.away_score,
                        elo_probability=elo.expected_home_win(game.home_team, game.away_team),
                        trend_gap=home_trend.offensive_momentum - away_trend.offensive_momentum,
                    )
                )
        history.extend(day_games)
    return rows


def validate_totals(
    store: FeatureStore,
    sport: str,
    *,
    target_hit_rate: float = 0.60,
) -> dict[str, Any]:
    """Build a baseline linear regression totals model and grade on holdout."""
    rows = build_totals_rows(store, sport)
    if len(rows) < 100:
        return {"status": "insufficient_data", "sport": sport, "rows": len(rows)}

    # Chronological split: 60/20/20 by date
    dates = sorted({r.date for r in rows})
    n = len(dates)
    train_cut = dates[int(n * 0.60)]
    val_cut = dates[min(int(n * 0.80), n - 1)]
    holdout_cut = dates[-1]

    train = [r for r in rows if r.date < train_cut]
    val = [r for r in rows if train_cut <= r.date < val_cut]
    holdout = [r for r in rows if r.date >= val_cut]

    if not train or not val or not holdout:
        return {"status": "insufficient_split", "sport": sport}

    # Fit linear regression on train
    X_train = np.array([[r.elo_probability, r.trend_gap] for r in train])
    y_train = np.array([r.total for r in train])
    model = LinearRegression().fit(X_train, y_train)

    # Predict on all cohorts
    X_holdout = np.array([[r.elo_probability, r.trend_gap] for r in holdout])
    predictions = model.predict(X_holdout)

    # Learn error distribution from validation cohort
    X_val = np.array([[r.elo_probability, r.trend_gap] for r in val])
    val_preds = model.predict(X_val)
    val_errors = np.array([r.total for r in val]) - val_preds
    error_sd = float(np.std(val_errors))

    # Reference line: rolling average of recent totals
    all_totals = [r.total for r in rows]
    league_mean = float(np.mean(all_totals))

    # Find optimal threshold on validation
    best_threshold = 0.0
    best_hit_rate = 0.0
    for threshold in np.arange(0.02, 0.30, 0.02):
        calls = hits = 0
        for pred, row in zip(val_preds, val):
            # P(over) = 1 - CDF(reference_line)
            # Use reference line = league_mean rounded to nearest 0.5
            ref_line = round(league_mean * 2) / 2
            over_prob = 1.0 - _normal_cdf(ref_line, pred, error_sd)
            confidence = max(over_prob, 1 - over_prob)
            if confidence >= 0.5 + threshold:
                calls += 1
                selection = "over" if over_prob >= 0.5 else "under"
                actual_over = row.total > ref_line
                hits += 1 if (selection == "over" and actual_over) or (selection == "under" and not actual_over) else 0
        if calls >= 20 and calls > 0:
            hr = hits / calls
            if hr > best_hit_rate:
                best_hit_rate = hr
                best_threshold = float(threshold)

    # Grade holdout
    ref_line = round(league_mean * 2) / 2
    calls = hits = 0
    for pred, row in zip(predictions, holdout):
        over_prob = 1.0 - _normal_cdf(ref_line, pred, error_sd)
        confidence = max(over_prob, 1 - over_prob)
        if confidence < 0.5 + best_threshold:
            continue
        calls += 1
        selection = "over" if over_prob >= 0.5 else "under"
        actual_over = row.total > ref_line
        hits += 1 if (selection == "over" and actual_over) or (selection == "under" and not actual_over) else 0

    hit_rate = hits / calls if calls else 0.0
    units = hits * (10/11) - (calls - hits) if calls else 0.0

    brier = 0.0
    if holdout:
        for pred, row in zip(predictions, holdout):
            over_prob = 1.0 - _normal_cdf(ref_line, pred, error_sd)
            actual = 1.0 if row.total > ref_line else 0.0
            brier += (over_prob - actual) ** 2
        brier /= len(holdout)

    qualified = calls >= MINIMUM_CALLS and hit_rate >= MINIMUM_HIT_RATE

    return {
        "sport": sport,
        "status": "baseline_totals",
        "model": "linear_regression_on_elo_trend",
        "league_mean_total": round(league_mean, 2),
        "reference_line": ref_line,
        "error_sd": round(error_sd, 4),
        "threshold": round(best_threshold + 0.5, 4),
        "train_observations": len(train),
        "validation_observations": len(val),
        "holdout_observations": len(holdout),
        "holdout": {
            "calls": calls,
            "hits": hits,
            "hit_rate": round(hit_rate, 6),
            "units_at_minus_110": round(units, 6),
            "brier": round(brier, 6),
            "base_rate_over": round(sum(1 for r in holdout if r.total > ref_line) / len(holdout), 6),
        },
        "coefficients": {
            "elo_probability": round(float(model.coef_[0]), 6),
            "trend_gap": round(float(model.coef_[1]), 6),
            "intercept": round(float(model.intercept_), 6),
        },
        "qualified": qualified,
    }


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5
    return 0.5 * (1 + math.erf((x - mean) / (sd * math.sqrt(2))))
