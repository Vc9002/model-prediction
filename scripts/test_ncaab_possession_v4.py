"""Retrospective, date-ordered NCAAB possession challenger against v3.

The pinned source release predates November 2026, but its first-seen time for
individual 2025-26 games is unknown. This is research, never a serving model.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scratch/team_box_2026.parquet"
TRAINING = ROOT / "scratch/ncaab_overhaul_training.jsonl"
OUTPUT = ROOT / "outputs/research/ncaab_possession_v4_2026-11-04.json"


def _games() -> list[dict]:
    frame = pd.read_parquet(SOURCE)
    games = []
    for game_id, pair in frame.groupby("game_id", sort=False):
        if len(pair) != 2 or set(pair.team_home_away) != {"home", "away"}:
            continue
        home = pair.loc[pair.team_home_away == "home"].iloc[0]
        away = pair.loc[pair.team_home_away == "away"].iloc[0]
        possessions = []
        for team in (home, away):
            possessions.append(
                float(team.field_goals_attempted)
                - float(team.offensive_rebounds)
                + float(team.turnovers)
                + 0.475 * float(team.free_throws_attempted)
            )
        pace = float(np.mean(possessions))
        if not 35 <= pace <= 110:
            continue
        games.append(
            {
                "id": str(game_id),
                "day": str(home.game_date),
                "home": str(home.team_id),
                "away": str(away.team_id),
                "pace": pace,
                "home_eff": 100 * float(home.team_score) / pace,
                "away_eff": 100 * float(away.team_score) / pace,
            }
        )
    return sorted(games, key=lambda game: (game["day"], game["id"]))


def _ratings(past: list[dict], prior: float = 12.0) -> dict[str, tuple[float, float, float, int]]:
    """Shrink opponent-adjusted offense, defense, and pace to league means."""
    if not past:
        return {}
    teams = sorted({g["home"] for g in past} | {g["away"] for g in past})
    index = {team: i for i, team in enumerate(teams)}
    h = np.array([index[g["home"]] for g in past])
    a = np.array([index[g["away"]] for g in past])
    participants = np.concatenate((h, a))
    count = np.bincount(participants, minlength=len(teams))
    league_eff = np.mean([g["home_eff"] + g["away_eff"] for g in past]) / 2
    league_pace = np.mean([g["pace"] for g in past])
    home_eff = np.array([g["home_eff"] for g in past]) - league_eff
    away_eff = np.array([g["away_eff"] for g in past]) - league_eff
    off = np.zeros(len(teams))
    defense = np.zeros(len(teams))
    for _ in range(20):
        off = np.bincount(
            participants,
            weights=np.concatenate((home_eff - defense[a], away_eff - defense[h])),
            minlength=len(teams),
        ) / (count + prior)
        defense = np.bincount(
            participants,
            weights=np.concatenate((away_eff - off[a], home_eff - off[h])),
            minlength=len(teams),
        ) / (count + prior)
        off -= np.average(off, weights=count)
        defense -= np.average(defense, weights=count)
    pace = np.bincount(
        participants,
        weights=np.tile(np.array([g["pace"] for g in past]), 2),
        minlength=len(teams),
    )
    pace = (pace + prior * league_pace) / (count + prior)
    return {
        team: (float(off[i]), float(defense[i]), float(pace[i]), int(count[i]))
        for i, team in enumerate(teams)
    }


def _features(games: list[dict]) -> dict[str, dict]:
    by_day = defaultdict(list)
    for game in games:
        by_day[game["day"]].append(game)
    past = []
    features = {}
    for day, current in sorted(by_day.items()):
        # Recompute only with previous completed dates, never same-day finals.
        rating = _ratings(past)
        for game in current:
            h = rating.get(game["home"], (0, 0, 70, 0))
            a = rating.get(game["away"], (0, 0, 70, 0))
            features[game["id"]] = {
                "poss_margin": (h[0] + a[1] - a[0] - h[1]) * (h[2] + a[2]) / 200,
                "poss_total": (h[0] + a[1] + a[0] + h[1]) * (h[2] + a[2]) / 200,
                "tempo": (h[2] + a[2]) / 2,
                "min_games": min(h[3], a[3]),
            }
        past.extend(current)
    return features


def _metrics(rows: list[dict], margin_model, total_model, sigma: float) -> dict:
    margin_x = np.array([[r["expected_margin"], r["poss_margin"], r["min_games"]] for r in rows])
    total_x = np.array([[r["expected_total"], r["poss_total"], r["tempo"], r["min_games"]] for r in rows])
    actual_margin = np.array([r["actual_margin"] for r in rows])
    actual_total = np.array([r["actual_total"] for r in rows])
    old_margin = np.array([r["expected_margin"] for r in rows])
    old_total = np.array([r["expected_total"] for r in rows])
    new_margin = margin_model.predict(margin_x)
    new_total = total_model.predict(total_x)
    outcome = (actual_margin > 0).astype(float)
    old_p = np.clip(norm.cdf(old_margin / sigma), 1e-6, 1 - 1e-6)
    new_p = np.clip(norm.cdf(new_margin / sigma), 1e-6, 1 - 1e-6)
    return {
        "n": len(rows),
        "margin_mae_v3": float(np.mean(abs(actual_margin - old_margin))),
        "margin_mae_v4": float(np.mean(abs(actual_margin - new_margin))),
        "total_mae_v3": float(np.mean(abs(actual_total - old_total))),
        "total_mae_v4": float(np.mean(abs(actual_total - new_total))),
        "margin_cdf_proxy_brier_v3": float(np.mean((old_p - outcome) ** 2)),
        "margin_cdf_proxy_brier_v4": float(np.mean((new_p - outcome) ** 2)),
        "margin_cdf_proxy_log_loss_v3": float(
            np.mean(-outcome * np.log(old_p) - (1 - outcome) * np.log(1 - old_p))
        ),
        "margin_cdf_proxy_log_loss_v4": float(
            np.mean(-outcome * np.log(new_p) - (1 - outcome) * np.log(1 - new_p))
        ),
        "margin_cdf_proxy_calibration_abs_v3": float(abs(np.mean(old_p) - np.mean(outcome))),
        "margin_cdf_proxy_calibration_abs_v4": float(abs(np.mean(new_p) - np.mean(outcome))),
    }


def _paired_total_bootstrap(rows: list[dict], total_model) -> dict:
    """Resample complete dates to retain within-slate error correlation."""
    inputs = np.array([[r["expected_total"], r["poss_total"], r["tempo"], r["min_games"]] for r in rows])
    challenger = total_model.predict(inputs)
    gains = np.array(
        [
            abs(r["actual_total"] - r["expected_total"]) - abs(r["actual_total"] - p)
            for r, p in zip(rows, challenger, strict=True)
        ]
    )
    days = np.array([r["date"] for r in rows])
    unique_days = np.unique(days)
    day_stats = np.array([[gains[days == day].sum(), np.sum(days == day)] for day in unique_days])
    rng = np.random.default_rng(1729)
    indices = rng.integers(0, len(unique_days), size=(10_000, len(unique_days)))
    samples = day_stats[indices].sum(axis=1)
    sampled_gains = samples[:, 0] / samples[:, 1]
    return {
        "days": len(unique_days),
        "mean_mae_gain": float(np.mean(gains)),
        "date_clustered_ci95": [float(x) for x in np.quantile(sampled_gains, [0.025, 0.975])],
        "bootstrap_fraction_positive": float(np.mean(sampled_gains > 0)),
    }


def main() -> dict:
    features = _features(_games())
    original = [json.loads(line) for line in TRAINING.read_text(encoding="utf-8").splitlines()]
    rows = [{**r, **features[str(r["event_id"])]} for r in original if str(r["event_id"]) in features]
    train = [r for r in rows if r["date"] < "2026-02-02"]
    validation = [r for r in rows if "2026-02-02" <= r["date"] < "2026-03-05"]
    later = [r for r in rows if r["date"] >= "2026-03-05"]
    margin_model = make_pipeline(StandardScaler(), Ridge(alpha=1000)).fit(
        np.array([[r["expected_margin"], r["poss_margin"], r["min_games"]] for r in train]),
        np.array([r["actual_margin"] for r in train]),
    )
    total_model = make_pipeline(StandardScaler(), Ridge(alpha=1000)).fit(
        np.array([[r["expected_total"], r["poss_total"], r["tempo"], r["min_games"]] for r in train]),
        np.array([r["actual_total"] for r in train]),
    )
    sigma = float(np.std([r["actual_margin"] - r["expected_margin"] for r in train]))
    report = {
        "source": "sportsdataverse-data team_box_2026.parquet",
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "method": "previous dates only; fixed-point opponent adjustment; 12-game shrinkage; ridge alpha 1000",
        "limitations": "Retrospective boxscore source; per-game first-seen unavailable. Moneyline Brier/log loss are margin-CDF proxies, not serving v3 probabilities. No executable price comparison.",
        "missing_boxscore_games": len(original) - len(rows),
        "train": _metrics(train, margin_model, total_model, sigma),
        "validation": _metrics(validation, margin_model, total_model, sigma),
        "later": _metrics(later, margin_model, total_model, sigma),
        "total_head_paired_bootstrap": {
            "validation": _paired_total_bootstrap(validation, total_model),
            "later": _paired_total_bootstrap(later, total_model),
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
