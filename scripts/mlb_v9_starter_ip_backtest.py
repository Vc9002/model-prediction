"""MLB v9 Step 7 -- starter-IP distribution challenger (walk-forward).

Tests the structural change pre-registered as mlb-v9-starter-ip-distribution:
the incumbent estimate multiplies the starter-weakness and bullpen-weakness
factors at full weight regardless of how many innings the starter throws.
The challenger makes the innings split explicit:

    mu_team = starter_rate x E[IP]/9 + bullpen_rate x (9 - E[IP])/9

where starter_rate = mu_incumbent / bullpen^eps_b and bullpen_rate =
mu_incumbent / starter^eps_s (elasticities from the shipped formula spec),
and E[IP] is the starter's credibility-shrunk expected innings from their
prior starts this season (K=5 prior starts toward league 5.0 IP). Fail-closed:
a starter with no resolvable prior starts this season falls back to the
incumbent mean.

Unlike the air-density signal (uniform per-game scale, moneyline-inert),
the IP split changes the home/away mean RATIO, so moneyline is a valid
target here. Same paired gamma_poisson seed-stream evaluation as the other
Step 7 harnesses; 5-fold expanding-window walk-forward, pooled date-cluster
bootstrap. No live API calls; no production paths touched.
"""

from __future__ import annotations

import argparse
import json
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
from model_prediction.domain import parse_utc
from model_prediction.models.mlb import load_formula_spec, simulate_game
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from scripts.mlb_v9_air_density_backtest import (
    FORMULA_SPEC_PATH,
    _folds,
    _Game,
    build_games,
)
from scripts.mlb_v9_distribution_backtest import (
    SNAPSHOTS_PATH,
    build_starter_history,
)

LEAGUE_STARTER_IP = 5.0
IP_PRIOR_STARTS = 5.0


def _expected_ip(starter_index: dict, starter_name: str, decision, season_year: int) -> float | None:
    """Credibility-shrunk expected innings pitched for the season to date.

    None when the starter has no resolvable prior starts this season --
    the caller must then fall back to the incumbent mean (fail-closed).
    """
    from scripts.mlb_v9_distribution_backtest import _normalize_name

    rows = starter_index.get(_normalize_name(starter_name), [])
    prior = [r for r in rows if r[0] < decision and r[0].year == season_year]
    if not prior:
        return None
    innings = [r[1] for r in prior]
    n = len(prior)
    credibility = n / (n + IP_PRIOR_STARTS)
    return credibility * mean(innings) + (1 - credibility) * LEAGUE_STARTER_IP


def _ip_split_means(item: _Game, starter_index: dict, spec) -> tuple[float, float] | None:
    """(away_mu, home_mu) for the IP-split challenger; None means fall back."""
    estimate = item.estimate
    decision = parse_utc(item.features.decision_timestamp_utc)
    year = parse_utc(item.game["event_start_utc"]).year
    # The team facing the HOME starter is the away offense, and vice versa.
    away_ip = _expected_ip(starter_index, item.features.home_starter.name, decision, year)
    home_ip = _expected_ip(starter_index, item.features.away_starter.name, decision, year)
    if away_ip is None or home_ip is None:
        return None
    factors = estimate.factors
    eps_s = float(spec.starter_weakness_elasticity)
    eps_b = float(spec.bullpen_elasticity)
    # estimate_runs multiplies the OPPOSING starter's weakness and the
    # OPPOSING bullpen's weakness into a team's expected runs (away_expected
    # uses home_starter_weakness and home_bullpen_weakness). Decompose each
    # team's incumbent mean into a per-9-innings starter rate and bullpen
    # rate by dividing out the factor that the IP split should govern:
    #   away_rate_starter = away_mu / home_bullpen^eps_b   (vs home starter)
    #   away_rate_bullpen = away_mu / home_starter^eps_s   (vs home bullpen)
    home_starter = float(factors["home_starter_weakness_index"])
    home_bp = float(factors["home_bullpen_weakness_index"])
    away_starter = float(factors["away_starter_weakness_index"])
    away_bp = float(factors["away_bullpen_weakness_index"])
    away_mu = estimate.away_expected_runs
    home_mu = estimate.home_expected_runs
    away_starter_rate = away_mu / (home_bp**eps_b)
    away_bullpen_rate = away_mu / (home_starter**eps_s)
    home_starter_rate = home_mu / (away_bp**eps_b)
    home_bullpen_rate = home_mu / (away_starter**eps_s)
    away_split = away_starter_rate * (home_ip / 9.0) + away_bullpen_rate * (9.0 - home_ip) / 9.0
    home_split = home_starter_rate * (away_ip / 9.0) + home_bullpen_rate * (9.0 - away_ip) / 9.0
    # Clamp to sane bounds around the incumbent mean, mirroring the
    # engine's factor_bounds philosophy.
    away_split = max(0.75 * away_mu, min(1.25 * away_mu, away_split))
    home_split = max(0.75 * home_mu, min(1.25 * home_mu, home_split))
    return away_split, home_split


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-date", default="2025-04-01")
    parser.add_argument("--max-games", type=int, default=None)
    args = parser.parse_args()

    spec = load_formula_spec(FORMULA_SPEC_PATH)
    starter_index = build_starter_history(SNAPSHOTS_PATH)
    games = build_games(args.min_date, args.max_games)
    folds = _folds(games)

    pooled_incumbent: list[float] = []
    pooled_candidate: list[float] = []
    pooled_outcomes: list[int] = []
    pooled_dates: list[str] = []
    inc_mae = cand_mae = 0.0
    mae_n = 0
    fallbacks = 0
    fold_reports = []

    for fold_index, (train, eval_rows) in enumerate(folds):
        del train  # no fitting in this experiment; folds only partition eval windows
        fold_inc: list[float] = []
        fold_cand: list[float] = []
        fold_out: list[int] = []
        fold_dates: list[str] = []
        fold_inc_mae = fold_cand_mae = 0.0
        fold_mae_n = 0
        for item in eval_rows:
            game = item.game
            split = _ip_split_means(item, starter_index, spec)
            if split is None:
                fallbacks += 1
                continue
            away_mu, home_mu = split
            candidate = _dc_replace(item.estimate, away_expected_runs=away_mu, home_expected_runs=home_mu)
            inc_sim = simulate_game(item.features, item.estimate, spec, seed_namespace="starter_ip")
            cand_sim = simulate_game(item.features, candidate, spec, seed_namespace="starter_ip")
            inc_home_p = float(np.mean(np.asarray(inc_sim.home_scores) > np.asarray(inc_sim.away_scores)))
            cand_home_p = float(np.mean(np.asarray(cand_sim.home_scores) > np.asarray(cand_sim.away_scores)))
            outcome = 1 if game["home_score"] > game["away_score"] else 0
            fold_inc.append(inc_home_p)
            fold_cand.append(cand_home_p)
            fold_out.append(outcome)
            fold_dates.append(game["event_start_utc"][:10])
            fold_inc_mae += abs(item.estimate.away_expected_runs - game["away_score"])
            fold_inc_mae += abs(item.estimate.home_expected_runs - game["home_score"])
            fold_cand_mae += abs(away_mu - game["away_score"])
            fold_cand_mae += abs(home_mu - game["home_score"])
            fold_mae_n += 2
        inc_metrics = calibration_metrics(fold_inc, fold_out, minimum_sample=1)
        cand_metrics = calibration_metrics(fold_cand, fold_out, minimum_sample=1)
        inc_brier = float(inc_metrics["brier_score"])
        cand_brier = float(cand_metrics["brier_score"])
        fold_reports.append(
            {
                "fold": fold_index,
                "n": len(fold_out),
                "incumbent_brier": inc_brier,
                "candidate_brier": cand_brier,
                "delta_brier": round(cand_brier - inc_brier, 6),
                "incumbent_mae": round(fold_inc_mae / fold_mae_n, 6),
                "candidate_mae": round(fold_cand_mae / fold_mae_n, 6),
            }
        )
        pooled_incumbent.extend(fold_inc)
        pooled_candidate.extend(fold_cand)
        pooled_outcomes.extend(fold_out)
        pooled_dates.extend(fold_dates)
        inc_mae += fold_inc_mae
        cand_mae += fold_cand_mae
        mae_n += fold_mae_n

    class _Row:
        __slots__ = ("date", "outcome")

        def __init__(self, date: str, outcome: int) -> None:
            self.date = date
            self.outcome = outcome

    rows = [_Row(d, o) for d, o in zip(pooled_dates, pooled_outcomes, strict=True)]
    bootstrap = _cluster_bootstrap_brier_delta(pooled_incumbent, pooled_candidate, rows, seed=20260817)
    by_date: dict[str, list[float]] = defaultdict(list)
    for inc, cand, row in zip(pooled_incumbent, pooled_candidate, rows, strict=True):
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
        "fallback_count": fallbacks,
        "min_date": args.min_date,
        "folds": fold_reports,
        "pooled": {
            "mean_delta_brier": mean_delta,
            "folds_better": folds_better,
            "bootstrap": bootstrap,
            "p_better": p_better,
            "incumbent_mae": round(inc_mae / mae_n, 6) if mae_n else None,
            "candidate_mae": round(cand_mae / mae_n, 6) if mae_n else None,
        },
    }
    out_dir = Path("outputs/research/mlb_v9_starter_ip")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"backtest_from{args.min_date}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["_file"] = str(out_path)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
