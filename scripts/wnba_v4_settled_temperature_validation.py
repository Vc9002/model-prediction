"""WNBA v4 + T=0.8 temperature calibration -- settled-picks validation.

Step 2 of the post-burn-in promotion chain for `wnba-v4-temperature-
calibration-T0.8` (docs/HANDOFF_2026-08-17.md): confirm the T=0.8 win found
on v4's own 163-game training holdout also holds on REAL settled ledger
outcomes, before freezing an artifact or starting a prospective shadow run.
This is a retrospective check on already-settled picks -- it is NOT the
prospective N>=30 shadow required before any promotion decision; that is a
separate, calendar-bound step that starts only after this one is read.

Reads the runtime-root SQLite ledger read-only (never mutates). Reports
identity vs T=0.8 Brier/LogLoss/ECE plus a date-cluster bootstrap
P(T=0.8 better), matching the methodology already used across the v9
research branch (roadmap_challenger._cluster_bootstrap_brier_delta).
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction.calibration import TemperatureCalibrator
from model_prediction.config import PROJECT_ROOT
from scripts.mlb_v9_calibration_xgb import _safe_metrics

DEFAULT_LEDGER_DB = Path("/Users/vincentc9002/model-prediction-runtime/ledgers/ledgers.db")
MODEL_ID = "wnba-elo-trend-lr-v4"
TEMPERATURE = 0.8


def _fetch_settled(db_path: Path, tier: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT pick_id, event_id, event_start_utc, model_probability, result
               FROM ledger_records
               WHERE sport = 'wnba' AND market_type = 'moneyline' AND status = 'settled'
                 AND model_id = ? AND ledger_tier = ?
                 AND result IN ('win', 'loss')
               ORDER BY event_start_utc""",
            (MODEL_ID, tier),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _date_cluster_bootstrap(
    deltas_by_date: dict[str, list[float]], seed: int, n_resamples: int = 2000
) -> dict:
    dates = sorted(deltas_by_date)
    observed = mean(v for d in dates for v in deltas_by_date[d])
    rng = random.Random(seed)
    samples = []
    for _ in range(n_resamples):
        sampled_days = [rng.choice(dates) for _ in dates]
        samples.append(mean(v for d in sampled_days for v in deltas_by_date[d]))
    samples.sort()
    p_better = sum(1 for s in samples if s < 0) / n_resamples
    return {
        "observed_mean_brier_delta": round(observed, 6),
        "p_better": round(p_better, 4),
        "ci_2_5": round(samples[int(0.025 * n_resamples)], 6),
        "ci_97_5": round(samples[int(0.975 * n_resamples) - 1], 6),
        "n_dates": len(dates),
        "n_resamples": n_resamples,
    }


def _run(tier: str, db_path: Path, seed: int) -> dict:
    rows = _fetch_settled(db_path, tier)
    if not rows:
        return {"tier": tier, "n": 0}

    identity_probs = [float(r["model_probability"]) for r in rows]
    outcomes = [1 if r["result"] == "win" else 0 for r in rows]
    dates = [str(r["event_start_utc"])[:10] for r in rows]

    calibrator = TemperatureCalibrator(TEMPERATURE, metadata=None)
    calibrated_probs = [calibrator.transform(p) for p in identity_probs]

    identity_metrics = _safe_metrics(identity_probs, outcomes)
    calibrated_metrics = _safe_metrics(calibrated_probs, outcomes)

    deltas_by_date: dict[str, list[float]] = defaultdict(list)
    for p_id, p_cal, y, d in zip(identity_probs, calibrated_probs, outcomes, dates, strict=True):
        deltas_by_date[d].append((p_cal - y) ** 2 - (p_id - y) ** 2)
    bootstrap = _date_cluster_bootstrap(deltas_by_date, seed=seed)

    identity_hits = sum(1 for p, y in zip(identity_probs, outcomes, strict=True) if (p > 0.5) == (y == 1))
    calibrated_hits = sum(1 for p, y in zip(calibrated_probs, outcomes, strict=True) if (p > 0.5) == (y == 1))

    return {
        "tier": tier,
        "n": len(rows),
        "date_range": [dates[0], dates[-1]],
        "identity": {**identity_metrics, "direction_correct": identity_hits},
        "temperature_0_8": {**calibrated_metrics, "direction_correct": calibrated_hits},
        "brier_delta": round(calibrated_metrics["brier"] - identity_metrics["brier"], 6),
        "log_loss_delta": round(calibrated_metrics["log_loss"] - identity_metrics["log_loss"], 6),
        "ece_delta": round(calibrated_metrics["ece"] - identity_metrics["ece"], 6),
        "cluster_bootstrap": bootstrap,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-db", default=str(DEFAULT_LEDGER_DB))
    parser.add_argument("--seed", type=int, default=20260818)
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "outputs/research/wnba_v5/settled_temperature_validation.json"),
    )
    args = parser.parse_args()

    db_path = Path(args.ledger_db)
    result = {
        "model_id": MODEL_ID,
        "temperature": TEMPERATURE,
        "source": "runtime-root SQLite ledger (read-only)",
        "note": (
            "Retrospective validation on already-settled picks. NOT the "
            "prospective N>=30 shadow required before promotion."
        ),
        "flat": _run("flat", db_path, args.seed),
        "main": _run("main", db_path, args.seed),
    }

    print(json.dumps(result, indent=2))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
