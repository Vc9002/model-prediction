"""Read-only point-in-time opponent-adjusted NCAAB score-rating ablation."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.stats import norm
from sklearn.linear_model import Ridge

from model_prediction.features.base import FeatureStore
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.basketball_leagues import ncaab_v3_model
from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs/research/ncaab_schedule_rating_2026-11-04.json"


def ratings(games: list[dict], prior_games: float = 8.0) -> dict[str, float]:
    """Ridge-like fixed-point score margins, with opponent adjustment."""
    if not games:
        return {}
    names = sorted({g["home_team"] for g in games} | {g["away_team"] for g in games})
    index = {name: i for i, name in enumerate(names)}
    home = np.array([index[g["home_team"]] for g in games])
    away = np.array([index[g["away_team"]] for g in games])
    margin = np.array([g["home_score"] - g["away_score"] - 3.0 for g in games], dtype=float)
    count = np.bincount(np.concatenate((home, away)), minlength=len(names))
    strength = np.zeros(len(names))
    for _ in range(20):
        target = np.concatenate((margin + strength[away], -margin + strength[home]))
        strength = np.bincount(np.concatenate((home, away)), weights=target, minlength=len(names)) / (
            count + prior_games
        )
        strength -= np.average(strength, weights=count + prior_games)
    return dict(zip(names, strength, strict=True))


def main() -> dict:
    games = [
        json.loads(line)
        for line in (ROOT / "data/processed/ncaab/games.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    by_day: dict[str, list[dict]] = defaultdict(list)
    for game in games:
        by_day[game["event_start_utc"][:10]].append(game)
    past: list[dict] = []
    features: dict[str, dict] = {}
    score_by_day: dict[str, dict[str, float]] = {}
    for day, current in sorted(by_day.items()):
        # Previous complete UTC dates only: no same-day final leaks.
        previous_season = [g for g in past if g["event_start_utc"][:10] < day]
        score = ratings(previous_season)
        score_by_day[day] = score
        offseason = day >= "2026-11-01"
        for game in current:
            raw = score.get(game["home_team"], 0.0) - score.get(game["away_team"], 0.0)
            features[str(game["event_id"])] = {
                "date": day,
                "srs_margin": 3.0 + (0.5 if offseason else 1.0) * raw,
                "actual_margin": game["home_score"] - game["away_score"],
            }
        past.extend(current)
    original = [
        json.loads(line)
        for line in (ROOT / "scratch/ncaab_overhaul_training.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rows = [
        {**r, "srs_margin": features[str(r["event_id"])]["srs_margin"]}
        for r in original
        if str(r["event_id"]) in features
    ]
    train = [r for r in rows if r["date"] < "2026-02-02"]
    validation = [r for r in rows if "2026-02-02" <= r["date"] < "2026-03-05"]
    later = [r for r in rows if "2026-03-05" <= r["date"]]
    model = Ridge(alpha=1000.0).fit(
        np.array([[r["expected_margin"], r["srs_margin"]] for r in train]),
        np.array([r["actual_margin"] for r in train]),
    )
    results = {}
    for label, split in (("train", train), ("validation", validation), ("later", later)):
        actual = np.array([r["actual_margin"] for r in split])
        old = np.array([r["expected_margin"] for r in split])
        new = model.predict(np.array([[r["expected_margin"], r["srs_margin"]] for r in split]))
        results[label] = {
            "n": len(split),
            "incumbent_mae": float(np.mean(np.abs(actual - old))),
            "challenger_mae": float(np.mean(np.abs(actual - new))),
        }
    db = RuntimePaths.resolve(repo_root=ROOT).ledgers_db
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    settled = conn.execute(
        "SELECT * FROM ledger_records WHERE sport='ncaab' AND ledger_tier='flat' AND status='settled'"
    ).fetchall()
    settled_events = {}
    for row in settled:
        if row["event_id"] not in settled_events:
            payload = json.loads(row["decision_payload_json"] or "{}")
            settled_events[row["event_id"]] = (
                row["event_start_utc"],
                payload["away_team"],
                payload["home_team"],
            )
    by_settled_day = defaultdict(list)
    for event_id, (start, away, home) in settled_events.items():
        by_settled_day[start[:10]].append(UpcomingGame(event_id, start, away, home, league="NCAAB"))
    live_features = {}
    for day, upcoming in by_settled_day.items():
        history = FeatureStore(ROOT / "data").games_before("ncaab", day)
        predictions = ncaab_v3_model().predict_games(history, upcoming)
        live_features.update(
            {p.event_id: p.feature_basis for p in predictions if p.market_type == "moneyline"}
        )
    replay = defaultdict(
        lambda: {
            "n": 0,
            "incumbent_pnl": 0.0,
            "challenger_calls": 0,
            "challenger_pnl": 0.0,
            "incumbent_brier_sum": 0.0,
            "challenger_brier_sum": 0.0,
        }
    )
    for row in settled:
        metric = replay[row["market_type"]]
        metric["n"] += 1
        metric["incumbent_pnl"] += row["pnl_units"] or 0.0
        if row["market_type"] == "total":
            # The proposed challenger only changes margin; total stays incumbent.
            probability = row["model_probability"]
        else:
            old = live_features[row["event_id"]]
            start, away_team, home_team = settled_events[row["event_id"]]
            day = start[:10]
            available_day = max(d for d in score_by_day if d <= day)
            score = score_by_day[available_day]
            srs = 3.0 + 0.5 * (score.get(home_team, 0.0) - score.get(away_team, 0.0))
            predicted_margin = float(model.predict([[old["expected_margin"], srs]])[0])
            if row["market_type"] == "moneyline":
                home = float(norm.cdf(predicted_margin / 10.5))
                probability = home if row["selection"] == "home" else 1 - home
            else:
                line = float(row["line"])
                if row["selection"] == "home":
                    probability = float(norm.sf((-line - predicted_margin) / 10.5))
                else:
                    probability = float(norm.cdf((line - predicted_margin) / 10.5))
        if probability > row["market_probability"]:
            metric["challenger_calls"] += 1
            metric["challenger_pnl"] += row["pnl_units"] or 0.0
        outcome = float(row["result"] == "win")
        metric["incumbent_brier_sum"] += (float(row["model_probability"]) - outcome) ** 2
        metric["challenger_brier_sum"] += (probability - outcome) ** 2
    for metric in replay.values():
        metric["incumbent_brier"] = metric.pop("incumbent_brier_sum") / metric["n"]
        metric["challenger_brier"] = metric.pop("challenger_brier_sum") / metric["n"]
    result = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "coefficients": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "historical": results,
        "settled": dict(replay),
        "note": "Same-selection retrospective settled P&L; no opposite-side or missing prices reconstructed.",
    }
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
