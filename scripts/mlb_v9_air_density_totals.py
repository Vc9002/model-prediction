"""MLB v9 Step 7 -- air-density temperature-deviation, TOTALS evaluation.

The moneyline evaluation (scripts/mlb_v9_air_density_backtest.py) showed the
signal is structurally inert for moneyline (uniform mean scaling preserves
P(home)) -- its only possible effect is on TOTALS, where absolute run counts
matter. This script runs that experiment against the reconstructed market
total line (data/historical/mlb_market_lines_reconstructed.jsonl, the only
local line source, which covers 2026-07+):

  - TRAIN (2025-04-01 .. 2026-06-30): fit the elasticity beta exactly as the
    moneyline backtest does (offset least squares, through origin)
  - EVAL (2026-07-01+, every game with a market total line): candidate
    adjusted means (d^beta, clipped) vs incumbent means, each drawn through
    gamma_poisson; P(over) = mean(away+home > line) elementwise
  - primary metric: exact-line Brier vs the market line, incumbent vs
    candidate, pooled + date-cluster bootstrap (seed 20260817)
  - reference: the market's own no-vig over probability (from American odds)
    so the result reads as "does the temperature adjustment move the model
    toward or away from the market's totals view"

No live API calls; no production paths touched.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from model_prediction.calibration import calibration_metrics
from model_prediction.models.mlb import load_formula_spec, simulate_game
from scripts.mlb_v9_air_density_backtest import (
    FORMULA_SPEC_PATH,
    _clipped_mean,
    _fit_elasticity,
    build_games,
)
from scripts.mlb_v9_distribution_backtest import _cluster_bootstrap_brier_delta

MARKET_LINES_PATH = Path("data/historical/mlb_market_lines_reconstructed.jsonl")
TRAIN_MIN_DATE = "2025-04-01"
TRAIN_MAX_DATE = "2026-06-30"
EVAL_MIN_DATE = "2026-07-01"


def _load_total_lines(path: Path) -> dict[str, dict]:
    """event_id -> {line, over_american_odds, under_american_odds}."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            total = (row.get("markets") or {}).get("total") or {}
            over_line = (total.get("over") or {}).get("line")
            if over_line is None:
                continue
            out[str(row["event_id"])] = {
                "line": float(over_line),
                "over_odds": (total.get("over") or {}).get("american_odds"),
                "under_odds": (total.get("under") or {}).get("american_odds"),
            }
    return out


def _american_to_prob(odds: int | None) -> float | None:
    if odds is None:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def _no_vig_over_prob(over_odds: int | None, under_odds: int | None) -> float | None:
    po = _american_to_prob(over_odds)
    pu = _american_to_prob(under_odds)
    if po is None or pu is None or po + pu == 0:
        return None
    return po / (po + pu)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-games", type=int, default=None)
    args = parser.parse_args()

    lines = _load_total_lines(MARKET_LINES_PATH)
    spec = load_formula_spec(FORMULA_SPEC_PATH)

    train_games = build_games(TRAIN_MIN_DATE, None)
    train_games = [g for g in train_games if g.game["event_start_utc"][:10] <= TRAIN_MAX_DATE]
    beta = _fit_elasticity(train_games)
    print(f"train games: {len(train_games)}, beta={beta:.6f}")

    eval_games = build_games(EVAL_MIN_DATE, args.max_games)
    eval_games = [g for g in eval_games if str(g.game["event_id"]) in lines]

    inc_probs: list[float] = []
    cand_probs: list[float] = []
    market_probs: list[float] = []
    outcomes: list[float] = []
    dates: list[str] = []

    for item in eval_games:
        game = item.game
        line_info = lines[str(game["event_id"])]
        line = line_info["line"]
        incumbent = item.estimate
        away_mu = _clipped_mean(incumbent.away_expected_runs, item.distance_factor, beta)
        home_mu = _clipped_mean(incumbent.home_expected_runs, item.distance_factor, beta)
        candidate = replace(incumbent, away_expected_runs=away_mu, home_expected_runs=home_mu)
        inc_sim = simulate_game(item.features, incumbent, spec, seed_namespace="air_density_totals")
        cand_sim = simulate_game(item.features, candidate, spec, seed_namespace="air_density_totals")
        inc_totals = np.asarray(inc_sim.away_scores) + np.asarray(inc_sim.home_scores)
        cand_totals = np.asarray(cand_sim.away_scores) + np.asarray(cand_sim.home_scores)
        inc_p_over = float(np.mean(inc_totals > line))
        cand_p_over = float(np.mean(cand_totals > line))
        actual_total = game["home_score"] + game["away_score"]
        outcome = 1.0 if actual_total > line else (0.5 if actual_total == line else 0.0)
        inc_probs.append(inc_p_over)
        cand_probs.append(cand_p_over)
        outcomes.append(outcome)
        dates.append(game["event_start_utc"][:10])
        no_vig = _no_vig_over_prob(line_info["over_odds"], line_info["under_odds"])
        if no_vig is not None:
            market_probs.append(no_vig)
        else:
            market_probs.append(float("nan"))

    class _Row:
        __slots__ = ("date", "outcome")

        def __init__(self, date: str, outcome: float) -> None:
            self.date = date
            self.outcome = outcome

    rows = [_Row(d, float(o)) for d, o in zip(dates, outcomes, strict=True)]
    bootstrap = _cluster_bootstrap_brier_delta(inc_probs, cand_probs, rows, seed=20260817)
    by_date: dict[str, list[float]] = defaultdict(list)
    for inc, cand, row in zip(inc_probs, cand_probs, rows, strict=True):
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

    # Exact-line Brier computed directly: the push outcome (0.5) is not
    # binary, so calibration_metrics (binary-only) is run on non-push rows
    # while the headline Brier uses the exact-line outcome for every row.
    def _exact_line_brier(probs: list[float]) -> float:
        return sum((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(outcomes)

    non_push = [i for i, o in enumerate(outcomes) if o in (0.0, 1.0)]
    inc_metrics = calibration_metrics(
        [inc_probs[i] for i in non_push], [float(outcomes[i]) for i in non_push], minimum_sample=1
    )
    cand_metrics = calibration_metrics(
        [cand_probs[i] for i in non_push], [float(outcomes[i]) for i in non_push], minimum_sample=1
    )
    inc_metrics["exact_line_brier"] = round(_exact_line_brier(inc_probs), 6)
    cand_metrics["exact_line_brier"] = round(_exact_line_brier(cand_probs), 6)
    market_only = [(p, o) for p, o in zip(market_probs, outcomes, strict=True) if not math.isnan(p)]
    market_metrics = None
    if len(market_only) >= 30:
        non_push_market = [(p, o) for p, o in market_only if o in (0.0, 1.0)]
        if non_push_market:
            market_metrics = calibration_metrics(
                [p for p, _ in non_push_market], [o for _, o in non_push_market], minimum_sample=1
            )
            market_metrics["exact_line_brier"] = round(
                sum((p - o) ** 2 for p, o in market_only) / len(market_only), 6
            )

    report = {
        "n_eval_games": len(rows),
        "train_games": len(train_games),
        "beta": beta,
        "incumbent": inc_metrics,
        "candidate": cand_metrics,
        "market_reference": market_metrics,
        "candidate_vs_incumbent_bootstrap": bootstrap,
        "candidate_vs_incumbent_p_better": p_better,
    }
    out_dir = Path("outputs/research/mlb_v9_air_density")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "totals_eval.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    report["_file"] = str(out_path)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
