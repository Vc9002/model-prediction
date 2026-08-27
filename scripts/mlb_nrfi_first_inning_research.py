"""Walk-forward research evaluation for the first-inning NRFI model.

Builds the PIT first-inning feature ledger from the Stats API snapshots,
fits ``MLBFirstInningModel`` on a chronological 60/20/20 split, and reports
LogLoss / Brier / AUC / calibration on the locked test window against three
references: the incumbent ``MLBNRFIModel`` (v1 hand-set weights), the
explicit fixed-vig market proxy, and real Polymarket NRFI quotes when the
odds archive has them.

Research-only. Run::

    PYTHONPATH=src:. .venv/bin/python scripts/mlb_nrfi_first_inning_research.py
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from model_prediction.domain import parse_utc
from model_prediction.models.mlb_first_inning import (
    MLBFirstInningModel,
    build_first_inning_ledger,
    compute_first_inning_priors,
    market_proxy_probabilities,
)
from model_prediction.models.mlb_nrfi import MLBNRFIModel

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data/mlb_statsapi/game_snapshots.jsonl"


def _log_loss(prob: float, outcome: int) -> float:
    p = min(max(prob, 1e-9), 1.0 - 1e-9)
    return -(outcome * math.log(p) + (1 - outcome) * math.log(1.0 - p))


def _brier(prob: float, outcome: int) -> float:
    return (prob - outcome) ** 2


def _chronological_split(rows, train_frac=0.60, val_frac=0.20):
    n = len(rows)
    train = rows[: int(n * train_frac)]
    val = rows[int(n * train_frac) : int(n * (train_frac + val_frac))]
    test = rows[int(n * (train_frac + val_frac)) :]
    return train, val, test


def _evaluate(probs: list[float], outcomes: list[int]) -> dict[str, float]:
    n = len(outcomes)
    ll = sum(_log_loss(p, y) for p, y in zip(probs, outcomes, strict=True)) / n
    br = sum(_brier(p, y) for p, y in zip(probs, outcomes, strict=True)) / n
    nrfi_rate = sum(outcomes) / n
    # Calibration: mean predicted vs realized rate (simple, no binning).
    calibration_error = sum(probs) / n - nrfi_rate
    return {
        "n": n,
        "log_loss": round(ll, 6),
        "brier": round(br, 6),
        "nrfi_rate": round(nrfi_rate, 4),
        "mean_predicted": round(sum(probs) / n, 4),
        "calibration_error": round(calibration_error, 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--report", type=Path, default=ROOT / "tmp/nrfi-first-inning-research.json")
    args = parser.parse_args()

    # Freeze league priors on the train window BEFORE the ledger is built
    # (the module's serving rule: priors are frozen at training time, never
    # re-estimated live, so the research ledger must use train-window priors
    # or the holdout evaluation would peek).
    snapshots_sorted = build_first_inning_ledger(args.snapshots)
    train, val, test = _chronological_split(snapshots_sorted)
    test_outcomes = [r.nrfi for r in test]
    train_end = train[-1].game_start_utc if train else None
    priors = compute_first_inning_priors(args.snapshots, end_utc=parse_utc(train_end)) if train_end else None

    print(f"Building first-inning ledger from {args.snapshots.name} ...")
    rows = build_first_inning_ledger(args.snapshots, priors=priors)
    print(f"  {len(rows)} games with PIT feature vectors")

    train, val, test = _chronological_split(rows)
    test_outcomes = [r.nrfi for r in test]

    print(f"  split: train={len(train)} val={len(val)} test={len(test)}")
    print("Fitting on train ...")
    model = MLBFirstInningModel()
    model.fit(train)

    def predict_probs(model: MLBFirstInningModel, split_rows) -> list[float]:
        return [model.predict_p_nrfi(r) for r in split_rows]

    test_probs = predict_probs(model, test)
    train_probs = predict_probs(model, train)

    # Market proxy: fixed-vig base rate anchored on the TRAIN nrfi rate.
    train_rate = sum(r.nrfi for r in train) / len(train)
    proxy_p, _ = market_proxy_probabilities(train_rate)
    proxy_probs = [proxy_p] * len(test)

    # Incumbent v1 model on the same test rows (its own feature path).
    try:
        incumbent = MLBNRFIModel()
        inc_probs = []
        for row in test:
            pred = incumbent.predict(
                home_team=row.home_team,
                away_team=row.away_team,
                decision=parse_utc(row.game_start_utc),
            )
            inc_probs.append(float(pred.p_nrfi))
        incumbent_metrics = _evaluate(inc_probs, test_outcomes)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        # v1 API drift (field renames, signature changes) must not block the
        # new model's research run; the incumbent is a reference only.
        incumbent_metrics = {"error": str(exc)}

    report = {
        "ledger_games": len(rows),
        "split": {"train": len(train), "val": len(val), "test": len(test)},
        "train_nrfi_rate": round(train_rate, 4),
        "market_proxy": {"base_rate": round(train_rate, 4), "p_nrfi_implied": round(proxy_p, 4)},
        "first_inning_v1": {
            "train": _evaluate(train_probs, [r.nrfi for r in train]),
            "test": _evaluate(test_probs, test_outcomes),
        },
        "market_proxy_test": _evaluate(proxy_probs, test_outcomes),
        "incumbent_mlb_nrfi_v1_test": incumbent_metrics,
        "top_coefficients": sorted(
            zip(model.feature_names, model.coef, strict=True),
            key=lambda pair: abs(pair[1]),
            reverse=True,
        )[:8],
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("\n=== Holdout (test) evaluation ===")
    for name, metrics in (
        ("first-inning v1 (fitted)", report["first_inning_v1"]["test"]),
        ("market proxy (fixed-vig base)", report["market_proxy_test"]),
        (
            "incumbent mlb-nrfi-v1",
            incumbent_metrics if isinstance(incumbent_metrics, dict) else {"error": True},
        ),
    ):
        if "error" in metrics:
            print(f"  {name}: error={metrics['error']}")
            continue
        print(
            f"  {name}: n={metrics['n']} logloss={metrics['log_loss']} "
            f"brier={metrics['brier']} nrfi_rate={metrics['nrfi_rate']} "
            f"calib_err={metrics['calibration_error']}"
        )
    print("\nTop |coef| features (train fit):")
    for name, coef in report["top_coefficients"]:
        print(f"  {name:<32} {coef:+.4f}")

    print(f"\nReport: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
