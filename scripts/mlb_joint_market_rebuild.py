"""MLB joint ML/runline/totals rebuild (Step 7 tail).

One coherent distribution per game -- the gamma_poisson champion from the
closed Step-7 distribution comparison -- priced against reconstructed
opening lines (data/historical/mlb_market_lines_reconstructed.jsonl,
DraftKings open, 2026-07-01..2026-08-01, 340 games) for all three markets
at once.

DATA CAVEAT (verified 2026-08-18): every row in that line archive carries
timestamp_valid=false and observed_at_utc AFTER its game -- the "opening"
lines are ESPN's POSTGAME pickcenter reconstruction of the open, with
provider attribution secondhand via ESPN. The line VALUES are ESPN's own
recorded pre-game opens (directionally usable), but the timing metadata
must never be treated as decision-time evidence, and any model-vs-market
conclusion from this file is provisional until a real pre-game odds
archive exists. Model-vs-model comparisons in this script do NOT inherit
that weakness (both sides share the same feature data).

  - moneyline: P(home) / (P(home)+P(away)) from the joint score draw
  - runline: P(away cover) at the market's own away spread line
  - totals: P(over) at the market's own total line, exact-line (push=0.5)

vs the market's own no-vig probabilities per market. Then the log-score
stacking challenger: p_stack = w*p_model + (1-w)*p_market with w grid-
searched on a chronological validation split (date-based 60/20/20) and
evaluated once on the locked holdout -- the same stacking treatment as the
market-as-prior shrinkage work, now inside the joint engine.

Reproduction gate: the handoff documents "model beats market's no-vig
totals line on the 171-game 2026-07 window (0.238 vs 0.242)" -- this
script prints that same 2026-07-only slice so the incumbent numbers are
reproduced, not assumed. No production paths touched; local cached data
only.
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from model_prediction.models.mlb import load_formula_spec, simulate_game
from scripts.mlb_v9_air_density_backtest import build_games
from scripts.mlb_v9_distribution_backtest import FORMULA_SPEC_PATH

MARKET_LINES_PATH = Path("data/historical/mlb_market_lines_reconstructed.jsonl")
EVAL_MIN_DATE = "2026-07-01"
EVAL_MAX_DATE = "2026-08-02"  # exclusive
STACK_GRID = tuple(round(x * 0.05, 2) for x in range(21))


def _load_market_lines(path: Path) -> dict[str, dict]:
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
            markets = row.get("markets") or {}
            moneyline = markets.get("moneyline") or {}
            spread = markets.get("spread") or {}
            total = markets.get("total") or {}
            if not all(markets.get(k) for k in ("moneyline", "spread", "total")):
                continue
            out[str(row["event_id"])] = {
                "moneyline": {
                    "home": (moneyline.get("home") or {}).get("american_odds"),
                    "away": (moneyline.get("away") or {}).get("american_odds"),
                },
                "spread": {
                    "away_line": (spread.get("away") or {}).get("line"),
                    "away": (spread.get("away") or {}).get("american_odds"),
                    "home": (spread.get("home") or {}).get("american_odds"),
                },
                "total": {
                    "line": (total.get("over") or {}).get("line"),
                    "over": (total.get("over") or {}).get("american_odds"),
                    "under": (total.get("under") or {}).get("american_odds"),
                },
            }
    return out


def _american_to_prob(odds) -> float | None:
    if odds is None:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def _no_vig(p1: float | None, p2: float | None) -> float | None:
    if p1 is None or p2 is None or p1 + p2 == 0:
        return None
    return p1 / (p1 + p2)


def _exact_line_brier(probs: list[float], outcomes: list[float]) -> float:
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(outcomes)


def _log_score(probs: list[float], outcomes: list[float]) -> float:
    clipped = [min(1 - 1e-6, max(1e-6, p)) for p in probs]
    return -mean(o * math.log(p) + (1 - o) * math.log(1 - p) for p, o in zip(clipped, outcomes, strict=True))


def main() -> int:
    lines = _load_market_lines(MARKET_LINES_PATH)
    spec = load_formula_spec(FORMULA_SPEC_PATH)
    games = build_games(EVAL_MIN_DATE, None)
    games = [g for g in games if EVAL_MIN_DATE <= g.game["event_start_utc"][:10] < EVAL_MAX_DATE]
    games = [g for g in games if str(g.game["event_id"]) in lines]
    print(f"eval games with market lines: {len(games)}")

    rows: list[dict] = []
    for item in games:
        game = item.game
        event_id = str(game["event_id"])
        line_info = lines[event_id]
        sim = simulate_game(item.features, item.estimate, spec, seed_namespace="joint_rebuild")
        away = np.asarray(sim.away_scores)
        home = np.asarray(sim.home_scores)
        totals = away + home

        # moneyline
        p_home_raw = float(np.mean(home > away))
        p_away_raw = float(np.mean(away > home))
        p_home = p_home_raw / (p_home_raw + p_away_raw) if (p_home_raw + p_away_raw) > 0 else 0.5
        ml_home_outcome = 1.0 if game["home_score"] > game["away_score"] else 0.0

        # runline: away covers if away_score + away_line > home_score
        away_line = float(line_info["spread"]["away_line"])
        p_away_cover = float(np.mean(away + away_line > home))
        actual_margin = game["away_score"] - game["home_score"]
        rl_outcome = (
            1.0 if actual_margin + away_line > 0 else (0.5 if actual_margin + away_line == 0 else 0.0)
        )

        # totals
        total_line = float(line_info["total"]["line"])
        p_over = float(np.mean(totals > total_line))
        actual_total = game["home_score"] + game["away_score"]
        tot_outcome = 1.0 if actual_total > total_line else (0.5 if actual_total == total_line else 0.0)

        # market no-vig references
        ml_market_home = _no_vig(
            _american_to_prob(line_info["moneyline"]["home"]),
            _american_to_prob(line_info["moneyline"]["away"]),
        )
        rl_market_away = _no_vig(
            _american_to_prob(line_info["spread"]["away"]), _american_to_prob(line_info["spread"]["home"])
        )
        tot_market_over = _no_vig(
            _american_to_prob(line_info["total"]["over"]), _american_to_prob(line_info["total"]["under"])
        )

        rows.append(
            {
                "date": game["event_start_utc"][:10],
                "model": {"ml": p_home, "rl": p_away_cover, "tot": p_over},
                "market": {"ml": ml_market_home, "rl": rl_market_away, "tot": tot_market_over},
                "outcome": {"ml": ml_home_outcome, "rl": rl_outcome, "tot": tot_outcome},
            }
        )

    dates = sorted({r["date"] for r in rows})
    train_count = max(1, int(len(dates) * 0.60))
    val_count = max(1, int(len(dates) * 0.20))
    holdout_start = dates[min(train_count + val_count, len(dates) - 1)]
    val_start = dates[train_count]
    validation = [r for r in rows if val_start <= r["date"] < holdout_start]
    holdout = [r for r in rows if r["date"] >= holdout_start]
    print(f"validation={len(validation)} holdout={len(holdout)} (holdout starts {holdout_start})")

    # reproduction gate: 2026-07-only totals slice from the handoff (0.238 vs 0.242)
    july = [r for r in rows if r["date"] < "2026-08-01" and r["market"]["tot"] is not None]
    if july:
        july_model_brier = _exact_line_brier(
            [r["model"]["tot"] for r in july], [r["outcome"]["tot"] for r in july]
        )
        july_market_brier = _exact_line_brier(
            [r["market"]["tot"] for r in july], [r["outcome"]["tot"] for r in july]
        )
        print(
            f"reproduction gate (2026-07 totals, n={len(july)}): model {july_model_brier:.4f} vs "
            f"market {july_market_brier:.4f} (handoff documented 0.238 vs 0.242 on n=171)"
        )

    # stacking weight per market, fit on validation only
    def _stack_fit(market: str) -> float | None:
        usable = [r for r in validation if r["market"][market] is not None]
        if not usable:
            return None
        outcomes = [r["outcome"][market] for r in usable]
        model_probs = [r["model"][market] for r in usable]
        market_probs = [r["market"][market] for r in usable]
        best_w, best_loss = 1.0, float("inf")
        for w in STACK_GRID:
            stacked = [w * m + (1 - w) * mk for m, mk in zip(model_probs, market_probs, strict=True)]
            loss = _log_score(stacked, outcomes)
            if loss < best_loss:
                best_w, best_loss = w, loss
        return best_w

    results = {}
    for market in ("ml", "rl", "tot"):
        usable = [r for r in holdout if r["market"][market] is not None]
        model_brier = _exact_line_brier(
            [r["model"][market] for r in usable], [r["outcome"][market] for r in usable]
        )
        market_brier = _exact_line_brier(
            [r["market"][market] for r in usable], [r["outcome"][market] for r in usable]
        )
        model_ls = _log_score([r["model"][market] for r in usable], [r["outcome"][market] for r in usable])
        market_ls = _log_score([r["market"][market] for r in usable], [r["outcome"][market] for r in usable])
        w = _stack_fit(market)
        stacked_brier = None
        if w is not None:
            stacked = [w * r["model"][market] + (1 - w) * r["market"][market] for r in usable]
            stacked_brier = _exact_line_brier(stacked, [r["outcome"][market] for r in usable])
        results[market] = {
            "n": len(usable),
            "model_brier": round(model_brier, 6),
            "market_brier": round(market_brier, 6),
            "model_log_score": round(model_ls, 6),
            "market_log_score": round(market_ls, 6),
            "brier_delta_vs_market": round(model_brier - market_brier, 6),
            "stack_weight_w": w,
            "stacked_brier": round(stacked_brier, 6) if stacked_brier is not None else None,
        }

    # date-cluster bootstrap: P(model better than market) per market on holdout
    bootstrap = {}
    for market in ("ml", "rl", "tot"):
        by_date: dict[str, list[float]] = defaultdict(list)
        for r in holdout:
            if r["market"][market] is None:
                continue
            by_date[r["date"]].append(
                (r["model"][market] - r["outcome"][market]) ** 2
                - (r["market"][market] - r["outcome"][market]) ** 2
            )
        days = sorted(by_date)
        if len(days) < 2:
            bootstrap[market] = None
            continue
        rng = random.Random(20260818)
        samples = []
        for _ in range(2000):
            sampled = [rng.choice(days) for _ in days]
            samples.append(mean(v for d in sampled for v in by_date[d]))
        samples.sort()
        bootstrap[market] = {
            "p_better": round(sum(1 for s in samples if s < 0) / 2000, 4),
            "ci_2_5": round(samples[49], 6),
            "ci_97_5": round(samples[1949], 6),
            "n_dates": len(days),
        }

    report = {
        "n_eval_games": len(rows),
        "window": [EVAL_MIN_DATE, EVAL_MAX_DATE],
        "holdout": results,
        "holdout_bootstrap_model_vs_market": bootstrap,
        "note": (
            "One joint gamma_poisson distribution prices all three markets. "
            "Stacking weight w is per-market, fitted on the validation split "
            "only; a w well below 1.0 means the market carries information "
            "the model does not (the same signal the market-as-prior "
            "shrinkage backtest found on ledger picks)."
        ),
    }
    out_path = Path("outputs/research/mlb_joint_rebuild/joint_market_eval.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
