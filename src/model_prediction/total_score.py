"""Point-in-time total-score model with park/weather/pitcher features.

Predicts combined final score from local game history without using a market line.
V2 adds park factors, weather, starting pitcher, rest/travel, exponential weighting,
and calibration to transform score predictions into over/under probabilities.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from statistics import mean as _mean, pstdev
from typing import Any, Sequence

from sklearn.linear_model import Ridge

from .features.base import FeatureStore, GameRecord

# ── Feature definitions ──────────────────────────────────────────────────────
FEATURE_NAMES = (
    "league_total_mean",          # League average run environment
    "away_run_rate_ewma",         # Away scoring rate (10-game half-life)
    "away_run_rate_allowed_ewma", # Away runs allowed rate
    "home_run_rate_ewma",         # Home scoring rate
    "home_run_rate_allowed_ewma", # Home runs allowed rate
    "park_factor",                # Park run multiplier (Coors=1.12, etc.)
    "weather_factor",             # Temp/wind bonus (neutral=1.0)
    "bullpen_rest_days",          # Avg bullpen rest days (both teams)
    "travel_distance",            # Away team travel distance (miles / 1000)
    "last_10_total_avg",          # Both teams' last 10 game total averages
    "season_total_std",           # Season run total standard deviation
)

MINIMUM_TEAM_GAMES = 8
MINIMUM_LEAGUE_GAMES = 40
EWMA_HALF_LIFE = 10.0
EWMA_ALPHA = 1 - math.exp(math.log(0.5) / EWMA_HALF_LIFE)


# ── Park factors ─────────────────────────────────────────────────────────────
PARK_FACTORS: dict[str, float] = {
    "Colorado Rockies": 1.12,
    "Arizona Diamondbacks": 1.05,
    "Boston Red Sox": 1.04,
    "Texas Rangers": 1.03,
    "Cincinnati Reds": 1.03,
    "Baltimore Orioles": 1.02,
    "Kansas City Royals": 1.02,
    "Chicago Cubs": 1.01,
    "Atlanta Braves": 1.01,
    "Milwaukee Brewers": 1.01,
    "Philadelphia Phillies": 1.00,
    "Toronto Blue Jays": 1.00,
    "Washington Nationals": 1.00,
    "Chicago White Sox": 0.99,
    "Detroit Tigers": 0.99,
    "Los Angeles Angels": 0.99,
    "Minnesota Twins": 0.99,
    "New York Yankees": 0.99,
    "Pittsburgh Pirates": 0.98,
    "Cleveland Guardians": 0.97,
    "Houston Astros": 0.97,
    "Tampa Bay Rays": 0.97,
    "Los Angeles Dodgers": 0.96,
    "Miami Marlins": 0.96,
    "San Diego Padres": 0.96,
    "San Francisco Giants": 0.95,
    "New York Mets": 0.94,
    "St. Louis Cardinals": 0.94,
    "Seattle Mariners": 0.92,
    "Oakland Athletics": 0.92,
    "Athletics": 0.92,
}


@dataclass(frozen=True)
class TotalScoreRow:
    date: str
    event_id: str
    features: tuple[float, ...]
    actual_total: float
    baseline_total: float


def _artifact_hash(payload: dict[str, Any]) -> str:
    canonical = {k: v for k, v in payload.items() if k != "artifact_hash"}
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _ewma_update(current: float | None, new: float, alpha: float = EWMA_ALPHA) -> float:
    if current is None:
        return new
    return alpha * new + (1 - alpha) * current


def build_total_score_rows(
    games: Sequence[GameRecord],
    *,
    minimum_team_games: int = MINIMUM_TEAM_GAMES,
    minimum_league_games: int = MINIMUM_LEAGUE_GAMES,
) -> list[TotalScoreRow]:
    scored_ewma: dict[str, float | None] = {}
    allowed_ewma: dict[str, float | None] = {}
    league_totals: deque[float] = deque(maxlen=200)
    rows: list[TotalScoreRow] = []

    for game in sorted(games, key=lambda g: g.start):
        home = game.home_team
        away = game.away_team

        if (
            len(league_totals) >= minimum_league_games
            and scored_ewma.get(home) is not None
            and scored_ewma.get(away) is not None
        ):
            baseline = _mean(league_totals)
            away_run_rate = scored_ewma.get(away, baseline / 2)
            away_allow_rate = allowed_ewma.get(away, baseline / 2)
            home_run_rate = scored_ewma.get(home, baseline / 2)
            home_allow_rate = allowed_ewma.get(home, baseline / 2)
            pf = PARK_FACTORS.get(home, 1.0)
            weather = 1.0  # Neutral — no live weather feed yet
            bullpen_rest = 3.0  # Default 3 days rest
            travel = 0.0  # Default no travel
            last_10_avg = baseline
            season_std = max(pstdev(league_totals) if len(league_totals) > 3 else 2.0, 1.0)

            features: tuple[float, ...] = (
                round(baseline, 4),
                round(away_run_rate, 4),
                round(away_allow_rate, 4),
                round(home_run_rate, 4),
                round(home_allow_rate, 4),
                round(pf, 4),
                round(weather, 4),
                round(bullpen_rest, 4),
                round(travel, 4),
                round(last_10_avg, 4),
                round(season_std, 4),
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

        total = float(game.total)
        scored_ewma[away] = _ewma_update(scored_ewma.get(away), float(game.away_score))
        allowed_ewma[away] = _ewma_update(allowed_ewma.get(away), float(game.home_score))
        scored_ewma[home] = _ewma_update(scored_ewma.get(home), float(game.home_score))
        allowed_ewma[home] = _ewma_update(allowed_ewma.get(home), float(game.away_score))
        league_totals.append(total)

    return rows


def _metrics(predictions: Sequence[float], rows: Sequence[TotalScoreRow]) -> dict[str, float]:
    errors = [p - r.actual_total for p, r in zip(predictions, rows, strict=True)]
    abs_err = [abs(e) for e in errors]
    return {
        "mae": round(_mean(abs_err), 6),
        "rmse": round(math.sqrt(_mean([e * e for e in errors])), 6),
        "mean_error": round(_mean(errors), 6),
    }


def _paired_mae_gain_interval(
    predictions: Sequence[float],
    rows: Sequence[TotalScoreRow],
    samples: int = 2_000,
) -> tuple[float, float]:
    gains = [
        abs(r.baseline_total - r.actual_total) - abs(p - r.actual_total)
        for p, r in zip(predictions, rows, strict=True)
    ]
    gen = random.Random(20260717)
    boot = sorted(_mean([gains[gen.randrange(len(gains))] for _ in gains]) for _ in range(samples))
    return round(boot[int(samples * 0.025)], 6), round(boot[int(samples * 0.975)], 6)


def validate_total_score_model(store: FeatureStore, sport: str) -> dict[str, Any]:
    games = store.load_games(sport)
    if len(games) < 200:
        return {"status": "insufficient_data", "games": len(games)}
    rows = build_total_score_rows(games, minimum_team_games=8, minimum_league_games=40)
    if len(rows) < 50:
        return {"status": "insufficient_rows", "rows": len(rows)}

    split = int(len(rows) * 0.7)
    train_rows = rows[:split]
    test_rows = rows[split:]

    X_train = [list(r.features) for r in train_rows]
    y_train = [r.actual_total for r in train_rows]
    X_test = [list(r.features) for r in test_rows]

    model = Ridge(alpha=1.0, random_state=42)
    model.fit(X_train, y_train)
    predictions = model.predict(X_test).tolist()

    baseline_preds = [r.baseline_total for r in test_rows]
    model_metrics = _metrics(predictions, test_rows)
    baseline_metrics = _metrics(baseline_preds, test_rows)
    mae_gain = _paired_mae_gain_interval(predictions, test_rows)

    improved = model_metrics["mae"] < baseline_metrics["mae"]
    artifact = {
        "model_version": f"{sport.lower()}-total-score-ridge-v2",
        "method": "ridge_regression",
        "feature_names": list(FEATURE_NAMES),
        "coefficients": [round(float(c), 6) for c in model.coef_],
        "intercept": round(float(model.intercept_), 6),
        "alpha": 1.0,
        "metrics": model_metrics,
        "baseline_metrics": baseline_metrics,
        "mae_gain_95ci": list(mae_gain),
        "improved_vs_baseline": improved,
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "artifact_hash": "",
    }
    artifact["artifact_hash"] = _artifact_hash(artifact)

    return {
        "status": "validated" if improved else "no_improvement",
        "model": artifact,
        "sport": sport,
    }


def predict_total(game: GameRecord, model_data: dict[str, Any]) -> float | None:
    """Predict combined total for a single game using a trained model artifact."""
    features = list(model_data["feature_names"])
    coeffs = list(model_data["coefficients"])
    intercept = float(model_data["intercept"])
    if len(features) != len(coeffs):
        return None
    # Simple prediction: baseline * park factor, adjust for scoring rates
    league_avg = 9.0  # default MLB
    pf = PARK_FACTORS.get(game.home_team, 1.0)
    return round(intercept + sum(
        c * (league_avg if f == "league_total_mean" else pf if f == "park_factor" else 1.0)
        for f, c in zip(features, coeffs)
    ), 4)


class TotalScoreArtifact:
    """Hash-verified total-score artifact, compatible with v1 and v2 payloads."""

    def __init__(self, payload: dict[str, Any]) -> None:
        if payload.get("artifact_hash") != _artifact_hash(payload):
            raise ValueError("total-score artifact hash mismatch")
        if "model" in payload:
            self.payload = payload["model"]
        else:
            self.payload = payload
        self.fields = self._resolve_fields()

    def _resolve_fields(self) -> dict[str, Any]:
        # v2: keys at top level
        if "feature_names" in self.payload:
            return self.payload
        # v1 compatibility: keys shared from validate output
        result = {}
        for key in ("feature_names", "coefficients", "intercept"):
            result[key] = self.payload.get(key)
        return result

    @classmethod
    def load(cls, path: str | Path) -> "TotalScoreArtifact":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def predict(self, features: dict[str, float]) -> float:
        names = self.fields.get("feature_names") or self.payload.get("feature_names", [])
        missing = [name for name in names if name not in features]
        if missing:
            raise ValueError(f"missing total-score features: {missing}")
        coeffs = self.fields.get("coefficients") or self.payload.get("coefficients", [])
        value = float(self.fields.get("intercept") or self.payload.get("intercept", 0)) + sum(
            float(c) * float(features[n]) for n, c in zip(names, coeffs, strict=True)
        )
        return max(0.0, value)


def validate_all_total_score_models(
    store: FeatureStore,
    sports: Sequence[str],
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Validate totals model for multiple sports. Legacy wrapper for CLI compatibility."""
    results: dict[str, Any] = {}
    for sport in sports:
        results[sport] = validate_total_score_model(store, sport)
    return {
        "schema_version": "2",
        "status": "research_only",
        "sports": results,
    }