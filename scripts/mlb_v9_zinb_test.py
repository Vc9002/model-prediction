"""MLB v9 Step 7 -- ZINB team-score draw test (walk-forward).

Zero-inflated negative binomial challenger to the gamma_poisson draw,
pre-registered as mlb-v9-zinb-test. Everything lives in THIS harness:

  - per fold, (phi, p_zero) are fitted on TRAIN team scores by method of
    moments: p_zero = max(0, (P0 - NB_P0) / (1 - NB_P0)) style solver is
    unstable, so instead fit by matching mean, variance, and P(score=0)
    simultaneously via a small fixed-point iteration on (phi, p_zero)
    over the NB moment equations.
  - EVAL: candidate team scores drawn from ZI-NB with the incumbent
    RunEstimate means; home-win probability via elementwise comparison
    of 20k draws per game; compared to the gamma_poisson draw's home-win
    probability on the SAME games.

The production simulate_game and its stable-seed stream are untouched.
Team-level independence (no shared-environment multiplier) is a disclosed
structural difference from gamma_poisson's correlated draw.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from model_prediction.calibration import calibration_metrics
from model_prediction.models.mlb import load_formula_spec, simulate_game
from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta
from scripts.mlb_v9_air_density_backtest import (
    FORMULA_SPEC_PATH,
    _folds,
    build_games,
)

N_DRAWS = 20000


def _fit_zinb_params(scores: list[int], mean_target: float | None = None) -> tuple[float, float]:
    """(phi, p_zero) via fixed-point method-of-moments on the score list.

    ZI-NB moment identities with NB parameterized by (mean m, phi):
    mean_z = (1 - p) * m, var_z = (1 - p) * (m*phi + p*m^2), P0_z =
    p + (1 - p) * P0_nb, P0_nb = (phi/(phi+1))^... (NB zero mass with
    n = m/(phi-1), prob = 1/phi: P0_nb = prob^n = phi^(-m/(phi-1))).

    Solve by alternating: given p, m = mean/(1-p); given m, p = (P0 - P0_nb(m))/
    (1 - P0_nb(m)), clamped to [0, 0.5]; phi from the variance equation,
    clamped to [1.05, 3.0]. Iterate to a fixed point (max 50 rounds).
    """
    obs = np.asarray([float(s) for s in scores])
    if len(obs) == 0:
        return 1.2, 0.0
    mean_obs = float(obs.mean())
    var_obs = float(obs.var(ddof=0))
    p0_obs = float(np.mean(obs == 0))
    p_zero = 0.0
    phi = 1.2
    for _ in range(50):
        m = mean_obs / (1 - p_zero) if p_zero < 1 else mean_obs
        n = m / (phi - 1.0) if phi > 1.0 else 1.0
        p0_nb = float(phi ** (-n))
        p_new = (p0_obs - p0_nb) / (1 - p0_nb) if p0_nb < 1 else 0.0
        p_new = max(0.0, min(0.5, p_new))
        # variance equation: var_obs = (1-p)(m*phi + p*m^2)
        target_inner = var_obs / (1 - p_new) - p_new * m * m if p_new < 1 else var_obs
        phi_new = target_inner / m if m > 0 else 1.2
        phi_new = max(1.05, min(3.0, phi_new))
        if abs(p_new - p_zero) < 1e-6 and abs(phi_new - phi) < 1e-6:
            p_zero, phi = p_new, phi_new
            break
        p_zero, phi = p_new, phi_new
    return phi, p_zero


def _zinb_draw(rng: np.random.Generator, mean_mu: float, phi: float, p_zero: float, count: int) -> np.ndarray:
    """ZI-NB draws with the fitted shape but the GAME's incumbent mean."""
    m = mean_mu  # incumbent RunEstimate mean is the target mean
    n = m / (phi - 1.0) if phi > 1.0 else 1.0
    prob = 1.0 / phi
    nb = rng.negative_binomial(n, prob, count)
    zero_mask = rng.random(count) < p_zero
    out = np.where(zero_mask, 0, nb).astype(np.int64)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-date", default="2025-04-01")
    parser.add_argument("--max-games", type=int, default=None)
    args = parser.parse_args()

    spec = load_formula_spec(FORMULA_SPEC_PATH)
    games = build_games(args.min_date, args.max_games)
    folds = _folds(games)

    pooled_incumbent: list[float] = []
    pooled_candidate: list[float] = []
    pooled_outcomes: list[int] = []
    pooled_dates: list[str] = []
    fold_reports = []

    for fold_index, (train, eval_rows) in enumerate(folds):
        train_scores: list[int] = []
        for item in train:
            train_scores.append(item.game["home_score"])
            train_scores.append(item.game["away_score"])
        phi, p_zero = _fit_zinb_params(train_scores)
        fold_inc: list[float] = []
        fold_cand: list[float] = []
        fold_out: list[int] = []
        fold_dates: list[str] = []
        for item in eval_rows:
            game = item.game
            incumbent = item.estimate
            inc_sim = simulate_game(item.features, incumbent, spec, seed_namespace="zinb_test")
            inc_home_p = float(np.mean(np.asarray(inc_sim.home_scores) > np.asarray(inc_sim.away_scores)))
            seed = int(game["event_id"]) % (2**32)
            rng = np.random.default_rng(seed)
            home_draw = _zinb_draw(rng, incumbent.home_expected_runs, phi, p_zero, N_DRAWS)
            away_draw = _zinb_draw(rng, incumbent.away_expected_runs, phi, p_zero, N_DRAWS)
            cand_home_p = float(np.mean(home_draw > away_draw))
            outcome = 1 if game["home_score"] > game["away_score"] else 0
            fold_inc.append(inc_home_p)
            fold_cand.append(cand_home_p)
            fold_out.append(outcome)
            fold_dates.append(game["event_start_utc"][:10])
        inc_metrics = calibration_metrics(fold_inc, fold_out, minimum_sample=1)
        cand_metrics = calibration_metrics(fold_cand, fold_out, minimum_sample=1)
        inc_brier = float(inc_metrics["brier_score"])
        cand_brier = float(cand_metrics["brier_score"])
        fold_reports.append(
            {
                "fold": fold_index,
                "n": len(fold_out),
                "phi": round(phi, 4),
                "p_zero": round(p_zero, 4),
                "incumbent_brier": inc_brier,
                "candidate_brier": cand_brier,
                "delta_brier": round(cand_brier - inc_brier, 6),
            }
        )
        pooled_incumbent.extend(fold_inc)
        pooled_candidate.extend(fold_cand)
        pooled_outcomes.extend(fold_out)
        pooled_dates.extend(fold_dates)

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
        "min_date": args.min_date,
        "folds": fold_reports,
        "pooled": {
            "mean_delta_brier": mean_delta,
            "folds_better": folds_better,
            "bootstrap": bootstrap,
            "p_better": p_better,
        },
    }
    out_dir = Path("outputs/research/mlb_v9_zinb")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"test_from{args.min_date}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["_file"] = str(out_path)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
