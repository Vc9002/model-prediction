"""MLB v9 Step 7 -- air-density (temperature-deviation) walk-forward backtest.

Evaluates the shadow feature features/air_density_weather.py against the
incumbent (no temperature adjustment) on real historical games, entirely
from local cached data:

  - per game: distance factor d from that game's temperature deviation vs
    its park's month-of-season norm (indoor -> neutral 1.0, fail-closed)
  - 5-fold expanding-window walk-forward: on each fold's TRAIN window only,
    fit the elasticity beta of runs wrt the distance factor via least
    squares with offset log(incumbent expected runs) -- i.e.
    log(runs + 0.5) - log(mu) = beta * log(d), through the origin (d=1
    must mean no adjustment, no intercept)
  - on each fold's EVAL window, candidate team means = incumbent means *
    d^beta, clipped to [0.85, 1.15] of the incumbent estimate (same
    bounding philosophy as the engine's factor_bounds); candidate vs
    incumbent compared on (a) moneyline Brier via the gamma_poisson joint
    draw with the SAME seed stream (only the means change), and
    (b) team-run MAE
  - pooled date-cluster bootstrap P(candidate better), fold agreement

The elasticity is deliberately NOT hardcoded from the published +2.8
runs/5000ft anchor -- that anchor mixes altitude with park geometry and
the plan requires the run translation to be fitted walk-forward, never
asserted. The physics (density from temperature, distance exponent 0.4)
comes from the shadow module and the published sources it cites.

No production code paths are touched. No live API calls.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import replace as _dc_replace
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from model_prediction.calibration import calibration_metrics
from model_prediction.features.air_density_weather import (
    air_density_distance_factor,
)
from model_prediction.models.mlb import (
    MLBGameFeatures,
    estimate_runs,
    feature_hash,
    load_formula_spec,
    simulate_game,
)
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from scripts.mlb_v9_distribution_backtest import (
    FORMULA_SPEC_PATH,
    GAMES_PATH,
    SNAPSHOTS_PATH,
    build_starter_history,
    build_team_history,
    bullpen_profile,
    load_games,
    load_snapshot_starters,
    park_factor,
    parse_utc,
    pitcher_form_at,
    team_form_at,
    team_recent_relief_lines,
)

MAX_FACTOR = 1.15
MIN_FACTOR = 0.85


class _Game:
    __slots__ = ("distance_factor", "estimate", "factor_status", "features", "game")

    def __init__(self, game: dict, features: MLBGameFeatures, estimate, distance: dict) -> None:
        self.game = game
        self.features = features
        self.estimate = estimate
        self.distance_factor = float(distance["factor"])
        self.factor_status = str(distance["status"])


def _snapshot_extras(path: Path) -> dict[str, dict]:
    """(game_start[:16]|home|away) -> {temp_f, condition, venue_name}."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            home_name = (snap.get("home") or {}).get("team_name", "")
            away_name = (snap.get("away") or {}).get("team_name", "")
            key = str(snap.get("game_start_utc", ""))[:16] + "|" + home_name + "|" + away_name
            weather = snap.get("weather") or {}
            out[key] = {
                "temp_f": weather.get("temperature_f"),
                "condition": weather.get("condition"),
                "venue_name": snap.get("venue_name"),
            }
    return out


def build_games(min_date: str, max_games: int | None) -> list[_Game]:
    games = load_games(GAMES_PATH)
    team_history = build_team_history(games)
    starter_history = build_starter_history(SNAPSHOTS_PATH)
    extras = _snapshot_extras(SNAPSHOTS_PATH)
    spec = load_formula_spec(FORMULA_SPEC_PATH)
    starters = load_snapshot_starters(SNAPSHOTS_PATH)
    out: list[_Game] = []
    for game in games:
        date_str = game["event_start_utc"][:10]
        if date_str < min_date:
            continue
        start = parse_utc(game["event_start_utc"])
        home_name, away_name = game["home_team"], game["away_team"]
        snap_key = date_str + "T" + game["event_start_utc"][11:16] + "|" + home_name + "|" + away_name
        pair = starters.get(snap_key)
        if pair is None or not pair[0] or not pair[1]:
            continue
        home_starter_name, away_starter_name = pair
        home_form = team_form_at(team_history, home_name, start)
        away_form = team_form_at(team_history, away_name, start)
        if home_form.status != "available" or away_form.status != "available":
            continue
        home_pitcher = pitcher_form_at(starter_history, home_starter_name, start)
        away_pitcher = pitcher_form_at(starter_history, away_starter_name, start)
        if home_pitcher is None or away_pitcher is None:
            continue
        park = park_factor(home_name)
        away_bullpen = bullpen_profile(team_recent_relief_lines(away_name, start))
        home_bullpen = bullpen_profile(team_recent_relief_lines(home_name, start))
        features = MLBGameFeatures(
            event_id=str(game["event_id"]),
            event_start_utc=game["event_start_utc"],
            decision_timestamp_utc=game["event_start_utc"],
            away_team=away_name,
            home_team=home_name,
            away_form=away_form,
            home_form=home_form,
            away_starter=away_pitcher,
            home_starter=home_pitcher,
            away_bullpen_weakness=away_bullpen["bullpen_weakness_index"],
            home_bullpen_weakness=home_bullpen["bullpen_weakness_index"],
            away_bullpen_status=away_bullpen["status"],
            home_bullpen_status=home_bullpen["status"],
            park_factor=park["park_factor"],
            park_factor_status=park["status"],
            weather_factor=1.0,
            weather_status="unavailable_from_source",
            starter_confirmed=True,
            starter_status="actual",
        )
        features = _dc_replace(features, feature_snapshot_hash=feature_hash(features))
        estimate = estimate_runs(features, spec)
        extra = extras.get(snap_key) or {}
        distance = air_density_distance_factor(
            extra.get("venue_name") or home_name,
            game["event_start_utc"],
            extra.get("temp_f"),
            extra.get("condition") or "",
            snapshot_path=SNAPSHOTS_PATH,
        )
        out.append(_Game(game, features, estimate, distance))
        if max_games and len(out) >= max_games:
            break
    return out


def _folds(games: list[_Game], n_folds: int = 5) -> list[tuple[list[_Game], list[_Game]]]:
    dates = sorted({g.game["event_start_utc"][:10] for g in games})
    n_chunks = n_folds + 1
    chunk_size = max(1, len(dates) // n_chunks)
    chunks = [dates[i * chunk_size : (i + 1) * chunk_size] for i in range(n_chunks - 1)]
    chunks.append(dates[(n_chunks - 1) * chunk_size :])
    folds = []
    for i in range(1, n_chunks):
        train_dates = {d for chunk in chunks[:i] for d in chunk}
        eval_dates = set(chunks[i])
        train = [g for g in games if g.game["event_start_utc"][:10] in train_dates]
        eval_rows = [g for g in games if g.game["event_start_utc"][:10] in eval_dates]
        if train and eval_rows:
            folds.append((train, eval_rows))
    return folds[-n_folds:] if len(folds) > n_folds else folds


def _fit_elasticity(train: list[_Game]) -> float:
    xs: list[float] = []
    ys: list[float] = []
    for item in train:
        game = item.game
        d = item.distance_factor
        log_d = math.log(d)
        for mu, runs in (
            (item.estimate.away_expected_runs, game["away_score"]),
            (item.estimate.home_expected_runs, game["home_score"]),
        ):
            xs.append(log_d)
            ys.append(math.log(max(runs, 0) + 0.5) - math.log(mu))
    if not xs or all(abs(x) < 1e-12 for x in xs):
        return 0.0
    beta, *_ = np.linalg.lstsq(np.asarray(xs).reshape(-1, 1), np.asarray(ys), rcond=None)
    return float(beta[0])


def _clipped_mean(mu: float, d: float, beta: float) -> float:
    factor = d**beta
    factor = max(MIN_FACTOR, min(MAX_FACTOR, factor))
    return mu * factor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-date", default="2025-04-01")
    parser.add_argument("--max-games", type=int, default=None)
    args = parser.parse_args()

    games = build_games(args.min_date, args.max_games)
    folds = _folds(games)
    spec = load_formula_spec(FORMULA_SPEC_PATH)

    pooled_incumbent_prob: list[float] = []
    pooled_candidate_prob: list[float] = []
    pooled_outcomes: list[int] = []
    pooled_dates: list[str] = []
    incumbent_mae_total = 0.0
    candidate_mae_total = 0.0
    mae_n = 0
    fold_reports = []

    for fold_index, (train, eval_rows) in enumerate(folds):
        beta = _fit_elasticity(train)
        fold_incumbent: list[float] = []
        fold_candidate: list[float] = []
        fold_outcomes: list[int] = []
        fold_dates: list[str] = []
        fold_incumbent_mae = 0.0
        fold_candidate_mae = 0.0
        fold_mae_n = 0
        for item in eval_rows:
            game = item.game
            incumbent = item.estimate
            away_mu = _clipped_mean(incumbent.away_expected_runs, item.distance_factor, beta)
            home_mu = _clipped_mean(incumbent.home_expected_runs, item.distance_factor, beta)
            candidate_estimate = _dc_replace(
                incumbent, away_expected_runs=away_mu, home_expected_runs=home_mu
            )
            inc_sim = simulate_game(item.features, incumbent, spec, seed_namespace="air_density_backtest")
            cand_sim = simulate_game(
                item.features, candidate_estimate, spec, seed_namespace="air_density_backtest"
            )
            # NOTE: these are Python lists -- plain `>` on two lists is a
            # LEXICOGRAPHIC comparison (single bool), not elementwise. The
            # first version of this harness had exactly that bug and produced
            # absurd 0.0/1.0 probabilities; np.asarray first, always.
            inc_home_p = float(np.mean(np.asarray(inc_sim.home_scores) > np.asarray(inc_sim.away_scores)))
            cand_home_p = float(np.mean(np.asarray(cand_sim.home_scores) > np.asarray(cand_sim.away_scores)))
            outcome = 1 if game["home_score"] > game["away_score"] else 0
            fold_incumbent.append(inc_home_p)
            fold_candidate.append(cand_home_p)
            fold_outcomes.append(outcome)
            fold_dates.append(game["event_start_utc"][:10])
            fold_incumbent_mae += abs(incumbent.away_expected_runs - game["away_score"])
            fold_incumbent_mae += abs(incumbent.home_expected_runs - game["home_score"])
            fold_candidate_mae += abs(away_mu - game["away_score"])
            fold_candidate_mae += abs(home_mu - game["home_score"])
            fold_mae_n += 2
        inc_metrics = calibration_metrics(fold_incumbent, fold_outcomes, minimum_sample=1)
        cand_metrics = calibration_metrics(fold_candidate, fold_outcomes, minimum_sample=1)
        inc_brier = float(inc_metrics["brier_score"])
        cand_brier = float(cand_metrics["brier_score"])
        fold_reports.append(
            {
                "fold": fold_index,
                "beta": round(beta, 6),
                "n": len(fold_outcomes),
                "incumbent_brier": inc_brier,
                "candidate_brier": cand_brier,
                "delta_brier": round(cand_brier - inc_brier, 6),
                "incumbent_mae": round(fold_incumbent_mae / fold_mae_n, 6),
                "candidate_mae": round(fold_candidate_mae / fold_mae_n, 6),
            }
        )
        pooled_incumbent_prob.extend(fold_incumbent)
        pooled_candidate_prob.extend(fold_candidate)
        pooled_outcomes.extend(fold_outcomes)
        pooled_dates.extend(fold_dates)
        incumbent_mae_total += fold_incumbent_mae
        candidate_mae_total += fold_candidate_mae
        mae_n += fold_mae_n

    class _Row:
        __slots__ = ("date", "outcome")

        def __init__(self, date: str, outcome: int) -> None:
            self.date = date
            self.outcome = outcome

    rows = [_Row(d, o) for d, o in zip(pooled_dates, pooled_outcomes, strict=True)]
    bootstrap = _cluster_bootstrap_brier_delta(
        pooled_incumbent_prob, pooled_candidate_prob, rows, seed=20260817
    )
    by_date: dict[str, list[float]] = defaultdict(list)
    for inc, cand, row in zip(pooled_incumbent_prob, pooled_candidate_prob, rows, strict=True):
        by_date[row.date].append((cand - row.outcome) ** 2 - (inc - row.outcome) ** 2)
    rng = random.Random(20260817)
    dates_sorted = sorted(by_date)
    better = 0
    for _ in range(2000):
        sampled = [rng.choice(dates_sorted) for _ in dates_sorted]
        vals = [v for day in sampled for v in by_date[day]]
        if mean(vals) < 0:
            better += 1
    p_better = round(better / 2000, 4)

    folds_better = sum(1 for f in fold_reports if f["delta_brier"] < 0)
    mean_delta = round(mean(f["delta_brier"] for f in fold_reports), 6)

    report = {
        "n_games": len(pooled_outcomes),
        "min_date": args.min_date,
        "folds": fold_reports,
        "pooled": {
            "mean_delta_brier": mean_delta,
            "folds_better": folds_better,
            "bootstrap": bootstrap,
            "p_better": p_better,
            "incumbent_mae": round(incumbent_mae_total / mae_n, 6) if mae_n else None,
            "candidate_mae": round(candidate_mae_total / mae_n, 6) if mae_n else None,
        },
    }
    out_dir = Path("outputs/research/mlb_v9_air_density")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"backtest_from{args.min_date}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["_file"] = str(out_path)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
