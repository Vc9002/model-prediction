"""Baseline spread models using linear regression on elo + trend.

Predicts margin of victory, then estimates cover probability against
a reference spread line. Same architecture as totals baseline.
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


MINIMUM_CALLS = 50
MINIMUM_HIT_RATE = 0.60


@dataclass(frozen=True)
class SpreadRow:
    date: str
    event_id: str
    margin: int  # home_score - away_score
    elo_probability: float
    trend_gap: float


def build_spread_rows(
    store: FeatureStore,
    sport: str,
    *,
    minimum_history_games: int = 50,
) -> list[SpreadRow]:
    games = store.load_games(sport)
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for game in games:
        by_date[game.start.date().isoformat()].append(game)

    history: list[GameRecord] = []
    rows: list[SpreadRow] = []
    for day in sorted(by_date):
        day_games = sorted(by_date[day], key=lambda item: (item.start, item.event_id))
        if len(history) >= minimum_history_games:
            elo = build_elo(history, sport)
            trends = TrendEngine(history)
            for game in day_games:
                home_trend = trends.team_trend(game.home_team)
                away_trend = trends.team_trend(game.away_team)
                rows.append(
                    SpreadRow(
                        date=day,
                        event_id=game.event_id,
                        margin=game.home_score - game.away_score,
                        elo_probability=elo.expected_home_win(game.home_team, game.away_team),
                        trend_gap=home_trend.offensive_momentum - away_trend.offensive_momentum,
                    )
                )
        history.extend(day_games)
    return rows


def validate_spreads(
    store: FeatureStore,
    sport: str,
) -> dict[str, Any]:
    rows = build_spread_rows(store, sport)
    if len(rows) < 100:
        return {"status": "insufficient_data", "sport": sport, "rows": len(rows)}

    dates = sorted({r.date for r in rows})
    n = len(dates)
    train_cut = dates[int(n * 0.60)]
    val_cut = dates[min(int(n * 0.80), n - 1)]

    train = [r for r in rows if r.date < train_cut]
    val = [r for r in rows if train_cut <= r.date < val_cut]
    holdout = [r for r in rows if r.date >= val_cut]

    if not train or not val or not holdout:
        return {"status": "insufficient_split", "sport": sport}

    X_train = np.array([[r.elo_probability, r.trend_gap] for r in train])
    y_train = np.array([r.margin for r in train])
    model = LinearRegression().fit(X_train, y_train)

    X_val = np.array([[r.elo_probability, r.trend_gap] for r in val])
    val_preds = model.predict(X_val)
    val_errors = np.array([r.margin for r in val]) - val_preds
    error_sd = float(np.std(val_errors))

    X_holdout = np.array([[r.elo_probability, r.trend_gap] for r in holdout])
    predictions = model.predict(X_holdout)

    # Reference spread: league average home margin (basketball lines are ~-5)
    all_margins = [r.margin for r in rows]
    league_mean_margin = float(np.mean(all_margins))
    ref_spread = round(league_mean_margin * 2) / 2  # round to nearest 0.5

    # Find optimal threshold on validation
    best_threshold = 0.0
    best_hit_rate = 0.0
    for threshold in np.arange(0.02, 0.30, 0.02):
        calls = hits = 0
        for pred, row in zip(val_preds, val):
            cover_prob = _normal_cdf(pred - ref_spread, 0, error_sd)
            confidence = max(cover_prob, 1 - cover_prob)
            if confidence >= 0.5 + threshold:
                calls += 1
                selection = "home" if cover_prob >= 0.5 else "away"
                actual_cover = row.margin > ref_spread
                hits += 1 if (selection == "home" and actual_cover) or (selection == "away" and not actual_cover) else 0
        if calls >= 20:
            hr = hits / calls
            if hr > best_hit_rate:
                best_hit_rate = hr
                best_threshold = float(threshold)

    # Grade holdout
    calls = hits = 0
    for pred, row in zip(predictions, holdout):
        cover_prob = _normal_cdf(pred - ref_spread, 0, error_sd)
        confidence = max(cover_prob, 1 - cover_prob)
        if confidence < 0.5 + best_threshold:
            continue
        calls += 1
        selection = "home" if cover_prob >= 0.5 else "away"
        actual_cover = row.margin > ref_spread
        hits += 1 if (selection == "home" and actual_cover) or (selection == "away" and not actual_cover) else 0

    hit_rate = hits / calls if calls else 0.0
    units = hits * (10/11) - (calls - hits) if calls else 0.0

    brier = 0.0
    if holdout:
        for pred, row in zip(predictions, holdout):
            cover_prob = _normal_cdf(pred - ref_spread, 0, error_sd)
            actual = 1.0 if row.margin > ref_spread else 0.0
            brier += (cover_prob - actual) ** 2
        brier /= len(holdout)

    qualified = calls >= MINIMUM_CALLS and hit_rate >= MINIMUM_HIT_RATE

    return {
        "sport": sport,
        "status": "research_score_model_candidate",
        "model": "linear_regression_on_elo_trend",
        "league_mean_margin": round(league_mean_margin, 2),
        "reference_spread": ref_spread,
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
            "base_rate_cover": round(sum(1 for r in holdout if r.margin > ref_spread) / len(holdout), 6),
        },
        "locked_holdout": {"mae": error_sd, "baseline_mae": error_sd},
        "training": {"holdout_rows": len(holdout)},
        "market_qualification": {"reason": "qualified" if qualified else "below_gate"},
        "qualified": qualified,
    }


def _normal_cdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.5
    return 0.5 * (1 + math.erf((x - mean) / (sd * math.sqrt(2))))
