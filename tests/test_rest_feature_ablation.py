"""Test b2b / rest / b2b+rest filters on frozen production models.

Run:  PYTHONPATH=src .venv/bin/python tests/test_rest_feature_ablation.py
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from model_prediction.features.base import FeatureStore
from model_prediction.features.elo_ratings import build_elo
from model_prediction.features.trends import TrendEngine
from model_prediction.validation import ValidationRow, chronological_split
from model_prediction.models.learned_market import LearnedMarketArtifact

ARTIFACTS = {
    "mlb":  "config/models/mlb-elo-trend-lr-v3.json",
    "wnba": "config/models/wnba-elo-trend-lr-v3.json",
    "nba":  "config/models/nba-elo-trend-lr-v3.json",
    "nfl":  "config/models/nfl-elo-trend-lr-v3.json",
}

FILTERS = [
    "baseline (frozen model, no filter)",
    "+b2b (suppress if home on back-to-back)",
    "+rest (suppress if rest_disparity ≤ -3)",
    "+rest (suppress if rest_disparity ≤ -2)",
    "+b2b+rest (suppress if b2b OR rest≤-2)",
]

# ── helpers ─────────────────────────────────────────────────────────

def _rest_days(team: str, game_start: datetime, team_dates: dict[str, list[datetime]]) -> int | None:
    dates = team_dates.get(team, [])
    prior = [d for d in dates if d < game_start]
    return (game_start.date() - max(prior).date()).days if prior else None


def build_holdout(store: FeatureStore, sport: str, artifact_path: str):
    artifact = LearnedMarketArtifact.load(artifact_path)
    threshold = artifact.threshold("moneyline")
    mm = artifact.raw["market_models"]["moneyline"]
    coeffs = dict(zip(mm["feature_names"], mm["coefficients"]))
    intercept = mm["intercept"]

    games = store.load_games(sport)
    by_date: dict[str, list] = defaultdict(list)
    for g in games:
        by_date[g.start.date().isoformat()].append(g)

    team_dates: dict[str, list[datetime]] = defaultdict(list)
    for g in games:
        for t in (g.home_team, g.away_team):
            team_dates[t].append(g.start)

    history, all_rows, all_extra = [], [], []
    for day in sorted(by_date):
        day_games = sorted(by_date[day], key=lambda g: (g.start, g.event_id))
        if len(history) >= 50:
            elo = build_elo(history, sport)
            trends = TrendEngine(history)
            for g in day_games:
                if g.home_score == g.away_score:
                    continue
                ht = trends.team_trend(g.home_team)
                at = trends.team_trend(g.away_team)

                feat = {}
                if "elo_probability" in coeffs:
                    feat["elo_probability"] = elo.expected_home_win(g.home_team, g.away_team)
                if "trend_gap" in coeffs:
                    feat["trend_gap"] = ht.offensive_momentum - at.offensive_momentum
                if "defensive_trend_gap" in coeffs:
                    feat["defensive_trend_gap"] = ht.defensive_momentum - at.defensive_momentum
                if "park_factor" in coeffs:
                    from model_prediction.features.park_factors import park_factor
                    feat["park_factor"] = float(park_factor(g.home_team).get("park_factor", 1.0))
                if "weather_factor" in coeffs:
                    feat["weather_factor"] = 1.0
                if "pitcher_era_gap" in coeffs:
                    feat["pitcher_era_gap"] = 0.0

                logodds = intercept + sum(coeffs.get(k, 0) * feat.get(k, 0) for k in coeffs)
                prob = 1 / (1 + math.exp(-logodds))

                all_rows.append(ValidationRow(
                    date=day, event_id=g.event_id,
                    outcome=int(g.home_score > g.away_score),
                    elo_probability=feat.get("elo_probability", 0.5),
                    trend_gap=feat.get("trend_gap", 0),
                    defensive_trend_gap=feat.get("defensive_trend_gap", 0),
                    park_factor=feat.get("park_factor", 1.0),
                    weather_factor=feat.get("weather_factor", 1.0),
                    park_available="park_factor" in coeffs,
                    weather_available="weather_factor" in coeffs,
                ))

                hr = _rest_days(g.home_team, g.start, team_dates)
                ar = _rest_days(g.away_team, g.start, team_dates)
                all_extra.append({
                    "home_rest": hr if hr is not None else -1,
                    "away_rest": ar if ar is not None else -1,
                    "rest_disparity": (hr - ar) if (hr is not None and ar is not None) else 0.0,
                    "home_b2b": 1 if (hr is not None and hr <= 1) else 0,
                    "frozen_prob": prob,
                    "frozen_call": max(prob, 1 - prob) >= threshold,
                })
        history.extend(day_games)

    _, _, holdout_rows, _ = chronological_split(all_rows)
    hd = set(r.date for r in holdout_rows)
    return [e for e, r in zip(all_extra, all_rows) if r.date in hd], holdout_rows, threshold


def apply(extra, rows, threshold, fname):
    selected = []
    for ext, row in zip(extra, rows):
        prob = ext["frozen_prob"]
        if max(prob, 1 - prob) < threshold:
            continue
        keep = True
        if "+b2b" in fname and "+rest" not in fname:
            if ext["home_b2b"]:
                keep = False
        elif "+rest" in fname and "+b2b" not in fname:
            limit = -3 if "≤ -3" in fname else -2
            if ext["rest_disparity"] <= limit:
                keep = False
        elif "+b2b+rest" in fname:
            if ext["home_b2b"] or ext["rest_disparity"] <= -2:
                keep = False
        if keep:
            outcome = row.outcome if prob >= 0.5 else 1 - row.outcome
            selected.append((prob, outcome))

    c = len(selected)
    h = sum(o for _, o in selected)
    return {"calls": c, "hits": h, "hit_rate": h / c if c else 0,
            "units": round(h * 10/11 - (c - h), 2), "suppressed": 0}


# ── run ─────────────────────────────────────────────────────────────

def run():
    results = {}
    for sport in ["mlb", "wnba", "nba", "nfl"]:
        store = FeatureStore("data")
        extra, rows, threshold = build_holdout(store, sport, ARTIFACTS[sport])
        baseline_calls = sum(1 for e in extra if e["frozen_call"])
        sport_res = {}
        for fname in FILTERS:
            r = apply(extra, rows, threshold, fname)
            r["suppressed"] = baseline_calls - r["calls"]
            sport_res[fname] = r
        results[sport] = {"baseline_calls": baseline_calls, "filters": sport_res}
    return results


def print_table(results):
    for sport in ["mlb", "wnba", "nba", "nfl"]:
        r = results[sport]
        base = r["filters"][FILTERS[0]]
        print(f"\n── {sport.upper()} (frozen model, {r['baseline_calls']} baseline calls) ──")
        print(f"  {'Filter':<55} {'Calls':>6} {'Hit%':>7} {'Units':>8} {'ΔU':>8} {'Suppr':>6}")
        print(f"  {'─'*92}")
        for fn in FILTERS:
            f = r["filters"][fn]
            d = f["units"] - base["units"]
            mark = " ← baseline" if fn == FILTERS[0] else ""
            print(f"  {fn:<55} {f['calls']:>6} {f['hit_rate']:>6.1%} {f['units']:>+8.2f} {d:>+8.2f} {f['suppressed']:>6}{mark}")


if __name__ == "__main__":
    results = run()
    print_table(results)
    print(f"\n{'='*80}")
    print("  Done. All four leagues tested against updated v3 models.")
    print(f"{'='*80}")
