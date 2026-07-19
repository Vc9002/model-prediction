"""Point-in-time total-score regression for research-only market support.

The model predicts combined final score without using a market line. That makes
MAE/RMSE validation honest even when historical over/under contracts are
missing. It does not establish over/under accuracy, price edge, or ROI.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from statistics import pstdev
from typing import Any, Sequence

from sklearn.linear_model import Ridge

from .features.base import FeatureStore, GameRecord


FEATURE_NAMES = (
    "league_total_mean",
    "away_scored_5",
    "away_scored_10",
    "away_allowed_5",
    "away_allowed_10",
    "home_scored_5",
    "home_scored_10",
    "home_allowed_5",
    "home_allowed_10",
)
MINIMUM_TEAM_GAMES = 10
MINIMUM_LEAGUE_GAMES = 50


@dataclass(frozen=True)
class TotalScoreRow:
    date: str
    event_id: str
    features: tuple[float, ...]
    actual_total: float
    baseline_total: float


def _artifact_hash(payload: dict[str, Any]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "artifact_hash"}
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _recent(values: deque[float], count: int) -> float:
    selected = list(values)[-count:]
    return _mean(selected)


def build_total_score_rows(games: Sequence[GameRecord]) -> list[TotalScoreRow]:
    """Construct rows strictly before updating state with the target game."""
    scored: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=25))
    allowed: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=25))
    league_totals: deque[float] = deque(maxlen=200)
    rows: list[TotalScoreRow] = []
    for game in sorted(games, key=lambda item: item.start):
        away_scored = scored[game.away_team]
        away_allowed = allowed[game.away_team]
        home_scored = scored[game.home_team]
        home_allowed = allowed[game.home_team]
        if (
            len(league_totals) >= MINIMUM_LEAGUE_GAMES
            and min(len(away_scored), len(home_scored)) >= MINIMUM_TEAM_GAMES
        ):
            baseline = _mean(league_totals)
            features = (
                baseline,
                _recent(away_scored, 5),
                _recent(away_scored, 10),
                _recent(away_allowed, 5),
                _recent(away_allowed, 10),
                _recent(home_scored, 5),
                _recent(home_scored, 10),
                _recent(home_allowed, 5),
                _recent(home_allowed, 10),
            )
            rows.append(
                TotalScoreRow(
                    date=game.start.date().isoformat(),
                    event_id=game.event_id,
                    features=features,
                    actual_total=float(game.total),
                    baseline_total=baseline,
                )
            )
        scored[game.away_team].append(float(game.away_score))
        allowed[game.away_team].append(float(game.home_score))
        scored[game.home_team].append(float(game.home_score))
        allowed[game.home_team].append(float(game.away_score))
        league_totals.append(float(game.total))
    return rows


def _metrics(predictions: Sequence[float], rows: Sequence[TotalScoreRow]) -> dict[str, float]:
    errors = [prediction - row.actual_total for prediction, row in zip(predictions, rows, strict=True)]
    absolute = [abs(error) for error in errors]
    return {
        "mae": round(_mean(absolute), 6),
        "rmse": round(math.sqrt(_mean([error * error for error in errors])), 6),
        "mean_error": round(_mean(errors), 6),
    }


def _paired_mae_gain_interval(
    predictions: Sequence[float],
    rows: Sequence[TotalScoreRow],
    samples: int = 2_000,
) -> tuple[float, float]:
    gains = [
        abs(row.baseline_total - row.actual_total) - abs(prediction - row.actual_total)
        for prediction, row in zip(predictions, rows, strict=True)
    ]
    generator = random.Random(20260717)
    bootstrapped = sorted(
        _mean([gains[generator.randrange(len(gains))] for _ in gains])
        for _ in range(samples)
    )
    return (
        round(bootstrapped[int(samples * 0.025)], 6),
        round(bootstrapped[int(samples * 0.975)], 6),
    )


def validate_total_score_model(store: FeatureStore, sport: str) -> dict[str, Any]:
    rows = build_total_score_rows(store.load_games(sport))
    if len(rows) < 150:
        return {
            "sport": sport.lower(),
            "status": "insufficient_point_in_time_rows",
            "rows": len(rows),
            "minimum_rows": 150,
        }
    train_end = int(len(rows) * 0.60)
    validation_end = int(len(rows) * 0.80)
    train, validation, holdout = rows[:train_end], rows[train_end:validation_end], rows[validation_end:]
    model = Ridge(alpha=10.0)
    model.fit([row.features for row in train], [row.actual_total for row in train])
    validation_predictions = [float(value) for value in model.predict([row.features for row in validation])]
    holdout_predictions = [float(value) for value in model.predict([row.features for row in holdout])]
    residuals = [
        row.actual_total - prediction
        for row, prediction in zip(validation, validation_predictions, strict=True)
    ]
    holdout_metrics = _metrics(holdout_predictions, holdout)
    baseline_metrics = _metrics([row.baseline_total for row in holdout], holdout)
    mae_gain = baseline_metrics["mae"] - holdout_metrics["mae"]
    gain_interval = _paired_mae_gain_interval(holdout_predictions, holdout)
    payload: dict[str, Any] = {
        "schema_version": "1",
        "sport": sport.lower(),
        "model_version": f"{sport.lower()}-total-score-ridge-v1",
        "method": "ridge_regression",
        "alpha": 10.0,
        "feature_names": list(FEATURE_NAMES),
        "coefficients": [round(float(value), 10) for value in model.coef_],
        "intercept": round(float(model.intercept_), 10),
        "validation_residual_sd": round(pstdev(residuals), 6),
        "training": {
            "total_games": len(store.load_games(sport)),
            "usable_rows": len(rows),
            "train_rows": len(train),
            "validation_rows": len(validation),
            "holdout_rows": len(holdout),
            "train_end": train[-1].date,
            "validation_end": validation[-1].date,
            "holdout_end": holdout[-1].date,
            "point_in_time": True,
        },
        "locked_holdout": {
            **holdout_metrics,
            "baseline_mae": baseline_metrics["mae"],
            "baseline_rmse": baseline_metrics["rmse"],
            "mae_gain_vs_rolling_league_mean": round(mae_gain, 6),
            "mae_gain_95pct_interval": list(gain_interval),
            "beats_baseline_mae": mae_gain > 0,
            "statistically_clear_mae_gain": gain_interval[0] > 0,
        },
        "market_qualification": {
            "eligible": False,
            "reason": "BLOCKED_MISSING_TIMESTAMP_VALID_HISTORICAL_TOTAL_LINES",
            "note": (
                "Score MAE/RMSE do not prove over-under accuracy, executable edge, or ROI."
            ),
        },
        "status": "research_score_model_candidate",
    }
    payload["artifact_hash"] = _artifact_hash(payload)
    return payload


def validate_all_total_score_models(
    store: FeatureStore,
    sports: Sequence[str],
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    results = {sport: validate_total_score_model(store, sport) for sport in sports}
    artifacts: dict[str, str] = {}
    if artifact_dir is not None:
        destination = Path(artifact_dir)
        destination.mkdir(parents=True, exist_ok=True)
        for sport, payload in results.items():
            if payload.get("artifact_hash") is None:
                continue
            path = destination / f"{payload['model_version']}.json"
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            artifacts[sport] = str(path)
    return {
        "schema_version": "1",
        "status": "research_only",
        "sports": results,
        "artifacts": artifacts,
        "qualification_note": (
            "No sport is totals-betting qualified without timestamp-valid historical contract lines."
        ),
    }


class TotalScoreArtifact:
    def __init__(self, payload: dict[str, Any]) -> None:
        if payload.get("artifact_hash") != _artifact_hash(payload):
            raise ValueError("total-score artifact hash mismatch")
        self.payload = payload

    @classmethod
    def load(cls, path: str | Path) -> "TotalScoreArtifact":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def predict(self, features: dict[str, float]) -> float:
        missing = [name for name in self.payload["feature_names"] if name not in features]
        if missing:
            raise ValueError(f"missing total-score features: {missing}")
        value = float(self.payload["intercept"]) + sum(
            float(coefficient) * float(features[name])
            for name, coefficient in zip(
                self.payload["feature_names"], self.payload["coefficients"], strict=True
            )
        )
        return max(0.0, value)
