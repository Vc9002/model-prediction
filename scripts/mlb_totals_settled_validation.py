"""Settled-picks validation for the promoted MLB totals model.

measured-edge-totals-v3 was promoted to production 2026-08-18 (66e6163,
operator directive). This tests the model's ACTUAL live record: every
settled MLB total pick it produced (flat + main tiers), recorded
model_probability vs real settled outcomes, with the market_probability
recorded at decision time as the reference. Read-only against the
runtime-root SQLite ledger.

Reports exact-line Brier / log-loss / direction-correct per tier + pooled,
a by-month breakdown on the flat tier, and a date-cluster bootstrap
P(model better than market) where the date count supports it. Pushes:
ledger settled rows are win/loss only, so outcomes are binary here (the
2.5-goal line the model prices pushes on a whole number -- lines at 10/9.5
etc. are mostly push-free in practice; any push rows would not be in
result IN (win, loss)).
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


def _fetch_settled(db_path: Path, tier: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT event_start_utc, selection, line, model_probability, market_probability, result
               FROM ledger_records
               WHERE sport = 'mlb' AND market_type = 'total' AND ledger_tier = ?
                 AND status = 'settled' AND result IN ('win', 'loss')
                 AND model_id = 'measured-edge-totals-v3'
                 AND model_probability IS NOT NULL AND market_probability IS NOT NULL
               ORDER BY event_start_utc""",
            (tier,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _date_cluster_bootstrap(deltas_by_date: dict[str, list[float]], seed: int) -> dict:
    dates = sorted(deltas_by_date)
    observed = mean(v for d in dates for v in deltas_by_date[d])
    rng = random.Random(seed)
    samples = []
    for _ in range(2000):
        sampled = [rng.choice(dates) for _ in dates]
        samples.append(mean(v for d in sampled for v in deltas_by_date[d]))
    samples.sort()
    return {
        "observed_mean_brier_delta": round(observed, 6),
        "p_better": round(sum(1 for s in samples if s < 0) / 2000, 4),
        "ci_2_5": round(samples[49], 6),
        "ci_97_5": round(samples[1949], 6),
        "n_dates": len(dates),
    }


def _evaluate(rows: list[dict], label: str, seed: int = 20260818) -> dict:
    if not rows:
        return {"label": label, "n": 0}
    model_probs = [float(r["model_probability"]) for r in rows]
    market_probs = [float(r["market_probability"]) for r in rows]
    outcomes = [1 if r["result"] == "win" else 0 for r in rows]
    dates = [str(r["event_start_utc"])[:10] for r in rows]

    model_metrics = _safe_metrics(model_probs, outcomes)
    market_metrics = _safe_metrics(market_probs, outcomes)
    model_hits = sum(1 for p, y in zip(model_probs, outcomes, strict=True) if (p > 0.5) == (y == 1))
    market_hits = sum(1 for p, y in zip(market_probs, outcomes, strict=True) if (p > 0.5) == (y == 1))

    deltas_by_date: dict[str, list[float]] = defaultdict(list)
    for p_m, p_mk, y, d in zip(model_probs, market_probs, outcomes, dates, strict=True):
        deltas_by_date[d].append((p_m - y) ** 2 - (p_mk - y) ** 2)

    monthly: dict[str, dict] = defaultdict(lambda: {"n": 0, "hits": 0})
    for p, y, d in zip(model_probs, outcomes, dates, strict=True):
        month = d[:7]
        monthly[month]["n"] += 1
        monthly[month]["hits"] += 1 if (p > 0.5) == (y == 1) else 0

    return {
        "label": label,
        "n": len(rows),
        "date_range": [dates[0], dates[-1]],
        "model": {**model_metrics, "direction_correct": model_hits},
        "market_reference": {**market_metrics, "direction_correct": market_hits},
        "model_vs_market_brier_delta": round(model_metrics["brier"] - market_metrics["brier"], 6),
        "model_vs_market_log_loss_delta": round(model_metrics["log_loss"] - market_metrics["log_loss"], 6),
        "cluster_bootstrap": _date_cluster_bootstrap(deltas_by_date, seed),
        "monthly_model_hit_rates": {
            m: {"n": v["n"], "hit_rate": round(v["hits"] / v["n"], 4)} for m, v in sorted(monthly.items())
        },
    }


def main() -> int:
    flat = _fetch_settled(DEFAULT_LEDGER_DB, "flat")
    main = _fetch_settled(DEFAULT_LEDGER_DB, "main")
    pooled = flat + main

    report = {
        "model_id": "measured-edge-totals-v3",
        "source": "runtime-root SQLite ledger (read-only), settled win/loss rows",
        "flat": _evaluate(flat, "flat"),
        "main": _evaluate(main, "main"),
        "pooled": _evaluate(pooled, "pooled", seed=20260819),
    }
    out_path = PROJECT_ROOT / "outputs/research/mlb_totals_settled/settled_validation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
