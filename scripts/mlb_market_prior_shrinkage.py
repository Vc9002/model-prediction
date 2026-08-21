"""MLB/tennis market-as-prior shrinkage (ops brainstorm, 2026-08-17): blend the
model's own probability with the market-implied probability recorded at
decision time, weight learned out-of-fold on real settled ledger picks.

p_blend = lambda * p_model + (1 - lambda) * p_market

lambda is grid-searched on a validation split (minimizing log-loss) and
evaluated once on a locked holdout with a date-cluster bootstrap, matching
the WNBA/NFL temperature-calibration methodology exactly -- this is really
the same "post-hoc probability adjustment" family, just blended against the
market instead of self-calibrated. Reads the runtime-root SQLite ledger
read-only. --sport/--market-type/--tier make it generic across sports.

Cross-sport results (2026-08-18): MLB is underpowered to say anything
(n=149 total, 24/32 validation/holdout after the date split -- lambda=1.0
just tied). Tennis (n=376) and soccer (n=191) both show a LARGE Brier/
log-loss win from leaning heavily on the market (tennis lambda=0.1, holdout
delta -0.0186, P(better)=0.9985; soccer lambda=0.45, delta -0.0244,
P(better)=0.9955) -- but in both cases ECE and calibration_slope got WORSE
even as Brier/log-loss improved, and the holdout bootstrap only has 5
distinct dates. That combination (Brier better, slope/ECE worse, tiny
n_dates) is exactly the shape you'd see from resolution/sharpness gains
dominating a small, noisy sample rather than a robust calibration
improvement -- plausible given tennis/soccer are liquid, well-covered
markets, but not something to act on without a larger-sample recheck as
more settled picks accumulate. CS2 (n=268 research tier) shows no real
signal (P(better)=0.552, a coin flip) and starts from an already-broken
baseline calibration slope (-0.097), a separate problem worth its own
look. Nothing here was wired into serving -- soccer/tennis are code-backed
production models (not JSON LearnedMarketArtifact like WNBA/NFL), so even
a promotable finding would need real wiring design, not just a frozen
artifact file.
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.config import PROJECT_ROOT
from scripts.mlb_v9_calibration_xgb import _safe_metrics

DEFAULT_LEDGER_DB = Path("/Users/vincentc9002/model-prediction-runtime/ledgers/ledgers.db")
LAMBDA_GRID = tuple(round(x * 0.05, 2) for x in range(21))  # 0.0 .. 1.0 step 0.05


def _fetch_settled(db_path: Path, sport: str, market_type: str, tier: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT pick_id, event_start_utc, model_probability, market_probability, result
               FROM ledger_records
               WHERE sport = ? AND market_type = ? AND ledger_tier = ?
                 AND status = 'settled' AND result IN ('win', 'loss')
                 AND model_probability IS NOT NULL AND market_probability IS NOT NULL
               ORDER BY event_start_utc""",
            (sport, market_type, tier),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _log_loss(probs: list[float], outcomes: list[int]) -> float:
    import math

    clipped = [min(1 - 1e-6, max(1e-6, p)) for p in probs]
    return -sum(
        y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(clipped, outcomes, strict=True)
    ) / len(clipped)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", default="mlb")
    parser.add_argument("--market-type", default="moneyline")
    parser.add_argument("--tier", default="flat")
    args = parser.parse_args()

    rows = _fetch_settled(DEFAULT_LEDGER_DB, args.sport, args.market_type, args.tier)
    if len(rows) < 30:
        print(f"too few settled rows with both probabilities ({len(rows)}) -- not enough to fit shrinkage")
        return 0

    dates = [r["event_start_utc"][:10] for r in rows]
    distinct_dates = sorted(set(dates))
    train_count = max(1, int(len(distinct_dates) * 0.60))
    val_count = max(1, int(len(distinct_dates) * 0.20))
    holdout_start_idx = min(train_count + val_count, len(distinct_dates) - 1)
    val_start = distinct_dates[train_count]
    holdout_start = distinct_dates[holdout_start_idx]

    def _split(lo: str | None, hi: str | None) -> list[dict]:
        out = []
        for r, d in zip(rows, dates, strict=True):
            if lo is not None and d < lo:
                continue
            if hi is not None and d >= hi:
                continue
            out.append(r)
        return out

    validation = _split(val_start, holdout_start)
    holdout = _split(holdout_start, None)
    print(f"total={len(rows)} validation={len(validation)} holdout={len(holdout)}")

    val_model = [r["model_probability"] for r in validation]
    val_market = [r["market_probability"] for r in validation]
    val_outcomes = [1 if r["result"] == "win" else 0 for r in validation]

    lambda_results = {}
    for lam in LAMBDA_GRID:
        blended = [lam * m + (1 - lam) * mk for m, mk in zip(val_model, val_market, strict=True)]
        lambda_results[lam] = round(_log_loss(blended, val_outcomes), 6)
    best_lambda = min(lambda_results, key=lambda_results.get)
    print(f"selected lambda={best_lambda} (lowest validation log-loss); grid={lambda_results}")

    hold_model = [r["model_probability"] for r in holdout]
    hold_market = [r["market_probability"] for r in holdout]
    hold_outcomes = [1 if r["result"] == "win" else 0 for r in holdout]
    hold_dates = [r["event_start_utc"][:10] for r in holdout]
    hold_blended = [
        best_lambda * m + (1 - best_lambda) * mk for m, mk in zip(hold_model, hold_market, strict=True)
    ]

    model_metrics = _safe_metrics(hold_model, hold_outcomes)
    blended_metrics = _safe_metrics(hold_blended, hold_outcomes)

    by_date: dict[str, list[float]] = defaultdict(list)
    for m, b, y, d in zip(hold_model, hold_blended, hold_outcomes, hold_dates, strict=True):
        by_date[d].append((b - y) ** 2 - (m - y) ** 2)
    dates_sorted = sorted(by_date)
    rng = random.Random(20260818)
    samples = []
    for _ in range(2000):
        sampled = [rng.choice(dates_sorted) for _ in dates_sorted]
        samples.append(mean(v for d in sampled for v in by_date[d]))
    samples.sort()
    p_better = sum(1 for s in samples if s < 0) / 2000 if samples else None

    report = {
        "sport": args.sport,
        "market_type": args.market_type,
        "tier": args.tier,
        "n_total": len(rows),
        "n_validation": len(validation),
        "n_holdout": len(holdout),
        "lambda_grid_validation_log_loss": lambda_results,
        "selected_lambda": best_lambda,
        "holdout": {
            "model_only": model_metrics,
            "blended": blended_metrics,
            "brier_delta": round(blended_metrics["brier"] - model_metrics["brier"], 6),
            "log_loss_delta": round(blended_metrics["log_loss"] - model_metrics["log_loss"], 6),
        },
        "cluster_bootstrap": {
            "observed_mean_brier_delta": round(mean(v for d in dates_sorted for v in by_date[d]), 6)
            if dates_sorted
            else None,
            "p_better": round(p_better, 4) if p_better is not None else None,
            "ci_2_5": round(samples[49], 6) if samples else None,
            "ci_97_5": round(samples[1949], 6) if samples else None,
            "n_dates": len(dates_sorted),
        },
        "verdict": (
            "promote_candidate"
            if round(blended_metrics["brier"] - model_metrics["brier"], 6) <= -0.002
            else "reject_below_magnitude_bar_or_worse"
        ),
        "note": (
            "lambda=1.0 winning on the validation grid would mean the market "
            "adds nothing over the model alone -- reported either way, not "
            "assumed."
        ),
    }
    out_dir = PROJECT_ROOT / "outputs/research/market_shrinkage"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.sport}_{args.market_type}_{args.tier}_shrinkage_backtest.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
